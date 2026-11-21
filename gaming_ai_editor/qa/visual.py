"""Contrôle qualité visuel : juge les images réellement rendues, pas le plan.

- images floues ajoutées : images bien moins nettes que la netteté habituelle de la vidéo
  (traînées, mises au point, transitions), moins la part déjà floue dans les mêmes
  passages du jeu (flou de mouvement des rotations rapides : 30 % sur le gameplay FPS). La comparaison directe avec la source a été
  essayée puis abandonnée : le rendu ajoute des détails fins (particules, accentuation,
  compression) qui le font paraître « plus net » que le jeu même quand il est flouté ;
- images brûlées : part des images où plus de 35 % des pixels sont blancs (flash trop
  fort, surexposition) ;
- images ternes : part des images sans contraste (écart-type de luminance très faible).

Un échec déclenche le second essai « prudent » du pipeline (effets réduits).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..ffutil import _popen_kwargs, ffmpeg_bin, probe

PASS, WARN, FAIL = "pass", "warn", "fail"


def _sharpness(img: np.ndarray, blocks: int = 6) -> float:
    """Netteté robuste aux textes incrustés : variance du laplacien par blocs, médiane des
    blocs. Un mot d'impact ou une légende (toujours nets) n'occupe que quelques blocs et ne
    masque plus un jeu flou."""
    lap = (img[1:-1, :-2] + img[1:-1, 2:] + img[:-2, 1:-1] + img[2:, 1:-1]
           - 4 * img[1:-1, 1:-1])
    h, w = lap.shape
    bh, bw = max(1, h // blocks), max(1, w // blocks)
    values = [float(lap[y:y + bh, x:x + bw].var())
              for y in range(0, h - bh + 1, bh) for x in range(0, w - bw + 1, bw)]
    return float(np.median(values)) if values else 0.0


def exposure(render: str | Path, fps: float = 2.0) -> dict:
    """Part des images brûlées (blanches) et ternes (sans contraste)."""
    w, h = 180, 320
    info = probe(render)
    if info and info.width and info.height and info.width > info.height:
        w, h = 320, 180
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(render), "-vf",
           f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, **_popen_kwargs()).stdout
    frames = np.frombuffer(out, np.uint8)
    n = frames.size // (w * h)
    if n == 0:
        return {"frames": 0, "burnt": 0.0, "flat": 0.0, "blurred": 0.0, "blur_times": [],
                "burnt_times": []}
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    white = (frames >= 240).mean(axis=(1, 2)) > 0.35
    burnt = float(np.mean(white))
    burnt_times = [round(float(i) / fps, 1) for i in np.flatnonzero(white)]
    flat = float(np.mean(frames.std(axis=(1, 2)) < 8.0))
    # flou : image bien moins nette que la netteté habituelle de CETTE vidéo (traînées,
    # mise au point, transitions trop longues). Relatif à la vidéo : indépendant du jeu.
    sharp = np.array([_sharpness(f) for f in frames])
    ref = float(np.median(sharp))
    soft = sharp < 0.35 * ref if ref > 0 else np.zeros(n, bool)
    times = [round(float(i) / fps, 1) for i in np.flatnonzero(soft)]
    return {"frames": int(n), "burnt": round(burnt, 3), "flat": round(flat, 3),
            "blurred": round(float(soft.mean()), 3), "blur_times": times[:12],
            "burnt_times": burnt_times[:12]}


def source_blur(plan: dict, fps: float = 2.0) -> float | None:
    """Part d'images floues dans les mêmes passages de la source, sans aucun effet. Les jeux
    ont leur propre flou de mouvement (rotations rapides) : seul le flou AJOUTÉ par le
    montage est un défaut. None si les sources sont introuvables."""
    segs = plan.get("segments") or []
    vertical = int(plan.get("height", 0)) > int(plan.get("width", 1))
    w, h = (180, 320) if vertical else (320, 180)
    frames = []
    for seg in segs:
        src = Path(seg.get("source", ""))
        if not src.is_file():
            return None
        crop = "crop='min(iw,ih*9/16)':ih," if vertical else ""
        cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{seg['start']:.3f}", "-t",
               f"{max(0.1, seg['end'] - seg['start']):.3f}", "-i", str(src), "-vf",
               f"fps={fps},{crop}scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
        out = subprocess.run(cmd, capture_output=True, timeout=600, **_popen_kwargs()).stdout
        arr = np.frombuffer(out, np.uint8)
        n = arr.size // (w * h)
        frames += list(arr[: n * w * h].reshape(n, h, w).astype(np.float32))
    if not frames:
        return None
    sharp = np.array([_sharpness(f) for f in frames])
    ref = float(np.median(sharp))
    return round(float(np.mean(sharp < 0.35 * ref)), 3) if ref > 0 else 0.0


def hook_white_times(render: str | Path, seconds: float = 3.0, fps: float = 15.0) -> list[float]:
    """Images blanches pendant l'accroche, à 15 images/s : un fondu au blanc ne dure que
    quelques images et passerait entre deux échantillons à 2 images/s."""
    w, h = 72, 128
    cmd = [ffmpeg_bin(), "-v", "error", "-t", f"{seconds:g}", "-i", str(render), "-vf",
           f"fps={fps:g},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=300, **_popen_kwargs()).stdout
    n = len(out) // (w * h)
    if n == 0:
        return []
    frames = np.frombuffer(out, np.uint8)[: n * w * h].reshape(n, w * h)
    white = (frames >= 240).mean(axis=1) > 0.35
    return [round(float(i) / fps, 2) for i in np.flatnonzero(white)]


def run_visual_qa(render: str | Path, plan: dict, qa_cfg: dict) -> list[dict]:
    checks = []
    exp = exposure(render)
    if not exp["frames"]:
        return checks
    burnt_max = float(qa_cfg.get("max_burnt_ratio", 0.06))
    status = FAIL if exp["burnt"] > burnt_max else WARN if exp["burnt"] > burnt_max / 2 else PASS
    hook = hook_white_times(render)
    if hook:  # une image blanche pendant l'accroche suffit à faire décrocher
        status = FAIL
    checks.append({"name": "images brûlées (flash, surexposition)", "status": status,
                   "detail": f"{exp['burnt'] * 100:.1f} % des images (max "
                             f"{burnt_max * 100:.0f} %)"
                             + (f" ; pendant l'accroche à {', '.join(f'{t:g} s' for t in hook)}"
                                if hook else ""), "value": exp["burnt"]})
    blur_max = float(qa_cfg.get("max_blurred_ratio", 0.12))
    base = source_blur(plan) if plan.get("segments") else None
    added = exp["blurred"] - (base or 0.0)
    status = FAIL if added > blur_max else WARN if added > blur_max / 2 else PASS
    where = f", vers {', '.join(f'{t:g} s' for t in exp['blur_times'][:6])}" \
        if exp["blur_times"] and status != PASS else ""
    ref = (f" ; mêmes passages dans le jeu : {base * 100:.1f} % (flou de mouvement du jeu)"
           if base is not None else "")
    checks.append({"name": "images floues ajoutées (effets, traînées)", "status": status,
                   "detail": f"{exp['blurred'] * 100:.1f} % des images{ref} ; ajouté par le "
                             f"montage : {max(0.0, added) * 100:.1f} % (max "
                             f"{blur_max * 100:.0f} %){where}", "value": round(added, 3)})
    flat_max = float(qa_cfg.get("max_flat_ratio", 0.15))
    checks.append({"name": "images ternes (sans contraste)",
                   "status": WARN if exp["flat"] > flat_max else PASS,
                   "detail": f"{exp['flat'] * 100:.1f} % des images", "value": exp["flat"]})
    return checks
