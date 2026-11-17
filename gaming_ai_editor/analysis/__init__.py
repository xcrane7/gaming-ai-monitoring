"""Agent Analyste local : signaux audio/vidéo -> moments forts."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..ffutil import MediaInfo, probe
from .highlights import Highlight, find_highlights
from .signals import HAVE_CV2, Signals, extract_signals

__all__ = ["analyze_video", "Highlight", "Signals", "HAVE_CV2"]


def analyze_video(path: str | Path, cfg: dict, info: MediaInfo | None = None,
                  on_progress: Callable[[float], None] | None = None,
                  cancel_check: Callable[[], bool] | None = None) -> dict:
    """Analyse complète d'un fichier ; ne modifie jamais la source (lecture seule)."""
    started = time.time()
    info = info or probe(path)
    signals = extract_signals(path, info, cfg, on_progress, cancel_check)
    highlights, score = find_highlights(signals, cfg)
    return {
        "path": str(path),
        "duration": info.duration,
        "width": info.width, "height": info.height, "fps": info.fps,
        "has_audio": info.has_audio,
        "window": signals.window,
        "backend": signals.backend,
        "scene_cuts": signals.scene_cuts,
        "score": [round(float(v), 4) for v in score],
        "rms_db": [round(float(v), 2) for v in signals.rms_db],
        "motion": [round(float(v), 5) for v in signals.motion],
        "highlights": [h.to_dict() for h in highlights],
        "analysis_seconds": round(time.time() - started, 2),
    }


def save_analysis(analysis: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis), encoding="utf-8")


def load_analysis(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def downsample(values: list[float], points: int = 600) -> list[float]:
    """Réduit une courbe pour l'affichage dans le dashboard (max par paquet)."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.size <= points:
        return [round(float(v), 4) for v in arr]
    edges = np.linspace(0, arr.size, points + 1).astype(int)
    return [round(float(arr[a:b].max()), 4) if b > a else 0.0
            for a, b in zip(edges[:-1], edges[1:])]
