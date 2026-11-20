"""Surveillance des ressources : RAM, VRAM, rendu en cours.

Utilisé par la file de rendu (un seul rendu lourd à la fois, attente si la
mémoire manque) et par le Skills Researcher (pause pendant les rendus, arrêt
si la machine est saturée, durée de cycle limitée).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

from .ffutil import _popen_kwargs


def ram_available_gb() -> float | None:
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
            return round(status.ullAvailPhys / 1024 ** 3, 2)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    return round(int(line.split()[1]) / 1024 ** 2, 2)
    except Exception:
        return None
    return None


def vram() -> dict | None:
    """{'used_gb', 'total_gb', 'free_gb'} de la carte NVIDIA, ou None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=10, **_popen_kwargs()).stdout.strip().splitlines()
        used, total = (float(x) / 1024 for x in out[0].split(","))
        return {"used_gb": round(used, 2), "total_gb": round(total, 2),
                "free_gb": round(total - used, 2)}
    except Exception:
        return None


def snapshot() -> dict:
    return {"ram_available_gb": ram_available_gb(), "vram": vram(), "at": time.time()}


def has_room(min_ram_gb: float = 2.0, min_vram_gb: float = 1.0) -> tuple[bool, str]:
    ram = ram_available_gb()
    if ram is not None and ram < min_ram_gb:
        return False, f"mémoire libre {ram} Go < {min_ram_gb} Go"
    v = vram()
    if v is not None and v["free_gb"] < min_vram_gb:
        return False, f"mémoire vidéo libre {v['free_gb']} Go < {min_vram_gb} Go"
    return True, "ok"


def wait_for_room(min_ram_gb: float = 2.0, min_vram_gb: float = 1.0, timeout: float = 600,
                  stop=None, log=None) -> bool:
    """Attend que la machine ait assez de mémoire libre. False si le délai expire."""
    deadline = time.time() + timeout
    announced = False
    while time.time() < deadline:
        ok, why = has_room(min_ram_gb, min_vram_gb)
        if ok:
            return True
        if log and not announced:
            log(f"Ressources insuffisantes ({why}) : attente.")
            announced = True
        if stop is not None and stop.wait(5):
            return False
        if stop is None:
            time.sleep(5)
    return False
