"""Inspection de l'environnement : ce qui est réellement disponible sur cette machine."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .ffutil import _popen_kwargs, encoder_works, ffmpeg_bin, have_ffmpeg


def _run(cmd: list[str], timeout: int = 20) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace", **_popen_kwargs())
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def ram_gb() -> float | None:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullTotalPhys / 1024 ** 3, 1)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except Exception:
        return None
    return None


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "installé"))
    except Exception:
        return None


def _gemini_key(cfg) -> bool:
    from .ai.providers import gemini_api_key

    return bool(gemini_api_key(cfg))


def inspect(cfg=None) -> dict:
    gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader"])
    ffmpeg_version = None
    if have_ffmpeg():
        out = _run([ffmpeg_bin(), "-hide_banner", "-version"])
        ffmpeg_version = out.splitlines()[0] if out else None
    encoders = {}
    if ffmpeg_version:
        for codec in ("libx264", "h264_nvenc", "hevc_nvenc", "av1_nvenc"):
            encoders[codec] = encoder_works(codec)
    root = Path(cfg.root) if cfg else Path.cwd()
    report = {
        "at": time.time(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 10),
        "cpu": platform.processor() or platform.machine(),
        "cpu_threads": os.cpu_count(),
        "ram_gb": ram_gb(),
        "gpu": gpu.splitlines() if gpu else [],
        "ffmpeg": ffmpeg_version,
        "encoders": encoders,
        "render_encoder": "nvenc" if encoders.get("h264_nvenc") else
                          "cpu" if encoders.get("libx264") else None,
        "modules": {name: _module_version(name)
                    for name in ("numpy", "cv2", "PIL", "rapidocr", "playwright", "faster_whisper")},
        "cli": {name: shutil.which(name) for name in ("gemini", "claude")},
        "gemini_api_key": bool(cfg and _gemini_key(cfg)),
        "disk_free_gb": round(shutil.disk_usage(root).free / 1024 ** 3, 1),
    }
    problems = []
    if not report["python_ok"]:
        problems.append("Python 3.10 ou plus récent est requis.")
    if not ffmpeg_version:
        problems.append("FFmpeg introuvable : installe-le (scripts\\install.bat s'en charge) "
                        "et vérifie qu'il est dans le PATH.")
    elif not encoders.get("libx264"):
        problems.append("Cette version de FFmpeg n'a pas libx264 (prendre un build « full »).")
    for mod, label in (("numpy", "NumPy"), ("PIL", "Pillow")):
        if not report["modules"][mod]:
            problems.append(f"{label} manquant : relance scripts\\install.bat "
                            "(ou pip install -r requirements.txt)")
    notes = []
    if ffmpeg_version and not encoders.get("h264_nvenc"):
        notes.append("NVENC inutilisable (pas de GPU NVIDIA détecté par FFmpeg, ou pilote trop "
                     "ancien) : rendu sur CPU.")
    if not report["modules"]["playwright"]:
        notes.append("Playwright absent : animations dessinées par Pillow (moins riches). "
                     "Moteur Chromium : pip install -r requirements-motion.txt puis "
                     "python -m playwright install chromium")
    if not report["modules"]["rapidocr"]:
        notes.append("RapidOCR absent : pas de lecture du texte à l'écran (kill feed, "
                     "VICTORY). Facultatif : pip install -r requirements-ocr.txt")
    if not report["modules"]["cv2"]:
        notes.append("OpenCV absent : analyse visuelle en mode NumPy (fonctionnel, moins fin).")
    if not report["modules"]["faster_whisper"]:
        notes.append("faster-whisper absent : pas de sous-titres automatiques "
                     "(les .srt fournis restent pris en charge).")
    if not report["gemini_api_key"]:
        notes.append("Pas de clé Gemini (GEMINI_API_KEY ou gemini_key.txt) : pas de détection "
                     "des kills, headshots et victoires.")
    if report["disk_free_gb"] < 20:
        notes.append(f"Espace disque faible : {report['disk_free_gb']} Go libres.")
    report["problems"], report["notes"] = problems, notes
    report["ready"] = not problems
    if cfg:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        (cfg.data_dir / "environment.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def format_report(report: dict) -> str:
    lines = [
        "=== GAMING_AI_EDITOR — inspection de l'environnement ===",
        f"Système      : {report['os']}",
        f"Python       : {report['python']} {'OK' if report['python_ok'] else 'TROP ANCIEN'}",
        f"Processeur   : {report['cpu']} ({report['cpu_threads']} threads)",
        f"Mémoire      : {report['ram_gb']} Go",
        f"GPU          : {', '.join(report['gpu']) or 'aucun GPU NVIDIA détecté'}",
        f"FFmpeg       : {report['ffmpeg'] or 'INTROUVABLE'}",
    ]
    for codec, ok in report["encoders"].items():
        lines.append(f"  encodeur {codec:<11}: {'fonctionne' if ok else 'indisponible'}")
    lines.append(f"Rendu via    : {report['render_encoder'] or 'impossible'}")
    for name, version in report["modules"].items():
        lines.append(f"Module {name:<14}: {version or 'absent'}")
    for name, path in report["cli"].items():
        lines.append(f"CLI {name:<9}: {path or 'absent'}")
    lines.append(f"Clé Gemini   : {'détectée' if report.get('gemini_api_key') else 'absente'}")
    lines.append(f"Disque libre : {report['disk_free_gb']} Go")
    for p in report["problems"]:
        lines.append(f"[BLOQUANT] {p}")
    for n in report["notes"]:
        lines.append(f"[info] {n}")
    lines.append("=> PRÊT" if report["ready"] else "=> NON PRÊT : corrige les points bloquants.")
    return "\n".join(lines)
