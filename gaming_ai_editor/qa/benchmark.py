"""Benchmarks : vitesse, taille et qualité (SSIM) d'un réglage d'encodage."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from .. import synth
from ..ffutil import (BUILTIN_ENCODE_PROFILE, FFmpegError, encoder_works, probe, run_capture,
                      run_ffmpeg)


def reference_clip(data_dir: Path, seconds: int = 12) -> Path:
    """Clip de référence 1080p (généré une fois, réutilisé ensuite)."""
    ref = Path(data_dir) / "benchmark" / f"reference_{seconds}s.mp4"
    if not ref.exists():
        synth.make_gameplay(ref, duration=seconds, width=1920, height=1080, fps=60,
                            bursts=[(3, 5), (8, 10)], cut_every=4)
    return ref


def ssim(encoded: Path, reference: Path) -> float | None:
    code, err = run_capture(["-v", "info", "-i", str(encoded), "-i", str(reference),
                             "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"], timeout=900)
    m = re.search(r"All:([\d.]+)", err)
    return float(m.group(1)) if code == 0 and m else None


def measure_encode(reference: Path, codec_args: list[str], out: Path,
                   low_priority: bool = False,
                   cancel_check: Callable[[], bool] | None = None) -> dict:
    """Encode le clip de référence avec `codec_args` et mesure le résultat."""
    info = probe(reference)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    run_ffmpeg(["-i", str(reference), "-an", *codec_args, str(out)],
               low_priority=low_priority, cancel_check=cancel_check, timeout=900)
    seconds = time.time() - started
    frames = info.duration * info.fps
    result = {
        "seconds": round(seconds, 3),
        "fps": round(frames / max(seconds, 1e-3), 1),
        "size_bytes": out.stat().st_size,
        "bitrate_kbps": round(out.stat().st_size * 8 / 1000 / max(info.duration, 1e-3), 1),
        "ssim": ssim(out, reference),
    }
    out.unlink(missing_ok=True)
    return result


def run_benchmark(cfg, log: Callable[[str], None] = print) -> dict:
    """Compare les encodeurs réellement utilisables sur cette machine."""
    ref = reference_clip(cfg.data_dir)
    work = cfg.data_dir / "benchmark"
    candidates: dict[str, list[str]] = {
        "x264 veryfast crf20 (défaut CPU)": BUILTIN_ENCODE_PROFILE["cpu"]["final"],
        "x264 faster crf20": ["-c:v", "libx264", "-preset", "faster", "-crf", "20",
                              "-pix_fmt", "yuv420p"],
        "x264 ultrafast crf18": ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                                 "-pix_fmt", "yuv420p"],
    }
    nvenc = encoder_works("h264_nvenc")
    if nvenc:
        candidates["nvenc p5 cq21 (défaut GPU)"] = BUILTIN_ENCODE_PROFILE["nvenc"]["final"]
        candidates["nvenc p7 cq21"] = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
                                       "-rc", "vbr", "-cq", "21", "-b:v", "0",
                                       "-pix_fmt", "yuv420p"]
        if encoder_works("hevc_nvenc"):
            candidates["hevc_nvenc p5 cq24"] = ["-c:v", "hevc_nvenc", "-preset", "p5",
                                                "-rc", "vbr", "-cq", "24", "-b:v", "0",
                                                "-pix_fmt", "yuv420p"]
    results = {}
    for name, args in candidates.items():
        try:
            results[name] = measure_encode(ref, args, work / "bench_out.mp4")
            r = results[name]
            log(f"{name}: {r['fps']} img/s, {r['bitrate_kbps']} kb/s, SSIM {r['ssim']}")
        except FFmpegError as exc:
            results[name] = {"error": str(exc)[:300]}
            log(f"{name}: ÉCHEC {str(exc)[:120]}")
    return {"reference": str(ref), "nvenc_available": nvenc, "results": results,
            "at": time.time()}
