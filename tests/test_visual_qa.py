"""Contrôle qualité visuel : images brûlées et images floues, sur vidéos à défauts connus."""
from gaming_ai_editor.config import DEFAULTS
from gaming_ai_editor.ffutil import run_ffmpeg
from gaming_ai_editor.qa.visual import exposure, run_visual_qa


def _clip(path, vf: str, seconds: int = 10):
    """Damier en mouvement (plein de détails), avec un défaut appliqué par `vf`."""
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s=360x640:r=30:d={seconds}",
                "-vf", vf or "null", "-c:v", "libx264", "-preset", "ultrafast", str(path)])
    return path


def test_clean_video_passes(tmp_path):
    checks = run_visual_qa(_clip(tmp_path / "net.mp4", ""), {}, DEFAULTS["qa"])
    assert {c["status"] for c in checks} == {"pass"}, checks


def test_burnt_frames_are_refused(tmp_path):
    # 2 s sur 10 quasi blanches (flash trop fort)
    v = _clip(tmp_path / "brule.mp4", "eq=brightness=0.9:enable='between(t,3,5)'")
    exp = exposure(v)
    assert 0.15 <= exp["burnt"] <= 0.25
    status = {c["name"]: c["status"] for c in run_visual_qa(v, {}, DEFAULTS["qa"])}
    assert status["images brûlées (flash, surexposition)"] == "fail"


def test_blurred_windows_are_refused_and_located(tmp_path):
    # 2,5 s de flou fort (traînée, mise au point ratée) au milieu d'une vidéo nette
    v = _clip(tmp_path / "flou.mp4", "gblur=sigma=8:enable='between(t,4,6.5)'")
    exp = exposure(v)
    assert 0.18 <= exp["blurred"] <= 0.32
    assert all(3.9 <= t <= 6.6 for t in exp["blur_times"]), exp["blur_times"]
    checks = {c["name"]: c for c in run_visual_qa(v, {}, DEFAULTS["qa"])}
    assert checks["images floues (effets, traînées)"]["status"] == "fail"
    assert "vers 4" in checks["images floues (effets, traînées)"]["detail"]
