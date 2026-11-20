"""Extraction des signaux bruts d'un gameplay.

FFmpeg décode (avec accélération matérielle si possible) et envoie un flux
réduit ; OpenCV calcule mouvement et changements de scène. Sans OpenCV, un
repli NumPy donne les mêmes mesures (un peu moins fines).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..ffutil import FFmpegError, MediaInfo, _popen_kwargs, ffmpeg_bin

try:  # OpenCV est recommandé mais pas obligatoire
    import cv2  # type: ignore

    HAVE_CV2 = True
except Exception:  # pragma: no cover - dépend de l'installation
    cv2 = None
    HAVE_CV2 = False


@dataclass
class Signals:
    window: float
    duration: float
    rms_db: np.ndarray = field(default_factory=lambda: np.zeros(0))
    flux: np.ndarray = field(default_factory=lambda: np.zeros(0))
    motion: np.ndarray = field(default_factory=lambda: np.zeros(0))
    scene_cuts: list[float] = field(default_factory=list)
    backend: str = "numpy"
    extras: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return max(len(self.rms_db), len(self.motion))


HOP_SECONDS = 0.02  # résolution fine de l'analyse audio (20 ms)


def audio_signals(path: str | Path, info: MediaInfo, window: float, rate: int = 16000,
                  cancel_check: Callable[[], bool] | None = None,
                  extras: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Énergie RMS (dB) et flux d'énergie positif par fenêtre.

    Si `extras` est fourni, y ajoute l'énergie fine (20 ms) et la part de graves
    de chaque tranche, utilisées pour situer précisément les impacts.
    """
    n_windows = max(1, int(np.ceil(info.duration / window)))
    if not info.has_audio:
        return np.full(n_windows, -80.0), np.zeros(n_windows)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            **_popen_kwargs())
    samples_per_window = int(rate * window)
    chunk_bytes = samples_per_window * 2
    values: list[float] = []
    hop = int(rate * HOP_SECONDS)
    fine_energy: list[np.ndarray] = []
    fine_bass: list[np.ndarray] = []
    fine_voice: list[np.ndarray] = []
    fine_flat: list[np.ndarray] = []
    bass_bin = max(1, int(200 * hop / rate))  # bins FFT sous 200 Hz
    voice_lo, voice_hi = int(300 * hop / rate), int(3400 * hop / rate) + 1  # bande de la voix
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(chunk_bytes)
            if not buf:
                break
            data = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32)
            if data.size == 0:
                break
            norm = data / 32768.0
            rms = float(np.sqrt(np.mean(np.square(norm))))
            values.append(20.0 * np.log10(max(rms, 1e-4)))
            if extras is not None and norm.size >= hop:
                frames = norm[: norm.size // hop * hop].reshape(-1, hop)
                fine_energy.append(np.sqrt(np.mean(frames ** 2, axis=1)))
                spec = np.abs(np.fft.rfft(frames * np.hanning(hop), axis=1)) ** 2
                total = spec.sum(axis=1) + 1e-12
                fine_bass.append(spec[:, 1:bass_bin + 1].sum(axis=1) / total)
                # voix : énergie dans 300-3400 Hz et harmonicité (spectre « en peigne »,
                # peu plat), pour distinguer un cri d'un bruit large bande (explosion)
                band = spec[:, voice_lo:voice_hi] + 1e-12
                fine_voice.append(band.sum(axis=1) / total)
                fine_flat.append(np.exp(np.log(band).mean(axis=1)) / band.mean(axis=1))
            if cancel_check and cancel_check():
                proc.kill()
                break
    finally:
        proc.stdout.close()
        proc.wait()
    if not values:
        return np.full(n_windows, -80.0), np.zeros(n_windows)
    rms_db = np.asarray(values, dtype=np.float64)
    flux = np.maximum(0.0, np.diff(rms_db, prepend=rms_db[0]))
    if extras is not None and fine_energy:
        extras["fine_energy"] = np.concatenate(fine_energy)
        extras["fine_bass"] = np.concatenate(fine_bass)
        extras["fine_voice"] = np.concatenate(fine_voice)
        extras["fine_flat"] = np.concatenate(fine_flat)
        extras["hop"] = HOP_SECONDS
    return rms_db, flux


def _frame_metrics(prev: np.ndarray | None, frame: np.ndarray,
                   prev_hist: np.ndarray | None) -> tuple[float, float, np.ndarray]:
    """Retourne (mouvement, distance d'histogramme, histogramme)."""
    if HAVE_CV2:
        hist = cv2.calcHist([frame], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist, alpha=1.0, norm_type=cv2.NORM_L1)
        if prev is None or prev_hist is None:
            return 0.0, 0.0, hist
        motion = float(cv2.absdiff(frame, prev).mean()) / 255.0
        dist = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
        return motion, dist, hist
    hist = np.bincount(frame.ravel() >> 3, minlength=32).astype(np.float64)
    hist /= max(hist.sum(), 1.0)
    if prev is None or prev_hist is None:
        return 0.0, 0.0, hist
    motion = float(np.abs(frame.astype(np.int16) - prev.astype(np.int16)).mean()) / 255.0
    dist = float(np.sqrt(max(0.0, 1.0 - np.sum(np.sqrt(prev_hist * hist)))))
    return motion, dist, hist


def video_signals(path: str | Path, info: MediaInfo, window: float, fps: float = 4,
                  width: int = 160, height: int = 90, hwaccel: str = "auto",
                  on_progress: Callable[[float], None] | None = None,
                  cancel_check: Callable[[], bool] | None = None,
                  cut_threshold: float = 0.45,
                  extras: dict | None = None) -> tuple[np.ndarray, list[float]]:
    """Mouvement par fenêtre et instants des changements de scène.

    Si `extras` est fourni, y ajoute par image : luminosité moyenne et position
    horizontale de l'action (centre de gravité du mouvement, 0 = gauche, 1 = droite).
    """
    n_windows = max(1, int(np.ceil(info.duration / window)))
    motion = np.zeros(n_windows)
    cuts: list[float] = []
    if not info.has_video:
        return motion, cuts

    def run(use_hw: bool) -> bool:
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error"]
        if use_hw:
            cmd += ["-hwaccel", hwaccel if hwaccel not in ("", None) else "auto"]
        cmd += ["-i", str(path), "-map", "0:v:0", "-an",
                "-vf", f"fps={fps},scale={width}:{height}:flags=fast_bilinear,format=gray",
                "-f", "rawvideo", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                **_popen_kwargs())
        frame_bytes = width * height
        prev = None
        prev_hist = None
        index = 0
        last_cut = -10.0
        bright: list[float] = []
        focus: list[float] = []
        coverage: list[float] = []
        cols = np.arange(width, dtype=np.float64)
        assert proc.stdout is not None
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width)
                t = index / fps
                m, dist, hist = _frame_metrics(prev, frame, prev_hist)
                w = min(n_windows - 1, int(t / window))
                is_cut = dist >= cut_threshold or (dist >= 0.3 and m >= 0.25)
                if is_cut and t - last_cut >= 1.0 and index > 0:
                    cuts.append(round(t, 3))
                    last_cut = t
                    # un changement de plan n'est pas du « mouvement » de jeu
                    m = min(m, 0.02)
                motion[w] = max(motion[w], m)
                if extras is not None:
                    bright.append(float(frame.mean()) / 255.0)
                    fx = float("nan")
                    cov = 0.0
                    if prev is not None and not (is_cut and index > 0):
                        diff = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
                        cov = float((diff > 10).mean())  # part de l'image qui bouge
                        weight = diff.sum(axis=0).astype(np.float64)
                        if weight.sum() > width * height * 2:  # assez de mouvement
                            fx = float((weight * cols).sum() / weight.sum() / (width - 1))
                    focus.append(fx)
                    coverage.append(cov)
                prev, prev_hist = frame, hist
                index += 1
                if on_progress and index % 40 == 0 and info.duration:
                    on_progress(min(1.0, t / info.duration))
                if cancel_check and index % 40 == 0 and cancel_check():
                    proc.kill()
                    break
        finally:
            proc.stdout.close()
            proc.wait()
        if extras is not None:
            extras["brightness"] = np.asarray(bright)
            extras["focus_x"] = np.asarray(focus)
            extras["coverage"] = np.asarray(coverage)
            extras["video_fps"] = fps
        return index > 0

    use_hw = hwaccel not in ("none", "", None)
    if not run(use_hw):
        motion[:] = 0
        cuts.clear()
        if not use_hw or not run(False):
            raise FFmpegError(f"Impossible de décoder la vidéo : {path}")
    return motion, cuts


def extract_signals(path: str | Path, info: MediaInfo, cfg: dict,
                    on_progress: Callable[[float], None] | None = None,
                    cancel_check: Callable[[], bool] | None = None) -> Signals:
    window = float(cfg["window_seconds"])
    extras: dict = {}
    rms_db, flux = audio_signals(path, info, window, int(cfg["audio_rate"]), cancel_check,
                                 extras)
    if on_progress:
        on_progress(0.15)
    motion, cuts = video_signals(
        path, info, window, float(cfg["video_fps"]), int(cfg["video_width"]),
        int(cfg["video_height"]), cfg.get("hwaccel", "auto"),
        (lambda p: on_progress(0.15 + 0.85 * p)) if on_progress else None, cancel_check,
        extras=extras,
    )
    n = max(len(rms_db), len(motion))

    def fit(a: np.ndarray, fill: float) -> np.ndarray:
        if len(a) >= n:
            return a[:n]
        return np.concatenate([a, np.full(n - len(a), fill)])

    return Signals(
        window=window, duration=info.duration,
        rms_db=fit(rms_db, -80.0), flux=fit(flux, 0.0), motion=fit(motion, 0.0),
        scene_cuts=cuts, backend="opencv" if HAVE_CV2 else "numpy", extras=extras,
    )
