"""Moteur de motion design Chromium : transparence réelle, timing, textes jamais coupés,
cinq styles distincts, repli Pillow. Vérifié sur les images produites, pas sur le code."""
import numpy as np
import pytest

from gaming_ai_editor.engines import graphics, web_motion
from gaming_ai_editor.engines.web_motion import STYLES, WebMotion, chromium_path

pytestmark = pytest.mark.skipif(not web_motion.available(), reason="Playwright non installé")

W, H = 360, 640
PAL = {"accent": "#FFC531", "accent2": "#9B5CFF", "text": "#FFFFFF"}
ITEMS = [
    {"type": "hook", "start": 0.0, "end": 2.0, "text": "Personne ne l'avait vu venir là"},
    {"type": "word", "start": 2.2, "end": 3.2, "text": "DÉFLAGRATION", "size": 150, "style": "slam"},
    {"type": "focus_ring", "start": 3.4, "end": 4.3, "x": 0.5, "y": 0.42},
    {"type": "notify", "start": 3.5, "end": 5.0, "text": "ÉLIMINATION", "slot": 0},
    {"type": "combo", "start": 5.2, "end": 6.8, "hits": [5.2, 5.6, 6.0], "text": "SÉRIE"},
    {"type": "headshot", "start": 7.0, "end": 8.2},
    {"type": "victory", "start": 8.4, "end": 10.0, "text": "VICTOIRE"},
    {"type": "climax", "start": 10.2, "end": 11.5},
]


def alpha(img):
    return np.asarray(img)[..., 3]


@pytest.fixture(scope="module", params=sorted(STYLES))
def engine(request):
    eng = WebMotion(ITEMS, W, H, PAL, request.param, chromium_path()).__enter__()
    yield eng
    eng.close()


def test_real_transparency_and_nothing_outside_windows(engine):
    assert engine.frame(11.9) is None, "aucun élément actif : aucune capture"
    f = engine.frame(1.0)
    a = alpha(f)
    assert f.mode == "RGBA" and a.min() == 0 and a.max() == 255
    assert 0.002 < (a > 0).mean() < 0.4, "texte opaque sur fond réellement transparent"


def test_every_component_is_invisible_before_its_start(engine):
    """Défaut constaté puis corrigé : la cible apparaissait dès la première image."""
    only = {it["type"]: it for it in ITEMS}
    for kind in ("focus_ring", "combo", "headshot", "victory", "climax"):
        eng = WebMotion([only[kind]], W, H, PAL, engine.style_name, chromium_path()).__enter__()
        try:
            eng.items[0]["start"] -= 0.5  # force une capture 0,4 s avant le début réel
            img = eng.frame(only[kind]["start"] - 0.4)
            assert img is None or alpha(img).max() == 0, kind
        finally:
            eng.close()


def test_animation_moves_between_frames(engine):
    """Une vraie animation : l'image change pendant l'entrée, puis se stabilise."""
    a0, a1, a2 = (alpha(engine.frame(t)).astype(int) for t in (2.22, 2.32, 2.9))
    assert np.abs(a1 - a0).mean() > 0.5, "l'entrée du mot bouge"
    assert (a2 > 0).mean() > 0.001


@pytest.mark.parametrize("t", [1.5, 2.9, 4.2, 6.5, 7.9, 9.5])
def test_text_is_never_cut_at_the_frame_edges(engine, t):
    """Une fois l'entrée finie, aucun texte ne touche les bords de l'image."""
    a = alpha(engine.frame(t))
    cols = np.where(a.max(axis=0) > 0)[0]
    if engine.style_name and t == 9.5:  # la bande de victoire traverse l'écran exprès
        return
    assert cols.size and cols.min() > 1 and cols.max() < W - 2, (engine.style_name, t)


def test_five_styles_look_different():
    frames = {}
    for name in STYLES:
        eng = WebMotion(ITEMS[:2], W, H, PAL, name, chromium_path()).__enter__()
        try:
            frames[name] = alpha(eng.frame(1.6)).astype(int)
        finally:
            eng.close()
    names = sorted(frames)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert np.abs(frames[a] - frames[b]).mean() > 1.0, (a, b)


def test_hybrid_track_merges_chromium_and_pillow(tmp_path):
    ctx = graphics.Context(W, H, 30, 3.0, palette=PAL)
    timeline = [{"type": "hook", "start": 0.0, "end": 2.0, "text": "Regarde ça"},
                {"type": "progress", "start": 0.0, "end": 3.0}]
    stats = graphics.render_track(timeline, ctx, tmp_path / "g.mov", web={"style": "epic"})
    assert stats["web_engine"] == "chromium" and 55 <= stats["web_frames"] <= 62
    assert stats["frames_with_graphics"] == 90, "Pillow (barre) + Chromium (accroche)"


def test_falls_back_to_pillow_without_chromium(tmp_path, monkeypatch):
    monkeypatch.setattr(web_motion, "available", lambda: False)
    ctx = graphics.Context(W, H, 30, 2.0, palette=PAL)
    stats = graphics.render_track([{"type": "hook", "start": 0.0, "end": 1.5, "text": "Repli"}],
                                  ctx, tmp_path / "g.mov", web={"style": "clean"})
    assert stats["web_engine"] == "Playwright absent" and stats["frames_with_graphics"] > 30


def test_style_follows_config_or_video_energy():
    cfg = {"motion": {"style": "cinematic"}}
    assert web_motion.choose_style(cfg, {"energy": 0.99}) == "cinematic"
    auto = {"motion": {"style": "auto"}}
    assert web_motion.choose_style(auto, {"energy": 0.95, "transitions": "smooth"}) == "epic"
    assert web_motion.choose_style(auto, {"energy": 0.8, "transitions": "aggressive"}) == "aggressive"
