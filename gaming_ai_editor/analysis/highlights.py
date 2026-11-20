"""Fusion des signaux en score d'intensité et détection des moments forts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .signals import Signals


@dataclass
class Highlight:
    start: float
    end: float
    peak: float
    score: float
    label: str = "action"
    source: str = "local"
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def robust_z(x: np.ndarray) -> np.ndarray:
    """Z-score robuste (médiane / MAD), borné à [0, 6]."""
    if x.size == 0:
        return x
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    scale = 1.4826 * mad
    if scale < 1e-6:
        scale = float(np.std(x))
    if scale < 1e-6:
        return np.zeros_like(x, dtype=np.float64)
    return np.clip((x - med) / scale, 0.0, 6.0)


def smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or x.size == 0:
        return x
    kernel = np.hanning(width + 2)[1:-1]
    kernel /= kernel.sum()
    return np.convolve(x, kernel, mode="same")


def score_timeline(signals: Signals, weights: dict, smooth_seconds: float) -> np.ndarray:
    audio = robust_z(signals.rms_db)
    flux = robust_z(signals.flux)
    motion = robust_z(signals.motion)
    score = (weights.get("audio", 0.45) * audio + weights.get("flux", 0.2) * flux
             + weights.get("motion", 0.35) * motion)
    return smooth(score, max(1, int(round(smooth_seconds / signals.window))))


def _label(signals: Signals, index: int) -> str:
    a = robust_z(signals.rms_db)
    m = robust_z(signals.motion)
    lo, hi = max(0, index - 3), min(signals.n, index + 4)
    audio, motion = float(a[lo:hi].max(initial=0)), float(m[lo:hi].max(initial=0))
    if audio >= 2.0 and motion >= 2.0:
        return "action intense"
    if audio >= motion:
        return "pic sonore"
    return "mouvement"


def find_highlights(signals: Signals, cfg: dict) -> tuple[list[Highlight], np.ndarray]:
    """Retourne les moments forts triés par score décroissant et la courbe de score."""
    score = score_timeline(signals, cfg.get("weights", {}), float(cfg.get("smooth_seconds", 3)))
    n = score.size
    if n == 0:
        return [], score
    w = signals.window
    duration = signals.duration or n * w
    min_dist = max(1, int(round(float(cfg.get("peak_min_distance", 6)) / w)))
    max_count = int(cfg.get("max_highlights", 120))
    baseline = float(np.median(score))
    spread = float(score.max() - baseline)

    highlights: list[Highlight] = []
    if spread > 1e-3:
        threshold = max(float(np.percentile(score, float(cfg.get("peak_percentile", 80)))),
                        baseline + 0.15 * spread)
        order = np.argsort(score)[::-1]
        taken = np.zeros(n, dtype=bool)
        for idx in order:
            if score[idx] < threshold or len(highlights) >= max_count:
                break
            if taken[idx]:
                continue
            lo, hi = max(0, idx - min_dist), min(n, idx + min_dist + 1)
            taken[lo:hi] = True
            # cœur du moment : zone contiguë au-dessus de la mi-hauteur du pic
            half = baseline + 0.5 * (score[idx] - baseline)
            left = idx
            while left > 0 and score[left - 1] >= half and idx - left < int(12 / w):
                left -= 1
            right = idx
            while right < n - 1 and score[right + 1] >= half and right - idx < int(12 / w):
                right += 1
            highlights.append(Highlight(
                start=round(float(left * w), 3),
                end=round(float(min(duration, (right + 1) * w)), 3),
                peak=round(float((idx + 0.5) * w), 3), score=round(float(score[idx]), 4),
                label=_label(signals, int(idx)),
            ))

    if not highlights:
        # Vidéo « plate » (aucun pic net) : repères réguliers pour pouvoir monter quand même.
        count = max(1, min(12, int(duration // 20) or 1))
        for i in range(count):
            peak = duration * (i + 0.5) / count
            highlights.append(Highlight(
                start=round(float(max(0.0, peak - 2)), 3),
                end=round(float(min(duration, peak + 2)), 3),
                peak=round(float(peak), 3), score=0.01, label="repère régulier",
                meta={"fallback": True},
            ))
    return highlights, score
