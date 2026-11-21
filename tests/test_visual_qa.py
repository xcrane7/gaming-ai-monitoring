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


def _plan(src):
    return {"format": "short", "width": 360, "height": 640, "fps": 30,
            "segments": [{"source": str(src), "start": 0.0, "end": 10.0}]}



def _blur_check(render, src):
    qa = {**DEFAULTS["qa"], "blur_check": True}  # expérimental : désactivé par défaut
    return {c["name"]: c for c in run_visual_qa(render, _plan(src), qa)}[
        "images floues ajoutées (effets, traînées)"]


def test_blur_added_by_the_edit_is_refused_and_located(tmp_path):
    src = _clip(tmp_path / "jeu.mp4", "")
    render = _clip(tmp_path / "flou.mp4", "gblur=sigma=8:enable='between(t,4,6.5)'")
    c = _blur_check(render, src)
    assert c["status"] == "warn" and 0.18 <= c["value"] <= 0.32, c
    assert "vers 4" in c["detail"]


def test_game_motion_blur_is_not_blamed_on_the_edit(tmp_path):
    """Le jeu lui-même est flou par moments (rotations rapides) : reprendre ces passages
    tels quels n'est pas un défaut ; un flou EN PLUS l'est."""
    src = _clip(tmp_path / "jeu.mp4", "gblur=sigma=8:enable='between(t,4,6.5)'")
    assert _blur_check(src, src)["status"] == "pass"
    worse = _clip(tmp_path / "pire.mp4",
                  "gblur=sigma=8:enable='between(t,4,6.5)+between(t,0.5,3)'")
    assert _blur_check(worse, src)["status"] == "warn"


def test_global_sharpening_and_particles_are_not_blur(tmp_path):
    """Ce qui touche toute la vidéo (accentuation, grain) ne fausse pas la comparaison."""
    src = _clip(tmp_path / "jeu.mp4", "")
    render = _clip(tmp_path / "net.mp4", "unsharp=5:5:1.2,noise=alls=12:allf=t")
    assert _blur_check(render, src)["status"] == "pass"


def test_a_single_white_frame_in_the_hook_is_refused(tmp_path):
    """Une seule image blanche (fondu au blanc) pendant les 3 premières secondes : refus,
    même si elle pèse moins de 6 % de la vidéo (cas vu sur le rendu réel à 1,5 s)."""
    v = _clip(tmp_path / "accroche.mp4", "eq=brightness=0.9:enable='between(t,1.4,1.6)'")
    c = {x["name"]: x for x in run_visual_qa(v, {}, DEFAULTS["qa"])}
    assert c["images brûlées (flash, surexposition)"]["status"] == "fail"
    assert "accroche à 1.4" in c["images brûlées (flash, surexposition)"]["detail"]
    late = _clip(tmp_path / "tard.mp4", "eq=brightness=0.9:enable='between(t,7.4,7.6)'")
    c = {x["name"]: x for x in run_visual_qa(late, {}, DEFAULTS["qa"])}
    assert c["images brûlées (flash, surexposition)"]["status"] != "fail"
