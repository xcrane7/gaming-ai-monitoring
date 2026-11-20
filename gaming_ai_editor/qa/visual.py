"""Contrôle qualité visuel : juge les images réellement rendues, pas le plan.

- images floues : images bien moins nettes que la netteté habituelle de la vidéo
  (traînées, mises au point, transitions). La comparaison directe avec la source a été
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
        return {"frames": 0, "burnt": 0.0, "flat": 0.0, "blurred": 0.0, "blur_times": []}
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    burnt = float(np.mean((frames >= 240).mean(axis=(1, 2)) > 0.35))
    flat = float(np.mean(frames.std(axis=(1, 2)) < 8.0))
    # flou : image bien moins nette que la netteté habituelle de CETTE vidéo (traînées,
    # mise au point, transitions trop longues). Relatif à la vidéo : indépendant du jeu.
    sharp = np.array([_sharpness(f) for f in frames])
    ref = float(np.median(sharp))
    soft = sharp < 0.35 * ref if ref > 0 else np.zeros(n, bool)
    times = [round(float(i) / fps, 1) for i in np.flatnonzero(soft)]
    return {"frames": int(n), "burnt": round(burnt, 3), "flat": round(flat, 3),
            "blurred": round(float(soft.mean()), 3), "blur_times": times[:12]}


def run_visual_qa(render: str | Path, plan: dict, qa_cfg: dict) -> list[dict]:
    checks = []
    exp = exposure(render)
    if not exp["frames"]:
        return checks
    burnt_max = float(qa_cfg.get("max_burnt_ratio", 0.06))
    status = FAIL if exp["burnt"] > burnt_max else WARN if exp["burnt"] > burnt_max / 2 else PASS
    checks.append({"name": "images brûlées (flash, surexposition)", "status": status,
                   "detail": f"{exp['burnt'] * 100:.1f} % des images (max "
                             f"{burnt_max * 100:.0f} %)", "value": exp["burnt"]})
    blur_max = float(qa_cfg.get("max_blurred_ratio", 0.12))
    status = FAIL if exp["blurred"] > blur_max else WARN if exp["blurred"] > blur_max / 2 \
        else PASS
    where = f", vers {', '.join(f'{t:g} s' for t in exp['blur_times'][:6])}" \
        if exp["blur_times"] else ""
    checks.append({"name": "images floues (effets, traînées)", "status": status,
                   "detail": f"{exp['blurred'] * 100:.1f} % des images (max "
                             f"{blur_max * 100:.0f} %){where}", "value": exp["blurred"]})
    flat_max = float(qa_cfg.get("max_flat_ratio", 0.15))
    checks.append({"name": "images ternes (sans contraste)",
                   "status": WARN if exp["flat"] > flat_max else PASS,
                   "detail": f"{exp['flat'] * 100:.1f} % des images", "value": exp["flat"]})
    return checks
