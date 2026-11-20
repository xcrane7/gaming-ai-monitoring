"""Agent QA : contrôle objectif de chaque rendu avant de le déclarer valide."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from ..ffutil import FFmpegError, probe, run_capture

PASS, WARN, FAIL = "pass", "warn", "fail"
_CHUNK = 4 * 1024 * 1024


def fingerprint(path: str | Path) -> str:
    """Empreinte rapide d'un fichier (taille + date + début/milieu/fin).

    Sert à prouver que les originaux n'ont pas été modifiés par le traitement.
    """
    path = Path(path)
    st = path.stat()
    h = hashlib.sha256(f"{st.st_size}:{st.st_mtime_ns}".encode())
    with path.open("rb") as f:
        for pos in (0, max(0, st.st_size // 2 - _CHUNK // 2), max(0, st.st_size - _CHUNK)):
            f.seek(pos)
            h.update(f.read(_CHUNK))
    return h.hexdigest()


def _check(name: str, status: str, detail: str, value=None) -> dict:
    return {"name": name, "status": status, "detail": detail, "value": value}


def _sum_intervals(text: str, pattern: str) -> float:
    return sum(float(m) for m in re.findall(pattern, text))


def measure(path: str | Path) -> dict:
    """Une seule passe FFmpeg : erreurs de décodage, noir, gel, silence, loudness."""
    code, err = run_capture([
        "-v", "info", "-i", str(path),
        "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2",
        "-af", "silencedetect=n=-50dB:d=1,ebur128",
        "-f", "null", "-",
    ], timeout=3600)
    loud = None
    summary = err.rsplit("Summary:", 1)
    if len(summary) == 2:
        m = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", summary[1])
        if m:
            loud = float(m.group(1))
    decode_errors = [ln.strip() for ln in err.splitlines()
                     if re.search(r"(?i)error while|invalid data|corrupt|decode_slice|"
                                  r"missing reference|non monotonically", ln)]
    return {
        "returncode": code,
        "decode_errors": decode_errors[:10],
        "black_seconds": _sum_intervals(err, r"black_duration:\s*([\d.]+)"),
        "freeze_seconds": _sum_intervals(err, r"freeze_duration:\s*([\d.]+)"),
        "silence_seconds": _sum_intervals(err, r"silence_duration:\s*([\d.]+)"),
        "loudness_lufs": loud,
    }


def run_qa(path: str | Path, plan: dict, cfg, originals_intact: bool | None = None,
           render_info: dict | None = None) -> dict:
    """Retourne un rapport {status, checks[...]}. `plan` est EditPlan.to_dict()."""
    started = time.time()
    path = Path(path)
    checks: list[dict] = []
    qa = cfg["qa"]

    if not path.exists() or path.stat().st_size < 1024:
        checks.append(_check("fichier", FAIL, "Fichier absent ou vide."))
        return _report(path, checks, started)
    checks.append(_check("fichier", PASS, f"{path.stat().st_size / 1e6:.1f} Mo",
                         path.stat().st_size))
    try:
        info = probe(path)
    except FFmpegError as exc:
        checks.append(_check("lecture", FAIL, str(exc)))
        return _report(path, checks, started)

    checks.append(_check("flux", PASS if info.has_video and info.has_audio else FAIL,
                         f"vidéo={info.video_codec or 'absente'}, "
                         f"audio={info.audio_codec or 'absent'}"))
    ok = (info.width, info.height) == (plan["width"], plan["height"])
    checks.append(_check("résolution", PASS if ok else FAIL,
                         f"{info.width}x{info.height} (attendu {plan['width']}x{plan['height']})",
                         [info.width, info.height]))
    ok = abs(info.fps - plan["fps"]) < 0.5
    checks.append(_check("images/seconde", PASS if ok else FAIL,
                         f"{info.fps:.2f} (attendu {plan['fps']:g})", info.fps))

    expected = float(plan["duration"])
    tol = float(qa["duration_tolerance"])
    ok = abs(info.duration - expected) <= tol
    checks.append(_check("durée conforme au plan", PASS if ok else FAIL,
                         f"{info.duration:.2f}s (plan {expected:.2f}s)", info.duration))
    lo, hi = float(plan.get("min_seconds") or 0), float(plan.get("max_seconds") or 1e9)
    if lo - tol <= info.duration <= hi + tol:
        checks.append(_check("durée du format", PASS,
                             f"{info.duration:.1f}s dans [{lo:.0f}s, {hi:.0f}s]"))
    else:
        # Impossible à respecter quand la source elle-même est trop courte : avertissement.
        short_source = any("trop courte" in n for n in plan.get("notes", []))
        checks.append(_check("durée du format", WARN if short_source else FAIL,
                             f"{info.duration:.1f}s hors de [{lo:.0f}s, {hi:.0f}s]"
                             + (" — source trop courte" if short_source else "")))
    if info.video_duration and info.audio_duration:
        drift = abs(info.video_duration - info.audio_duration)
        checks.append(_check("synchro audio/vidéo", PASS if drift <= 0.15 else FAIL,
                             f"écart de durée {drift * 1000:.0f} ms", drift))

    m = measure(path)
    bad = m["returncode"] != 0 or m["decode_errors"]
    checks.append(_check("intégrité du décodage", FAIL if bad else PASS,
                         "; ".join(m["decode_errors"]) or "Aucune erreur de décodage."))
    dur = max(info.duration, 0.001)
    ratio = m["black_seconds"] / dur
    checks.append(_check("images noires", PASS if ratio <= float(qa["max_black_ratio"]) else FAIL,
                         f"{m['black_seconds']:.1f}s ({ratio:.0%})", ratio))
    ratio = m["freeze_seconds"] / dur
    checks.append(_check("image figée", PASS if ratio <= 0.5 else WARN,
                         f"{m['freeze_seconds']:.1f}s ({ratio:.0%})", ratio))
    ratio = m["silence_seconds"] / dur
    checks.append(_check("silence", PASS if ratio <= float(qa["max_silence_ratio"]) else WARN,
                         f"{m['silence_seconds']:.1f}s ({ratio:.0%})", ratio))
    target = float(cfg["render"]["loudness_lufs"])
    if m["loudness_lufs"] is None or m["loudness_lufs"] < -60:
        checks.append(_check("volume (LUFS)", WARN, "Piste quasi silencieuse ou non mesurable.",
                             m["loudness_lufs"]))
    else:
        ok = abs(m["loudness_lufs"] - target) <= float(qa["loudness_tolerance"])
        checks.append(_check("volume (LUFS)", PASS if ok else WARN,
                             f"{m['loudness_lufs']:.1f} LUFS (cible {target:g})",
                             m["loudness_lufs"]))
    motion_score = None
    if plan.get("style") and (plan.get("graphics") or plan.get("sfx")):
        from .motion import run_motion_qa

        try:
            motion = run_motion_qa(path, plan, render_info)
            checks.extend(motion["checks"])
            motion_score = motion["score"]
        except Exception as exc:  # une mesure qui plante ne doit pas masquer le rendu
            checks.append(_check("contrôle du montage animé", WARN, f"mesure impossible : {exc}"))
    if qa.get("visual", True) and plan.get("segments"):
        from .visual import run_visual_qa

        try:
            checks.extend(run_visual_qa(path, plan, qa))
        except Exception as exc:
            checks.append(_check("contrôle visuel", WARN, f"mesure impossible : {exc}"))
    if originals_intact is not None:
        checks.append(_check("originaux intacts", PASS if originals_intact else FAIL,
                             "Empreintes identiques avant/après." if originals_intact
                             else "UNE SOURCE A ÉTÉ MODIFIÉE PENDANT LE TRAITEMENT."))
    report = _report(path, checks, started)
    if motion_score is not None:
        report["motion_score"] = motion_score
    return report


def _report(path: Path, checks: list[dict], started: float) -> dict:
    statuses = {c["status"] for c in checks}
    status = FAIL if FAIL in statuses else WARN if WARN in statuses else PASS
    return {"path": str(path), "status": status, "checks": checks,
            "qa_seconds": round(time.time() - started, 2), "at": time.time()}
