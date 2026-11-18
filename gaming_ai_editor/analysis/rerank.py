"""Re-classement des moments forts : ce qu'un monteur choisirait, pas seulement le plus fort.

    score = base_signaux × bonus_événements × bonus_série × bonus_texte
            × pénalité_doublon × pénalité_répétition

- événements : réaction du joueur (×1,3), silence puis pic (×1,4) ;
- série : 3 actions ou plus à moins de 8 s = un seul grand moment, ×(1 + 0,25 par action
  au-delà de 2), plafonné à ×2 ; le moment est élargi pour contenir toute la série ;
- texte à l'écran (OCR) : kill / headshot / victoire ×1,5, fail ×1,2, avec étiquette ;
- doublon visuel (empreinte perceptuelle de l'image du pic) : ×0,6 ;
- variété : la k-ième répétition d'une même étiquette ×0,9^(k-1).

La vision IA (Gemini ou Claude) s'applique ensuite, seulement sur les meilleurs candidats.
Les informations détaillées vont dans `meta` (why, streak, ocr_hits, dup_of, base_score).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..ffutil import _popen_kwargs, ffmpeg_bin
from . import ocr


def grab_frame(path: str | Path, t: float, width: int = 960) -> np.ndarray | None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error",
           "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=60, **_popen_kwargs()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not raw:
        return None
    pixels = len(raw) // 3
    height = pixels // width
    if height <= 0:
        return None
    return np.frombuffer(raw[: width * height * 3], dtype=np.uint8).reshape(height, width, 3)


def dhash(frame: np.ndarray, size: int = 8) -> int:
    """Empreinte perceptuelle (différence de luminosité entre pixels voisins)."""
    gray = frame.mean(axis=2)
    h, w = gray.shape
    ys = np.linspace(0, h - 1, size).astype(int)
    xs = np.linspace(0, w - 1, size + 1).astype(int)
    small = gray[np.ix_(ys, xs)]
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def moment_times(h: dict, duration: float) -> list[float]:
    """Début, pic et fin d'un moment : de quoi juger l'action, pas une image figée."""
    return sorted({round(min(max(t, 0.0), max(duration - 0.05, 0.0)), 2)
                   for t in (h["start"], h["peak"], min(h["end"], h["peak"] + 1.5))})


def ocr_times(h: dict, duration: float) -> list[float]:
    """Les textes de jeu apparaissent APRÈS l'action : kill feed ~0,5 s après, bandeaux
    de victoire ou d'échec une à deux secondes après la fin."""
    return sorted({round(min(max(t, 0.0), max(duration - 0.05, 0.0)), 2)
                   for t in (h["peak"] + 0.7, h["end"] + 2.0)})


def rerank(path: str | Path, analysis: dict, cfg: dict, games: dict | None = None) -> dict:
    """Modifie analysis["highlights"] en place et renvoie un résumé."""
    hl = analysis.get("highlights", [])
    if not hl:
        return {}
    duration = float(analysis.get("duration", 0))
    events = analysis.get("events", [])
    streaks = analysis.get("streaks", [])
    for h in hl:
        meta = h.setdefault("meta", {})
        meta["base_score"] = h["score"]
        near = [e for e in events if h["start"] - 1.0 <= e["t"] <= h["end"] + 1.0]
        bonus, why = 1.0, []
        if any(e["kind"] == "reaction" for e in near):
            bonus *= 1.3
            why.append("réaction du joueur")
        if any(e["kind"] == "surprise" for e in near):
            bonus *= 1.4
            why.append("silence puis pic")
        streak = next((s for s in streaks if s["start"] - 1.0 <= h["peak"] <= s["end"] + 1.0),
                      None)
        if streak:
            bonus *= min(2.0, 1.0 + 0.25 * (streak["count"] - 2))
            why.append(f"série de {streak['count']} actions")
            meta["streak"] = streak
            if streak["end"] - streak["start"] <= 20:  # le moment contient toute la série
                h["start"] = round(max(0.0, min(h["start"], streak["start"] - 0.5)), 3)
                h["end"] = round(min(duration, max(h["end"], streak["end"] + 0.5)), 3)
        if h.get("label") != "menu":
            if streak:
                h["label"] = f"série x{streak['count']}"
            elif "silence puis pic" in why:
                h["label"] = "surprise"
            elif "réaction du joueur" in why:
                h["label"] = "réaction"
        h["score"] = round(h["score"] * bonus, 4)
        meta["bonus"] = round(bonus, 3)
        if why:
            meta["why"] = ", ".join(why)
    hl.sort(key=lambda h: h["score"], reverse=True)

    top = [h for h in hl if h.get("label") != "menu"][: int(cfg.get("rerank_top", 20))]
    use_ocr = cfg.get("ocr", "auto") != "off" and ocr.available()
    profile = ocr.game_profile(games or {}, str(path))
    hashes: list[tuple[int, dict]] = []
    ocr_moments = 0
    ocr_top = int(cfg.get("ocr_top", 10))
    for rank, h in enumerate(top):
        peak = grab_frame(path, h["peak"], width=320)
        if peak is not None:
            h["meta"]["dhash"] = dhash(peak)
        if use_ocr and rank < ocr_top:
            frames = [(t, f) for t in ocr_times(h, duration)
                      if (f := grab_frame(path, t)) is not None]
            hits = ocr.scan(frames, profile)
            if hits:
                ocr_moments += 1
                label = hits[0]["label"]
                h["meta"]["ocr_hits"] = hits
                h["label"], h["source"] = label, "ocr"
                h["score"] = round(h["score"] * (1.2 if label == "fail" else 1.5), 4)
                h["meta"]["why"] = ", ".join(filter(None, [h["meta"].get("why"),
                                                           f"texte à l'écran « {hits[0]['text']} »"]))
    # doublons visuels : deux moments qui montrent la même image
    for h in sorted(top, key=lambda x: x["score"], reverse=True):
        dh = h["meta"].get("dhash")
        if dh is None:
            continue
        twin = next((o for d, o in hashes if hamming(d, dh) <= 8), None)
        if twin is not None:
            h["score"] = round(h["score"] * 0.6, 4)
            h["meta"]["dup_of"] = twin["peak"]
        else:
            hashes.append((dh, h))
    # variété : on ne retient pas cinq fois le même type de moment
    counts: dict[str, int] = {}
    for h in sorted(hl, key=lambda x: x["score"], reverse=True):
        k = counts.get(h.get("label", ""), 0)
        if k:
            h["score"] = round(h["score"] * 0.9 ** k, 4)
        counts[h.get("label", "")] = k + 1
    for h in hl:
        h["meta"].pop("dhash", None)
    hl.sort(key=lambda h: h["score"], reverse=True)
    return {"ocr": "actif" if use_ocr else "indisponible", "game": profile["name"],
            "ocr_moments": ocr_moments, "candidates": len(top)}
