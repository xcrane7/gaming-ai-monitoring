"""Fixtures : dossiers temporaires et gameplays synthétiques (vérité terrain connue)."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gaming_ai_editor import synth  # noqa: E402
from gaming_ai_editor.config import Config  # noqa: E402
from gaming_ai_editor.ffutil import have_ffmpeg  # noqa: E402

if not have_ffmpeg():
    pytest.exit("FFmpeg est requis pour les tests.", returncode=2)

TEST_OVERRIDES = {
    "short": {"width": 360, "height": 640},
    "long": {"width": 640, "height": 360},
    "ai": {"gemini": {"mode": "off"}, "claude": {"mode": "off"}},
    "watcher": {"stable_seconds": 10, "poll_seconds": 1},
    "research": {"reference": {"seconds": 3, "width": 480, "height": 270, "fps": 30},
                 "measure_passes": 1, "candidates_per_cycle": 2, "startup_delay_seconds": 120},
}


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    """Clips générés une seule fois pour toute la session de tests."""
    root = tmp_path_factory.mktemp("media")
    clips = {}
    clips["short_truth"] = synth.make_gameplay(root / "match.mp4", duration=60, width=640,
                                               height=360, fps=30)
    clips["short"] = root / "match.mp4"
    clips["long_truth"] = synth.make_gameplay(root / "session.mp4", duration=660, width=320,
                                              height=180, fps=15, cut_every=60)
    clips["long"] = root / "session.mp4"
    synth.make_gameplay(root / "muet.mp4", duration=40, width=640, height=360, fps=30,
                        audio=False)
    clips["silent"] = root / "muet.mp4"
    synth.make_gameplay(root / "carre.mp4", duration=40, width=480, height=480, fps=25)
    clips["square"] = root / "carre.mp4"
    return clips


@pytest.fixture
def cfg(tmp_path):
    config = Config(root=tmp_path / "home", overrides=TEST_OVERRIDES)
    config.ensure_dirs()
    return config


@pytest.fixture
def orch(cfg):
    from gaming_ai_editor.pipeline import Orchestrator

    o = Orchestrator(cfg)
    yield o
    o.db.close()


def copy_in(cfg, src: Path, name: str | None = None) -> Path:
    dst = cfg.input_dir / (name or src.name)
    shutil.copy(src, dst)
    return dst
