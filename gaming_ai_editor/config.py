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
        "rerank": True,  # réaction, silence puis pic, séries, OCR, doublons, variété
        "rerank_top": 20,  # candidats examinés en détail (images, OCR)
        "ocr": "auto",  # "auto" (si RapidOCR est installé) | "off"
        "ocr_top": 10,  # candidats dont on lit le texte à l'écran (2 images chacun)
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
        "layout": "auto",  # "auto" | "track" | "crop" | "blur" | "hybrid"
        "fg_aspect": 0.8,  # largeur/hauteur de la zone nette en mode hybrid (0.8 = 4:5)
        "transition": "xfade",  # "xfade" | "cut"
        "transition_seconds": 0.25,
        "hook_first": True,
        # "story" : cold open + montée + climax à la fin ; "best_first" : meilleur moment
        # en premier ; "chrono" : ordre chronologique
        "structure": "story",
        "bounds": "adaptive",  # "adaptive" (suit l'action) ou "fixed" (pre_roll/post_roll)
        "lead_min": 0.8, "lead_max": 3.5, "tail_max": 4.0,
        "loop_ending": True,  # fin coupée net pour que la relecture TikTok enchaîne
        "hook_text": "auto",  # "auto" (Claude ou modèle selon le moment) | "off" | "texte fixe"
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
        "bounds": "adaptive",
        "lead_min": 1.5, "lead_max": 8.0, "tail_max": 6.0,
        "jl_cuts": 0.2,  # coupe en J : le son du clip suivant arrive 0,2 s avant l'image
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
        # zooms de caméra dans une image 1,15× plus grande : punch-in net (1.0 = désactivé)
        "supersample": 1.15,
    },
    "style": {
        # Moteur de motion design. "enabled": false = ancien montage simple.
        "enabled": True,
        "channel_name": "",  # affiché dans le bandeau bas (lower third)
        "accent_color": "",  # ex. "#B6F24A" : couleur fixe = identité de chaîne
        "font": "",  # police imposée (sinon choisie automatiquement)
        "long_intensity": 0.5,  # effets plus discrets sur le format long
        "max_ramps_short": 1,
        "max_ramps_long": 2,
        "hud": True,
        "music_generate": True,  # musique générée si assets/music est vide
        "beat_sync": True,
        "grade": True,
        "subscribe_outro": True,
        "slowmo_audio": "keep_pitch",  # "keep_pitch" (hauteur conservée) | "deep" (bullet time)
        "speed_up_dead_time": True,  # accélère le plus long temps mort des clips de montée
        "dead_time_speed": 1.7,
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
        "style": "words",  # "words" / "tiktok" : mot à mot animé | "classic" : ASS simple
        "whisper_model": "small",
        "language": None,
        "font_size": 16,
    },
    "ai": {
        # Garde-fou des coûts : rien de payant sans autorisation explicite.
        "allow_paid_api": False,
        # Gemini : clé API gratuite (Google AI Studio, projet SANS facturation), dans la
        # variable GEMINI_API_KEY ou le fichier gemini_key.txt. Une requête par vidéo.
        "gemini": {"mode": "auto", "command": "gemini", "model": None, "timeout": 240,
                   "free_tier_only": True, "max_requests_per_day": 20},
        "claude": {"mode": "auto", "command": "claude", "model": None, "timeout": 240,
                   "vision": True},  # Claude regarde les images si Gemini n'est pas disponible
        "vision_order": ["gemini", "claude"],  # qui étiquette les moments forts, dans l'ordre
        "vision_top": 12,  # nombre de moments montrés à l'IA
        "frames_per_moment": 3,  # début, pic, fin : l'IA juge l'action, pas une image figée
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
        "max_cycle_minutes": 45,  # un cycle ne peut jamais tourner indéfiniment
        "min_ram_gb": 2.0,
        "experiments": ["encode_profile", "motion_preset", "decode_accel", "scene_detector"],
    },
    "qa": {
        "max_black_ratio": 0.15,
        "max_silence_ratio": 0.5,
        "loudness_tolerance": 3.0,
        "duration_tolerance": 1.0,
    },
    # Profils de jeux pour la lecture du texte à l'écran (facultatif). Exemple :
    # "fortnite": {"match": ["fortnite"], "regions": {"kill_feed": [0.0, 0.55, 0.35, 0.8]},
    #              "keywords": {"kill": ["eliminated"], "victoire": ["victory royale"]}}
    "games": {},
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
