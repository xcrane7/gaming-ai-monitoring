"""1. Gameplay Intelligence Engine — comprendre ce qui se passe dans la vidéo.

À partir des signaux bruts (énergie audio fine à 20 ms, part de graves,
luminosité, mouvement, position de l'action), il produit :

- des **événements typés** situés à 20 ms près :
    shot      transitoire sec et bref (tir, coup) ;
    explosion transitoire riche en graves, qui dure, ou accompagné d'un flash ;
    impact    transitoire fort non classé ;
    burst     rafale d'au moins 3 transitoires rapprochés (combo) ;
    flash     éclair visuel sans son marquant ;
    rush      mouvement intense et soutenu ;
- une **piste de suivi** de l'action (position horizontale lissée) pour le recadrage vertical ;
- un **profil colorimétrique** (teinte dominante, saturation, luminosité) ;
- le **rythme** de la vidéo (événements par minute, mouvement moyen).

Les étiquettes sémantiques (kill, victoire, headshot...) ne peuvent pas être
devinées de façon fiable par le son et l'image seuls : elles viennent de Gemini
quand il est disponible (voir ai/providers.py). Sans IA, le moteur ne prétend
jamais qu'un événement est un « kill ».
"""
from __future__ import annotations

import colorsys
import math
import subprocess
from pathlib import Path

import numpy as np

from ..ffutil import MediaInfo, _popen_kwargs, ffmpeg_bin

EVENT_KINDS = ("shot", "explosion", "impact", "burst", "flash", "rush")


def _robust(x: np.ndarray) -> tuple[float, float]:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return med, max(mad, 1e-6)


def detect_onsets(energy: np.ndarray, bass: np.ndarray, hop: float,
                  max_per_minute: int = 40) -> list[dict]:
    """Transitoires audio : montée brutale d'énergie par rapport aux 100 ms précédentes."""
    if energy.size < 20:
        return []
    level = 20.0 * np.log10(energy + 1e-4)
    # niveau moyen des 4 tranches qui précèdent (de 40 à 100 ms avant)
    past = np.convolve(level, np.ones(4) / 4, mode="full")[: level.size]
    past = np.concatenate([[level[0]] * 2, past[:-2]])
    rise = level - past
    med, mad = _robust(rise)
    loud_floor = float(np.percentile(level, 40))
    threshold = max(6.0, med + 4.0 * mad)
    radius = max(1, int(0.15 / hop))
    spacing = max(1, int(0.18 / hop))
    candidates = []
    for i in range(radius, rise.size - radius):
        r = rise[i]
        if r < threshold or level[i] < loud_floor:
            continue
        if r < rise[i - radius:i + radius + 1].max():
            continue
        candidates.append(i)
    onsets: list[dict] = []
    taken: list[int] = []
    top = max([rise[i] for i in candidates], default=threshold + 1.0)
    bass_ref = float(np.median(bass)) if bass.size else 0.0
    for i in sorted(candidates, key=lambda k: rise[k], reverse=True):
        if any(abs(i - j) < spacing for j in taken):
            continue
        taken.append(i)
        peak = level[i:i + int(0.06 / hop) + 1].max()
        after = level[i + int(0.15 / hop): i + int(0.15 / hop) + 1]
        sustain = float(peak - after[0]) if after.size else 20.0
        # part de graves relative au reste de la vidéo (certains jeux ont un fond grave permanent)
        b = float(bass[i:i + 4].mean()) - bass_ref if bass.size > i else 0.0
        onsets.append({
            "t": round(i * hop, 3),
            "strength": round(float(0.3 + 0.7 * (rise[i] - threshold)
                                    / max(top - threshold, 1e-6)), 3),
            "bass": round(b, 3),
            "decay_db": round(sustain, 2),  # chute après 150 ms : grande = son sec
            "level": round(float(level[i]), 2),
        })
    duration_min = max(energy.size * hop / 60.0, 1 / 60)
    limit = max(8, int(max_per_minute * duration_min))
    onsets.sort(key=lambda o: o["strength"], reverse=True)
    return sorted(onsets[:limit], key=lambda o: o["t"])


def classify(onsets: list[dict], brightness: np.ndarray, motion: np.ndarray,
             video_fps: float, window: float) -> list[dict]:
    events: list[dict] = []
    bjump = np.zeros_like(brightness)
    if brightness.size > 1:
        bjump[1:] = np.maximum(0.0, np.diff(brightness))

    def flash_near(t: float, radius: float = 0.3) -> float:
        if bjump.size == 0:
            return 0.0
        a = max(0, int((t - radius) * video_fps))
        b = min(bjump.size, int((t + radius) * video_fps) + 1)
        return float(bjump[a:b].max()) if b > a else 0.0

    for o in onsets:
        fl = flash_near(o["t"])
        if (o["bass"] >= 0.12 and o["decay_db"] < 9) or fl >= 0.08:
            kind = "explosion"
        elif o["decay_db"] >= 9 and o["bass"] < 0.12:
            kind = "shot"
        else:
            kind = "impact"
        events.append({"t": o["t"], "kind": kind, "strength": o["strength"],
                       "bass": o["bass"], "flash": round(fl, 3)})

    # rafales : au moins 3 transitoires espacés de moins de 0,6 s
    run: list[dict] = []
    bursts = []
    for e in events + [{"t": 1e9}]:
        if run and e["t"] - run[-1]["t"] <= 0.6:
            run.append(e)
            continue
        if len(run) >= 3:
            bursts.append({"t": run[0]["t"], "end": run[-1]["t"], "kind": "burst",
                           "count": len(run),
                           "strength": round(min(1.0, max(x["strength"] for x in run) + 0.1), 3)})
        run = [e] if e["t"] < 1e9 else []
    events.extend(bursts)

    # éclairs visuels sans son marquant
    if bjump.size:
        thr = 0.12
        last = -10.0
        for k in np.argsort(bjump)[::-1][:50]:
            t = k / video_fps
            if bjump[k] < thr:
                break
            if abs(t - last) < 1.0 or any(abs(t - e["t"]) < 0.4 for e in events):
                continue
            events.append({"t": round(float(t), 3), "kind": "flash",
                           "strength": round(float(min(1.0, bjump[k] * 3)), 3)})
            last = t

    # rush : mouvement fort soutenu au moins 2 s
    if motion.size > 4:
        med, mad = _robust(motion)
        hot = (motion - med) / mad > 2.5
        k = 0
        while k < hot.size:
            if hot[k]:
                j = k
                while j < hot.size and hot[j]:
                    j += 1
                if (j - k) * window >= 2.0:
                    mid = (k + j) / 2 * window
                    events.append({"t": round(float(mid), 3), "kind": "rush",
                                   "end": round(float(j * window), 3),
                                   "strength": round(float(min(1.0, (motion[k:j].mean() - med)
                                                               / mad / 6)), 3)})
                k = j
            else:
                k += 1
    return sorted(events, key=lambda e: e["t"])


def focus_track(focus: np.ndarray, video_fps: float, step: float = 0.5,
                smooth_seconds: float = 1.5) -> dict:
    """Position horizontale de l'action, lissée, échantillonnée toutes les `step` secondes."""
    if focus.size == 0:
        return {"step": step, "x": [], "spread": 0.0}
    x = focus.astype(np.float64)
    valid = ~np.isnan(x)
    if valid.sum() < 2:
        return {"step": step, "x": [0.5] * max(1, int(focus.size / video_fps / step)),
                "spread": 0.0}
    idx = np.arange(x.size)
    x = np.interp(idx, idx[valid], x[valid])
    # ramène doucement vers le centre : on ne suit que les déplacements francs
    x = 0.5 + 0.8 * (x - 0.5)
    width = max(1, int(smooth_seconds * video_fps))
    kernel = np.hanning(width + 2)[1:-1]
    kernel /= kernel.sum()
    padded = np.pad(x, width, mode="edge")
    x = np.convolve(padded, kernel, mode="same")[width:-width]
    samples = x[:: max(1, int(round(step * video_fps)))]
    return {"step": step, "x": [round(float(v), 3) for v in samples],
            "spread": round(float(np.std(samples)), 3)}


def color_profile(path: str | Path, duration: float, samples: int = 12) -> dict:
    """Teinte dominante, saturation et luminosité moyennes (quelques images suffisent)."""
    w, h = 32, 18
    every = max(duration / samples, 0.5)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-map", "0:v:0", "-vf", f"fps=1/{every:.3f},scale={w}:{h}", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=300, **_popen_kwargs()).stdout
    except (OSError, subprocess.TimeoutExpired):
        raw = b""
    if len(raw) < w * h * 3:
        return {"hue": 0.0, "saturation": 0.0, "luma": 0.5}
    px = np.frombuffer(raw[: len(raw) // (w * h * 3) * w * h * 3], dtype=np.uint8)
    px = px.reshape(-1, 3).astype(np.float64) / 255.0
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px[:: max(1, len(px) // 4000)]])
    sat = hsv[:, 1] * hsv[:, 2]
    ang = hsv[:, 0] * 2 * math.pi
    hue = math.atan2(float((np.sin(ang) * sat).sum()), float((np.cos(ang) * sat).sum()))
    luma = float((0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]).mean())
    return {"hue": round((hue / (2 * math.pi)) % 1.0, 3),
            "saturation": round(float(hsv[:, 1].mean()), 3), "luma": round(luma, 3)}


def ui_windows(coverage: np.ndarray, video_fps: float, threshold: float = 0.05,
               min_seconds: float = 1.5) -> list[list[float]]:
    """Passages de menu, d'écran de pause ou de chargement : presque rien ne bouge.

    En jeu, la caméra bouge et une grande partie de l'image change d'une image à
    l'autre ; dans un menu, seul un petit élément s'anime (curseur, aperçu).
    """
    if coverage.size == 0:
        return []
    width = max(1, int(2.0 * video_fps))
    padded = np.pad(coverage, width // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)[: coverage.size]
    static = np.median(windows, axis=1) < threshold
    out, k = [], 0
    while k < static.size:
        if static[k]:
            j = k
            while j < static.size and static[j]:
                j += 1
            if (j - k) / video_fps >= min_seconds:
                out.append([round(k / video_fps, 2), round(j / video_fps, 2)])
            k = j
        else:
            k += 1
    return out


def in_windows(t: float, windows: list, margin: float = 0.0) -> bool:
    return any(a - margin <= t <= b + margin for a, b in windows)


def analyze(path: str | Path, info: MediaInfo, signals) -> dict:
    """Résultat ajouté à l'analyse d'une vidéo (clés events, focus, color, pace)."""
    ex = signals.extras or {}
    hop = float(ex.get("hop", 0.02))
    onsets = detect_onsets(np.asarray(ex.get("fine_energy", [])),
                           np.asarray(ex.get("fine_bass", [])), hop)
    video_fps = float(ex.get("video_fps", 4))
    events = classify(onsets, np.asarray(ex.get("brightness", [])), signals.motion,
                      video_fps, signals.window)
    ui = ui_windows(np.asarray(ex.get("coverage", [])), video_fps)
    events = [e for e in events if not in_windows(e["t"], ui, 0.3)]
    focus = focus_track(np.asarray(ex.get("focus_x", [])), video_fps)
    minutes = max(info.duration / 60.0, 1 / 60)
    counts = {k: sum(1 for e in events if e["kind"] == k) for k in EVENT_KINDS}
    return {
        "events": events,
        "ui_windows": ui,
        "focus": focus,
        "color": color_profile(path, info.duration) if info.has_video else {},
        "pace": {"events_per_minute": round(len(onsets) / minutes, 1),
                 "motion_mean": round(float(np.mean(signals.motion)) if signals.motion.size
                                      else 0.0, 4),
                 "counts": counts},
    }


def events_in(analysis: dict, start: float, end: float) -> list[dict]:
    return [e for e in analysis.get("events", []) if start <= e["t"] <= end]
