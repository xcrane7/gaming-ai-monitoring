"""Outils FFmpeg : sonde, détection d'encodeur (NVENC réellement testé), exécution suivie."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

IS_WINDOWS = sys.platform.startswith("win")


class FFmpegError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """Tâche interrompue volontairement (ex. chercheur mis en pause par un rendu)."""


def ffmpeg_bin() -> str:
    return os.environ.get("GAE_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("GAE_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def have_ffmpeg() -> bool:
    return bool(shutil.which(ffmpeg_bin())) and bool(shutil.which(ffprobe_bin()))


def _popen_kwargs(low_priority: bool = False) -> dict:
    kwargs: dict = {}
    if IS_WINDOWS:
        flags = 0x08000000  # CREATE_NO_WINDOW
        if low_priority:
            flags |= 0x00000040  # IDLE_PRIORITY_CLASS
        kwargs["creationflags"] = flags
    elif low_priority:
        kwargs["preexec_fn"] = lambda: os.nice(15)
    return kwargs


# --------------------------------------------------------------------------
# Sonde
# --------------------------------------------------------------------------
@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    audio_streams: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    video_duration: float = 0.0
    audio_duration: float = 0.0
    size: int = 0
    bit_rate: int = 0
    raw: dict = field(default_factory=dict, repr=False)


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def probe(path: str | Path) -> MediaInfo:
    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **_popen_kwargs())
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe a échoué sur {path}: {proc.stderr.strip()[-400:]}")
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    info = MediaInfo(path=str(path), raw=data)
    info.duration = _to_float(fmt.get("duration"))
    info.size = int(_to_float(fmt.get("size")))
    info.bit_rate = int(_to_float(fmt.get("bit_rate")))
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video:
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.video_codec = stream.get("codec_name", "")
            rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
            try:
                info.fps = float(Fraction(rate)) if rate not in ("0/0", "") else 0.0
            except (ValueError, ZeroDivisionError):
                info.fps = 0.0
            if not info.fps:
                try:
                    info.fps = float(Fraction(stream.get("r_frame_rate", "0/1")))
                except (ValueError, ZeroDivisionError):
                    info.fps = 30.0
            info.video_duration = _to_float(stream.get("duration"), info.duration)
            rotation = 0
            for side in stream.get("side_data_list", []) or []:
                if "rotation" in side:
                    rotation = int(_to_float(side["rotation"]))
            if abs(rotation) in (90, 270):
                info.width, info.height = info.height, info.width
        elif kind == "audio":
            info.audio_streams += 1
            if not info.has_audio:
                info.has_audio = True
                info.audio_codec = stream.get("codec_name", "")
                info.audio_duration = _to_float(stream.get("duration"), info.duration)
    if not info.duration:
        info.duration = max(info.video_duration, info.audio_duration)
    return info


# --------------------------------------------------------------------------
# Encodeurs
# --------------------------------------------------------------------------
_ENCODER_CACHE: dict[str, bool] = {}


def encoder_works(codec: str) -> bool:
    """Teste un encodeur par un VRAI encodage de quelques images.

    Lister les encodeurs ne suffit pas : FFmpeg annonce h264_nvenc même sans
    carte NVIDIA ou avec un pilote trop ancien.
    """
    if codec in _ENCODER_CACHE:
        return _ENCODER_CACHE[codec]
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=s=640x360:r=30:d=0.5", "-pix_fmt", "yuv420p",
        "-c:v", codec, "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, **_popen_kwargs())
        ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _ENCODER_CACHE[codec] = ok
    return ok


def nvenc_available() -> bool:
    return encoder_works("h264_nvenc")


BUILTIN_ENCODE_PROFILE = {
    "cpu": {
        "final": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p"],
        "mezzanine": ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
                      "-pix_fmt", "yuv420p"],
    },
    "nvenc": {
        "final": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                  "-cq", "21", "-b:v", "0", "-maxrate", "16M", "-bufsize", "32M",
                  "-pix_fmt", "yuv420p"],
        "mezzanine": ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "constqp", "-qp", "14",
                      "-pix_fmt", "yuv420p"],
    },
}


def resolve_encoder_family(preference: str = "auto") -> str:
    """Retourne 'nvenc' ou 'cpu' selon la préférence et ce qui fonctionne vraiment."""
    if preference == "cpu":
        return "cpu"
    if preference in ("auto", "nvenc") and nvenc_available():
        return "nvenc"
    return "cpu"


# --------------------------------------------------------------------------
# Activité de rendu (le Skills Researcher se met en pause quand elle est active)
# --------------------------------------------------------------------------
class RenderActivity:
    STALE_SECONDS = 90

    def __init__(self, data_dir: Path):
        self.file = Path(data_dir) / "render.active"

    def begin(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(str(os.getpid()), encoding="utf-8")

    def heartbeat(self) -> None:
        try:
            os.utime(self.file, None)
        except OSError:
            pass

    def end(self) -> None:
        try:
            self.file.unlink()
        except OSError:
            pass

    def is_active(self) -> bool:
        try:
            return (time.time() - self.file.stat().st_mtime) < self.STALE_SECONDS
        except OSError:
            return False


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------
def run_ffmpeg(
    args: list[str],
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    low_priority: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> str:
    """Lance ffmpeg ; retourne stderr. Lève FFmpegError en cas d'échec.

    `args` ne contient pas le binaire. `-y` est toujours ajouté : les fichiers
    de sortie visés sont uniquement dans OUTPUT/ ou data/, jamais dans INPUT/.
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *args]
    started = time.time()
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, cwd=cwd,
                                **_popen_kwargs(low_priority))
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    value = line.split("=", 1)[1]
                    if value.lstrip("-").isdigit() and duration and on_progress:
                        seconds = int(value) / 1_000_000
                        on_progress(max(0.0, min(1.0, seconds / duration)))
                if cancel_check and cancel_check():
                    proc.kill()
                    proc.wait()
                    raise Cancelled("ffmpeg interrompu")
                if timeout and time.time() - started > timeout:
                    proc.kill()
                    proc.wait()
                    raise FFmpegError(f"ffmpeg : délai dépassé ({timeout}s)")
            proc.wait()
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg code {proc.returncode}: {stderr.strip()[-1500:]}")
    if on_progress:
        on_progress(1.0)
    return stderr


def run_capture(args: list[str], timeout: float | None = None) -> tuple[int, str]:
    """ffmpeg avec stderr capturé (pour les filtres de mesure : ebur128, blackdetect...)."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **_popen_kwargs())
    return proc.returncode, proc.stderr.decode("utf-8", "replace")
