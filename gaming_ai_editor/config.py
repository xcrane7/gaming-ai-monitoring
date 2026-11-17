"""Configuration : valeurs par défaut + surcharge par config.json.

Le dossier racine est celui du projet, ou la variable d'environnement GAE_HOME.
Aucune valeur par défaut n'active un service payant.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v"}

DEFAULTS: dict[str, Any] = {
    "paths": {
        "input": "INPUT",
        "output": "OUTPUT",
        "data": "data",
        "music": "assets/music",
        "sfx": "assets/sfx",
    },
    "watcher": {
        "poll_seconds": 5,
        # Un fichier est considéré complet quand sa taille n'a pas bougé pendant ce délai
        # (évite de traiter un enregistrement OBS / ShadowPlay encore en cours).
        "stable_seconds": 10,
    },
    "analysis": {
        "window_seconds": 0.5,
        "video_fps": 4,
        "video_width": 160,
        "video_height": 90,
        "audio_rate": 16000,
        "hwaccel": "auto",  # "auto" | "none"
        "weights": {"audio": 0.45, "flux": 0.2, "motion": 0.35},
        "smooth_seconds": 3.0,
        "peak_percentile": 80,
        "peak_min_distance": 6.0,
        "max_highlights": 120,
    },
    "short": {
        "enabled": True,
        "width": 1080,
        "height": 1920,
        "min_seconds": 30,
        "max_seconds": 60,
        "target_seconds": 45,
        "clip_min": 4.0,
        "clip_max": 14.0,
        "pre_roll": 4.0,
        "post_roll": 3.0,
        "layout": "hybrid",  # "crop" | "blur" | "hybrid"
        "fg_aspect": 0.8,  # largeur/hauteur de la zone nette en mode hybrid (0.8 = 4:5)
        "transition": "xfade",  # "xfade" | "cut"
        "transition_seconds": 0.25,
        "hook_first": True,
    },
    "long": {
        "enabled": True,
        "width": 1920,
        "height": 1080,
        "min_seconds": 300,
        "max_seconds": 720,
        "target_seconds": 480,
        "clip_min": 12.0,
        "clip_max": 75.0,
        "pre_roll": 10.0,
        "post_roll": 8.0,
        "merge_gap": 4.0,
        "transition": "cut",
        "transition_seconds": 0.4,
    },
    "render": {
        "max_fps": 60,
        "encoder": "auto",  # "auto" | "nvenc" | "cpu"
        "audio_bitrate": "192k",
        "loudness_lufs": -14.0,
        "audio_tracks": "first",  # "first" | "mix" (OBS multi-pistes)
        "keep_work_files": False,
    },
    "music": {
        "enabled": True,  # utilisée seulement si assets/music contient des fichiers
        "volume_db": -20.0,
        "ducking": True,
    },
    "sfx": {
        "enabled": True,  # whoosh sur les transitions si assets/sfx/whoosh.* existe
        "volume_db": -9.0,
    },
    "subtitles": {
        # "auto" : .srt à côté de la vidéo source s'il existe, sinon Whisper local
        # (faster-whisper) s'il est installé, sinon aucun sous-titre.
        "mode": "auto",  # "auto" | "sidecar" | "whisper" | "off"
        "whisper_model": "small",
        "language": None,
        "font_size": 16,
    },
    "ai": {
        # Garde-fou des coûts : rien de payant sans autorisation explicite.
        "allow_paid_api": False,
        "gemini": {"mode": "auto", "command": "gemini", "model": None, "timeout": 240},
        "claude": {"mode": "auto", "command": "claude", "model": None, "timeout": 240},
        # mode : "auto" (CLI de l'abonnement si présent) | "cli" | "api" | "off"
    },
    "research": {
        "enabled": True,
        "interval_hours": 6,
        "startup_delay_seconds": 120,
        "allow_network": True,
        "allow_package_install": True,
        "auto_integrate": True,
        "min_speed_gain": 1.10,
        "max_quality_loss": 0.003,  # perte SSIM tolérée
        "max_size_growth": 1.15,
        "license_allowlist": [
            "MIT", "BSD", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "Apache",
            "ISC", "MPL-2.0", "LGPL", "Unlicense", "Zlib", "PSF",
        ],
        "sandbox_timeout": 600,
    },
    "qa": {
        "max_black_ratio": 0.15,
        "max_silence_ratio": 0.5,
        "loudness_tolerance": 3.0,
        "duration_tolerance": 1.0,
    },
    "dashboard": {"host": "127.0.0.1", "port": 8765},
}


def project_root() -> Path:
    env = os.environ.get("GAE_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    def __init__(self, root: Path | None = None, overrides: dict | None = None):
        self.root = Path(root).resolve() if root else project_root()
        self.data = copy.deepcopy(DEFAULTS)
        self.config_file = self.root / "config.json"
        if self.config_file.exists():
            try:
                _merge(self.data, json.loads(self.config_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as exc:
                raise SystemExit(f"config.json illisible : {exc}")
        if overrides:
            _merge(self.data, overrides)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, name: str) -> Path:
        p = Path(self.data["paths"][name])
        return p if p.is_absolute() else self.root / p

    @property
    def input_dir(self) -> Path:
        return self.path("input")

    @property
    def output_dir(self) -> Path:
        return self.path("output")

    @property
    def data_dir(self) -> Path:
        return self.path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gae.sqlite3"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    def ensure_dirs(self) -> None:
        for p in (
            self.input_dir,
            self.output_dir / "shorts",
            self.output_dir / "youtube",
            self.output_dir / "reports",
            self.data_dir,
            self.work_dir,
            self.data_dir / "analysis",
            self.path("music"),
            self.path("sfx"),
        ):
            p.mkdir(parents=True, exist_ok=True)
