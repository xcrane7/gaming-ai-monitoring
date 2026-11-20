"""Accroche, légendes et boucle (skill shorts-hook-captions)."""
import numpy as np
import pytest

from gaming_ai_editor import synth
from gaming_ai_editor.editor import subtitles as subs
from gaming_ai_editor.editor.plan import EditPlan, Segment, build_plan
from gaming_ai_editor.editor.render import Renderer
from gaming_ai_editor.engines import editing, graphics
from gaming_ai_editor.ffutil import probe
from test_engines import _fake_analysis

TEXT_TYPES = ("hook", "word", "captions", "notify", "combo", "headshot", "victory")


def test_no_text_in_the_tiktok_interface_zones():
    """Aucun texte sous les onglets, sous la description ni sous la colonne de boutons."""
    ctx = graphics.Context(540, 960, 30, 4.0)
    words = subs.words_from_cues([subs.Cue(0.0, 4.0, "Attention il arrive par la gauche "
                                                     "avec 3 grenades NON!!")])
    long_word = "Dernière déflagration du siècle"
    items = [
        {"type": "hook", "start": 0, "end": 4, "text": "Personne ne l'a vu venir ce coup là"},
        {"type": "captions", "start": 0, "end": 4, "words": words},
        {"type": "word", "start": 0, "end": 4, "text": long_word, "style": "pop", "angle": -6,
         "pos": [0.47, 0.56], "size": 150},
        {"type": "notify", "start": 0, "end": 4, "text": "Élimination", "icon": "skull",
         "slot": 2},
        {"type": "combo", "start": 0, "end": 4, "hits": [0.1, 0.3, 0.5]},
    ]
    for item in items:
        for t in (0.6, 1.5, 3.0):
            frame = graphics.render_frame(graphics.build([item], ctx), ctx, t)
            if frame is None:
                continue
            alpha = np.asarray(frame)[..., 3] > 40
            h, w = alpha.shape
            assert not alpha[: int(graphics.SAFE_TOP * h)].any(), (item["type"], "haut")
            assert not alpha[int(graphics.SAFE_BOTTOM * h):].any(), (item["type"], "bas")
            x0, y0, y1 = graphics.SAFE_BUTTONS
            assert not alpha[int(y0 * h):int(y1 * h), int(x0 * w):].any(), (item["type"], "boutons")


def test_caption_chunks_keywords_and_impact_silence():
    words = subs.words_from_cues([subs.Cue(1.0, 4.0, "il en reste 3 et il crie NON!!")])
    caps = graphics.Captions({"type": "captions", "start": 1, "end": 4, "words": words,
                              "mute": [[2.0, 2.3]]}, graphics.Context(540, 960, 30, 5))
    assert all(len(c) <= 4 and len(" ".join(w["w"] for w in c)) <= 22 for c in caps.chunks)
    assert graphics.Captions.is_keyword("3") and graphics.Captions.is_keyword("NON!!")
    assert not graphics.Captions.is_keyword("reste")
    ctx = caps.ctx
    assert graphics.render_frame([caps], ctx, 2.1) is None or \
        np.asarray(graphics.render_frame([caps], ctx, 2.1))[..., 3].max() == 0
    assert np.asarray(graphics.render_frame([caps], ctx, 1.5))[..., 3].max() > 0


def _directed(cfg, analysis, key="k"):
    plan = build_plan("short", [analysis], cfg)
    editing.direct(plan, [analysis], cfg, None, key, None)
    return plan


def test_hook_follows_the_best_moment_and_the_settings(cfg):
    a = _fake_analysis("/x/v.mp4")
    a["highlights"][0].update({"source": "claude", "label": "fail"})
    plan = _directed(cfg, a)
    assert plan.hook in editing.HOOK_TEMPLATES["fail"], plan.hook
    cfg.data["short"]["hook_text"] = "1 contre 4…"
    assert _directed(cfg, a).hook == "1 contre 4…"
    cfg.data["short"]["hook_text"] = "off"
    plan = _directed(cfg, a)
    assert plan.hook == "" and not any(g["type"] == "hook" for g in plan.graphics)


def test_no_impact_word_while_the_hook_is_on_screen(cfg):
    plan = _directed(cfg, _fake_analysis("/x/v.mp4"))
    hook = next(g for g in plan.graphics if g["type"] == "hook")
    assert not any(g["type"] == "word" and g["start"] < hook["end"] for g in plan.graphics)


def test_one_event_text_at_a_time_and_one_word_per_clip(cfg):
    a = _fake_analysis("/x/v.mp4")
    a["highlights"][0].update({"source": "claude", "label": "kill"})
    plan = _directed(cfg, a)
    texts = [g for g in plan.graphics if g["type"] in editing.EVENT_PRIORITY]
    for i, x in enumerate(texts):
        for y in texts[i + 1:]:
            assert x["end"] <= y["start"] or y["end"] <= x["start"], (x, y)
    offsets = plan.offsets()
    for seg, off in zip(plan.segments, offsets):
        words = [g for g in plan.graphics if g["type"] == "word"
                 and off <= g["start"] < off + seg.out_duration]
        assert len(words) <= 1


def test_loop_ends_on_a_beat_and_brings_back_the_hook(cfg):
    plan = _directed(cfg, _fake_analysis("/x/v.mp4"))
    beat = 60.0 / plan.music["bpm"]
    assert plan.loop
    assert abs(plan.duration / beat - round(plan.duration / beat)) * beat < 0.01
    hooks = [g for g in plan.graphics if g["type"] == "hook"]
    assert len(hooks) == 2 and hooks[1].get("reprise") and hooks[1]["end"] == plan.duration
    cfg.data["short"]["loop_ending"] = False
    plan = _directed(cfg, _fake_analysis("/x/v.mp4"))
    assert len([g for g in plan.graphics if g["type"] == "hook"]) == 1


def test_classic_subtitles_still_use_a_valid_ass_file(cfg, tmp_path):
    src = tmp_path / "s.mp4"
    synth.make_gameplay(src, duration=12, width=480, height=270, fps=30, cut_every=None,
                        bursts=[(4, 4.6)], noise=30)
    from gaming_ai_editor.analysis import analyze_video

    a = analyze_video(src, cfg["analysis"])
    cfg.data["subtitles"]["style"] = "classic"
    cfg.data["render"]["keep_work_files"] = True
    plan = EditPlan("short", 360, 640, 30, segments=[Segment(str(src), 1, 9, 1, "", 4.3)])
    editing.direct(plan, [a], cfg, None, "ass", None)
    cues = {str(src): [subs.Cue(2.0, 4.0, "Regarde bien ça")]}
    res = Renderer(cfg).render(plan, cfg.output_dir / "ass.mp4", cfg.work_dir / "ass",
                               cues_by_source=cues)
    assert res["subtitles"] == "1 répliques"
    ass = (cfg.work_dir / "ass" / "subs.ass").read_text(encoding="utf-8")
    assert "[Events]" in ass and ass.count("Dialogue:") == 1
    assert probe(res["path"]).duration == pytest.approx(plan.duration, abs=0.15)
