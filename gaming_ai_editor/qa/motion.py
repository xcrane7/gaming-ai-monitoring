"""9. Quality Assurance Engine — contrôles propres au montage animé.

Complète qa/checks.py (fichier, format, durée, noir, silence, volume, originaux)
par des mesures sur le résultat réellement rendu :

    synchro des impacts   les sons d'impact tombent-ils à l'instant prévu ? (ms)
    scintillement         sauts de luminosité brutaux non expliqués par un flash,
                          une transition ou une coupe prévus
    fluidité              part d'images dupliquées hors ralentis (saccades)
    transitions           nombre de transitions rendues = nombre prévu
    couche graphique      la couche d'animations a bien été produite
    densité d'effets      ni montage plat, ni effets en continu
    qualité d'encodage    débit par pixel dans une plage raisonnable

Le score sur 100 sert aussi au Skills Researcher pour comparer deux versions
d'un réglage. Il mesure la qualité technique, pas le goût artistique.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..editor.plan import EditPlan
from ..ffutil import _popen_kwargs, ffmpeg_bin, probe

PASS, WARN, FAIL = "pass", "warn", "fail"


def _check(name, status, detail, value=None):
    return {"name": name, "status": status, "detail": detail, "value": value}


def _gray_frames(path: Path, width: int = 96, height: int = 170):
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-map", "0:v:0", "-vf", f"scale={width}:{height}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            **_popen_kwargs())
    size = width * height
    luma, diff = [], []
    prev = None
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, dtype=np.uint8).astype(np.float32) / 255.0
        luma.append(float(f.mean()))
        diff.append(float(np.abs(f - prev).mean()) if prev is not None else 1.0)
        prev = f
    proc.stdout.close()
    proc.wait()
    return np.asarray(luma), np.asarray(diff)


def _audio_energy(path: Path, rate: int = 16000, hop: float = 0.005) -> np.ndarray:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=900, **_popen_kwargs()).stdout
    y = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    n = int(rate * hop)
    if y.size < n:
        return np.zeros(1)
    frames = y[: y.size // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-5)


def source_duplicates(plan: EditPlan, fps: float, sample: float = 3.0) -> float | None:
    """Part d'images répétées dans la source, mesurée sur quelques secondes de chaque clip."""
    dups = total = 0
    for seg in plan.segments[:6]:
        if not Path(seg.source).exists():
            continue
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error",
               "-ss", f"{seg.start:.3f}", "-t", f"{min(sample, seg.duration):.3f}",
               "-i", seg.source, "-map", "0:v:0",
               "-vf", f"fps={fps:g},scale=96:54:flags=area,format=gray", "-f", "rawvideo", "-"]
        raw = subprocess.run(cmd, capture_output=True, timeout=300, **_popen_kwargs()).stdout
        f = np.frombuffer(raw[: len(raw) // 5184 * 5184], dtype=np.uint8).reshape(-1, 5184)
        if len(f) < 2:
            continue
        d = np.abs(np.diff(f.astype(np.float32) / 255.0, axis=0)).mean(axis=1)
        dups += int((d < 0.0015).sum())
        total += len(d)
    return dups / total if total else None


def planned_times(plan: EditPlan) -> dict:
    """Instants (temps monté) des flashs, impacts sonores, jonctions et ralentis prévus."""
    offsets = plan.offsets()
    flashes, ramps = [], []
    for seg, off in zip(plan.segments, offsets):
        for t, _f in (seg.vfx or {}).get("flash", []):
            flashes.append(off + float(t))
        r = seg.speed_ramp()
        if r:
            ramps.append((off + r.start, off + r.map(r.end)))
    impacts = [float(c["t"]) for c in plan.sfx or [] if c["kind"] in ("impact", "boom")]
    return {"flashes": flashes, "impacts": impacts, "junctions": plan.junctions(),
            "ramps": ramps}


def sync_errors(energy: np.ndarray, times: list[float], hop: float = 0.005) -> list[float]:
    """Pour chaque impact prévu : écart (s) entre l'instant prévu et l'attaque mesurée."""
    errors = []
    radius = int(0.2 / hop)
    for t in times:
        c = int(t / hop)
        a, b = max(4, c - radius), min(energy.size - 1, c + radius)
        if b - a < 8:
            continue
        rise = energy[a:b] - np.array([energy[max(0, k - 4):k].mean() for k in range(a, b)])
        k = int(np.argmax(rise))
        if rise[k] < 6:  # aucune attaque nette : impact noyé dans le mix
            continue
        errors.append(abs((a + k) * hop - t))
    return errors


def run_motion_qa(path: str | Path, plan_dict: dict, render_info: dict | None = None) -> dict:
    path = Path(path)
    plan = EditPlan.from_dict(plan_dict)
    checks: list[dict] = []
    info = probe(path)
    dur = max(info.duration, 0.1)
    planned = planned_times(plan)
    score = 100.0

    # 1. synchronisation des impacts
    if planned["impacts"]:
        errs = sync_errors(_audio_energy(path), planned["impacts"])
        if errs:
            med = float(np.median(errs)) * 1000
            st = PASS if med <= 50 else WARN if med <= 120 else FAIL
            score -= 0 if st == PASS else 10 if st == WARN else 25
            checks.append(_check("synchro des impacts", st,
                                 f"écart médian {med:.0f} ms sur {len(errs)} impacts", med))
        else:
            checks.append(_check("synchro des impacts", WARN,
                                 "attaques non mesurables (mix trop chargé)", None))
            score -= 5

    luma, diff = _gray_frames(path)
    fps = info.fps or plan.fps
    # 2. scintillement : sauts de luminosité non prévus
    jumps = np.where(np.abs(np.diff(luma)) > 0.18)[0] + 1
    explained = planned["flashes"] + planned["junctions"]
    unexplained = [j / fps for j in jumps
                   if not any(abs(j / fps - x) <= 0.25 for x in explained)]
    per_min = len(unexplained) / (dur / 60)
    st = PASS if per_min <= 4 else WARN if per_min <= 10 else FAIL
    score -= 0 if st == PASS else 8 if st == WARN else 20
    checks.append(_check("scintillement", st,
                         f"{len(unexplained)} saut(s) de luminosité non prévu(s)", per_min))

    # 3. fluidité : images dupliquées hors ralentis
    in_ramp = np.zeros(diff.size, dtype=bool)
    for a, b in planned["ramps"]:
        in_ramp[int(a * fps):int(b * fps) + 1] = True
    dup = (diff < 0.0015) & ~in_ramp
    ratio = float(dup.sum()) / max(1, (~in_ramp).sum())
    source_ratio = source_duplicates(plan, fps)
    # on juge le montage, pas l'enregistrement : la source peut déjà répéter des images
    allowed = max(0.08, (source_ratio or 0.0) + 0.05)
    st = PASS if ratio <= allowed else WARN
    score -= 0 if st == PASS else 8
    extra = f" (source : {source_ratio:.0%})" if source_ratio is not None else ""
    checks.append(_check("fluidité", st, f"{ratio:.0%} d'images répétées hors ralentis{extra}",
                         ratio))

    # 4. transitions
    if plan.transition == "xfade" and len(plan.segments) > 1:
        expected = len(plan.segments) - 1
        got = len(plan.transitions) if plan.transitions else expected
        st = PASS if got == expected else WARN
        checks.append(_check("transitions", st, f"{got}/{expected} transitions animées", got))

    # 5. couche graphique
    if plan.graphics:
        g = (render_info or {}).get("graphics") or {}
        drawn = int(g.get("frames_with_graphics", 0))
        st = PASS if drawn > 0 else FAIL
        score -= 0 if st == PASS else 30
        checks.append(_check("couche graphique", st,
                             f"{drawn} images animées, {sum(g.get('elements', {}).values())} "
                             "éléments", drawn))

    # 6. densité d'effets
    events = sum(len(s.events) for s in plan.segments)
    if plan.style:
        per10 = events / dur * 10
        low = 0.6 if plan.format == "short" else 0.15  # le format long respire davantage
        st = PASS if low <= per10 <= 6 else WARN
        score -= 0 if st == PASS else 6
        word = "plat" if per10 < low else "surchargé" if per10 > 6 else "équilibré"
        checks.append(_check("densité d'effets", st,
                             f"{per10:.1f} moments animés / 10 s ({word})", per10))

    # 7. qualité d'encodage
    if info.bit_rate and info.width:
        bpp = info.bit_rate / (info.width * info.height * max(fps, 1))
        st = PASS if 0.02 <= bpp <= 1.2 else WARN
        score -= 0 if st == PASS else 5
        checks.append(_check("qualité d'encodage", st, f"{bpp:.3f} bit/pixel", bpp))

    return {"checks": checks, "score": round(max(0.0, score), 1)}
