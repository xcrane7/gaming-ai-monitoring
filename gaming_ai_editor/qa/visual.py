"""Contrôle qualité visuel : juge les images réellement rendues, pas le plan.

- images floues ajoutées : chaque image du rendu est comparée à l'image du jeu au même
  instant (paires). Deux mesures essayées puis abandonnées : la netteté globale rendu/source
  (le rendu ajoute des détails fins et paraît « plus net » même flouté) et la part d'images
  floues de chaque vidéo rapportée à sa propre médiane (un montage garde surtout l'action :
  sa médiane change, ses images calmes passaient pour floues). La comparaison directe avec la source a été
  essayée puis abandonnée : le rendu ajoute des détails fins (particules, accentuation,
  compression) qui le font paraître « plus net » que le jeu même quand il est flouté ;
- images brûlées : part des images où plus de 35 % des pixels sont blancs (flash trop
  fort, surexposition) ;
- images ternes : part des images sans contraste (écart-type de luminance très faible).

Un échec (images brûlées, image blanche dans l'accroche) déclenche le second essai
« prudent » du pipeline. Le flou ajouté n'est qu'un avertissement : sur un vrai gameplay,
les paires d'images restent décalées de 1 à 2 images et le flou de mouvement du jeu varie
d'une image à l'autre (faux positifs vérifiés à l'œil).
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
    w, h = 96, 160
    info = probe(render)
    if info and info.width and info.height and info.width > info.height:
        w, h = 160, 90
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(render), "-vf",
           f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, **_popen_kwargs()).stdout
    frames = np.frombuffer(out, np.uint8)
    n = frames.size // (w * h)
    if n == 0:
        return {"frames": 0, "burnt": 0.0, "flat": 0.0, "burnt_times": []}
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    white = (frames >= 240).mean(axis=(1, 2)) > 0.35
    burnt_times = [round(float(i) / fps, 1) for i in np.flatnonzero(white)]
    flat = float(np.mean(frames.std(axis=(1, 2)) < 8.0))
    return {"frames": int(n), "burnt": round(float(np.mean(white)), 3), "flat": round(flat, 3),
            "burnt_times": burnt_times[:12]}


def _decode(path: str | Path, fps: float, w: int, h: int, start: float | None = None,
            dur: float | None = None, crop: str = "") -> np.ndarray:
    cmd = [ffmpeg_bin(), "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, dur or 0.1):.3f}"]
    cmd += ["-i", str(path), "-vf", f"fps={fps:g},{crop}scale={w}:{h}:flags=area,format=gray",
            "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, **_popen_kwargs()).stdout
    n = len(out) // (w * h)
    return np.frombuffer(out, np.uint8)[: n * w * h].reshape(n, h, w).astype(np.float32)


def added_blur(render: str | Path, plan: dict, fps: float = 2.0) -> dict | None:
    """Flou AJOUTÉ par le montage, image par image : chaque image du rendu est comparée à
    l'image de la source au même instant. Le rapport habituel (médiane) absorbe ce qui
    touche toute la vidéo (accentuation, particules, mise à l'échelle) ; une image est
    « floutée par le montage » si son rapport tombe sous 35 % de ce rapport habituel.
    Le flou propre au jeu (rotations rapides) est flou des deux côtés : il ne compte pas.
    None si le plan ou les sources manquent."""
    from ..editor.plan import EditPlan

    if not plan.get("segments"):
        return None
    p = EditPlan.from_dict(plan)
    vertical = int(plan.get("height", 0)) > int(plan.get("width", 1))
    w, h = (180, 320) if vertical else (320, 180)
    rfps = 10.0
    rend = _decode(render, rfps, w, h)
    if not len(rend):
        return None
    junctions = p.junctions()
    pairs = []  # (temps du rendu, rapport de netteté rendu / source)
    for seg, off in zip(p.segments, p.offsets()):
        if not Path(seg.source).is_file():
            return None
        crop = "crop='min(iw,ih*9/16)':ih," if vertical else ""
        src = _decode(seg.source, fps, w, h, seg.start, seg.duration, crop)
        for k, frame in enumerate(src):
            t_out = off + seg.map_time(seg.start + k / fps)
            if any(abs(t_out - j) < 0.4 for j in junctions):
                continue  # transitions : deux images mêlées, pas de correspondance
            i = int(round(t_out * rfps))
            base = _sharpness(frame)
            if i >= len(rend) or base < 1.0:
                continue
            pairs.append((t_out, _sharpness(rend[i]) / base))
    if len(pairs) < 4:
        return None
    ratios = np.array([r for _, r in pairs])
    usual = float(np.median(ratios))
    soft = ratios < 0.35 * usual
    times = [round(t, 1) for (t, _), bad in zip(pairs, soft) if bad]
    return {"samples": len(pairs), "added": round(float(soft.mean()), 3),
            "times": times[:12], "usual_ratio": round(usual, 2)}


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
    # désactivé par défaut : faux positifs sur le gameplay réel (16 %) et sur les vidéos de
    # test (23 %) alors qu'aucun flou n'était ajouté ; à réactiver une fois fiabilisé
    blur = added_blur(render, plan) if qa_cfg.get("blur_check", False) else None
    if blur is not None:
        added = blur["added"]
        # avertissement seulement : sur le gameplay FPS réel, les paires d'images sont décalées
        # de 1 à 2 images (20 à 40 ms) et le flou de mouvement du jeu change d'une image à
        # l'autre ; les images signalées (16 %) n'étaient pas visiblement plus floues
        status = WARN if added > blur_max else PASS
        where = f", vers {', '.join(f'{t:g} s' for t in blur['times'][:6])}" \
            if blur["times"] and status != PASS else ""
        checks.append({"name": "images floues ajoutées (effets, traînées)", "status": status,
                       "detail": f"{added * 100:.1f} % des images nettement plus floues que le "
                                 f"jeu au même instant (max {blur_max * 100:.0f} %, "
                                 f"{blur['samples']} paires){where}", "value": added})
    flat_max = float(qa_cfg.get("max_flat_ratio", 0.15))
    checks.append({"name": "images ternes (sans contraste)",
                   "status": WARN if exp["flat"] > flat_max else PASS,
                   "detail": f"{exp['flat'] * 100:.1f} % des images", "value": exp["flat"]})
    return checks
