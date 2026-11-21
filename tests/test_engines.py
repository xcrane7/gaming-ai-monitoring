"""Tests des moteurs de montage créatif (engines/)."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from gaming_ai_editor import synth
from gaming_ai_editor.editor.plan import EditPlan, Segment, build_plan
from gaming_ai_editor.editor.render import Renderer
from gaming_ai_editor.engines import audio, color, editing, graphics, intelligence, motion
from gaming_ai_editor.ffutil import probe, run_ffmpeg
from gaming_ai_editor.qa.checks import run_qa
from gaming_ai_editor.research.registry import (ACTIVE, QUARANTINE, VALIDATED, SkillRegistry)


# -- 3. Motion ------------------------------------------------------------------------
def test_easing_curves_start_and_end_where_expected():
    for name, f in motion.EASINGS.items():
        assert f(0.0) == pytest.approx(0.0, abs=1e-9), name
        assert f(1.0) == pytest.approx(1.0, abs=1e-9), name
    assert motion.ease_out_back(0.7) > 1.0  # dépassement : effet « rebond »
    assert motion.tween(0.5, 0, 1, 10, 20, "linear") == pytest.approx(15)


def test_speed_ramp_is_continuous_and_consistent():
    r = motion.SpeedRamp(1.0, 2.0, 0.35)
    assert r.speed(0.5) == 1.0 and r.speed(1.5) == pytest.approx(0.35)
    assert r.speed(1.02) > 0.35  # ralentissement progressif, pas brutal
    xs = [r.map(x / 100) for x in range(0, 400)]
    assert all(b >= a for a, b in zip(xs, xs[1:])), "le temps ne recule jamais"
    assert r.map(3.0) == pytest.approx(3.0 + r.extra, abs=1e-6)
    assert 1.0 < r.extra < 1.9
    assert r.audio_rate() == pytest.approx(1.0 / (1.0 + r.extra), abs=1e-6)


def test_speed_ramp_renders_to_the_expected_duration(tmp_path):
    src = tmp_path / "s.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4", "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", str(src)])
    r = motion.SpeedRamp(1.0, 2.0, 0.35)
    out = tmp_path / "o.mp4"
    run_ffmpeg(["-i", str(src), "-vf", f"setpts='{r.setpts_expr()}',framerate=fps=30",
                "-c:v", "libx264", "-preset", "ultrafast", str(out)])
    assert probe(out).duration == pytest.approx(4.0 + r.extra, abs=0.1)


def test_camera_expression_is_finite_long_before_an_impact():
    expr = motion.expr_punch([30.0], 0.1)
    assert "max(t-30,0)" in expr  # aucun exp() géant avant l'impact
    f = motion.camera_filters(motion.CameraMove(5, punches=[(1, 1)], shakes=[(1, 1)],
                                                 rotate=True), 360, 640)
    assert f.startswith("scale=") and "crop=360:640" in f and "rotate=" in f


# -- 5. Color -------------------------------------------------------------------------
def test_luts_are_valid_and_distinct(tmp_path):
    tables = {}
    for look in color.LOOKS:
        cube = color.write_cube(tmp_path / f"{look}.cube", look, size=9)
        lines = cube.read_text().splitlines()
        values = np.array([[float(v) for v in ln.split()] for ln in lines[4:]])
        assert values.shape == (9 ** 3, 3) and values.min() >= 0 and values.max() <= 1
        tables[look] = values
    looks = list(tables)
    for i in range(len(looks)):
        for j in range(i + 1, len(looks)):
            assert np.abs(tables[looks[i]] - tables[looks[j]]).mean() > 0.005
    assert color.choose_look({"hue": 0.8, "saturation": 0.5, "luma": 0.2}) == "neon_night"
    assert color.choose_look({"hue": 0.3, "saturation": 0.1, "luma": 0.5}) == "punchy"


# -- 6. Audio -------------------------------------------------------------------------
@pytest.mark.parametrize("bpm", [100, 128, 150])
def test_beat_detection_finds_tempo_and_phase(tmp_path, bpm):
    m = audio.generate_music(tmp_path / "m.wav", 30, bpm=bpm, drop_at=12.0, seed=bpm)
    assert any(abs(d - 12.0) < 1e-3 for d in m["downbeats"]), "le drop tombe sur une mesure"
    d = audio.detect_beats(tmp_path / "m.wav")
    assert d["bpm"] == pytest.approx(bpm, abs=0.6)
    errs = [min(abs(x - b) for b in m["beats"]) for x in d["beats"][1:-1]]
    assert max(errs) < 0.04


def test_sfx_track_places_sounds_on_time(tmp_path):
    path = audio.render_sfx_track([{"t": 1.5, "kind": "impact"}, {"t": 3.0, "kind": "boom"}],
                                  5.0, tmp_path / "sfx.wav")
    y = audio.load_mono(path, 16000)
    env = np.abs(y)
    first = np.argmax(env > 0.2) / 16000
    assert first == pytest.approx(1.5, abs=0.02)
    for kind in audio.SFX_KINDS:
        x = audio.sfx(kind, 1)
        assert x.size > 100 and np.isfinite(x).all() and np.abs(x).max() <= 1.0


# -- 1. Intelligence ------------------------------------------------------------------
def test_intelligence_finds_events_and_menus(tmp_path):
    from gaming_ai_editor.config import DEFAULTS
    from gaming_ai_editor.analysis import analyze_video

    game = tmp_path / "g.mp4"
    truth = synth.make_gameplay(game, duration=40, width=480, height=270, fps=30,
                                bursts=[(10, 11), (25, 26)], cut_every=None, noise=40)
    menu = tmp_path / "menu.mp4"  # écran fixe avec un petit élément animé : un menu
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x202030:s=480x270:r=30:d=12", "-f", "lavfi",
                "-i", "testsrc2=s=60x40:r=30:d=12", "-f", "lavfi", "-i", "sine=f=200:d=12",
                "-filter_complex", "[0][1]overlay=210:115[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(menu)])
    both = tmp_path / "both.mp4"
    run_ffmpeg(["-i", str(menu), "-i", str(game), "-filter_complex",
                "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(both)])
    a = analyze_video(both, DEFAULTS["analysis"])
    assert a["ui_windows"] and a["ui_windows"][0][0] < 1 and 10 <= a["ui_windows"][0][1] <= 13.5
    times = [e["t"] for e in a["events"]]
    for s, e in truth["bursts"]:
        assert any(12 + s - 0.3 <= t <= 12 + e + 0.3 for t in times), (s, times)
    assert not any(t < 11 for t in times), "aucun événement dans le menu"
    assert a["color"]["luma"] > 0 and a["focus"]["x"]
    json.dumps(a)


# -- 7. Graphics ----------------------------------------------------------------------
def test_long_impact_word_stays_inside_the_frame():
    ctx = graphics.Context(360, 640, 30, 3.0)
    els = graphics.build([{"type": "word", "start": 0, "end": 2, "text": "Dernière déflagration",
                           "style": "slam", "size": 150, "angle": -6}], ctx)
    alpha = np.asarray(graphics.render_frame(els, ctx, 1.0))[..., 3]
    cols = np.where(alpha.max(axis=0) > 0)[0]
    assert cols.min() > 0 and cols.max() < 359, "le mot ne doit pas sortir de l'image"


def test_every_graphic_element_renders(tmp_path):
    ctx = graphics.Context(360, 640, 30, 3.0, beats=[0.5, 1.0, 1.5], clips=[0, 1.5])
    timeline = [{"type": t, "start": 0.2, "end": 2.8} for t in
                ("hud", "progress", "combo", "victory", "headshot", "embers", "subscribe")]
    timeline += [
        {"type": "hook", "start": 0.0, "end": 2.0, "text": "Regarde bien ça"},
        {"type": "word", "start": 0.5, "end": 1.5, "text": "BOOM", "style": "slam"},
        {"type": "notify", "start": 0.5, "end": 2.5, "text": "Explosion", "icon": "bolt"},
        {"type": "title", "start": 0.0, "end": 2.0, "text": "Titre", "subtitle": "Sous"},
        {"type": "lower_third", "start": 0.0, "end": 2.5, "title": "Chaîne", "subtitle": "Jeu"},
        {"type": "scoreboard", "start": 0.0, "end": 2.0, "index": 1, "total": 3, "score": 0.7},
        {"type": "sparks", "start": 1.0, "x": 0.5, "y": 0.5},
        {"type": "shockwave", "start": 1.0, "x": 0.5, "y": 0.5},
        {"type": "explosion", "start": 1.2, "x": 0.5, "y": 0.5},
        {"type": "focus_ring", "start": 1.0, "x": 0.5, "y": 0.45},
        {"type": "climax", "start": 0.2, "end": 2.8},
        {"type": "hitmarker", "start": 1.0, "end": 1.45, "x": 0.5, "y": 0.45},
        {"type": "captions", "start": 0.2, "end": 2.6,
         "words": [{"t0": 0.2, "t1": 0.9, "w": "Regarde", "line": 0},
                   {"t0": 0.9, "t1": 2.6, "w": "ça", "line": 0}]},
    ]
    timeline[2]["hits"] = [0.3, 0.6, 0.9]
    assert set(graphics.ELEMENTS) <= {i["type"] for i in timeline}
    for t in (0.3, 1.0, 1.3, 2.0):
        frame = graphics.render_frame(graphics.build(timeline, ctx), ctx, t)
        alpha = np.asarray(frame)[..., 3]
        assert alpha.max() == 255 and 0.01 < (alpha > 0).mean() < 0.95
    stats = graphics.render_track(timeline, ctx, tmp_path / "g.mov")
    # tout s'arrête à 2,8 s : les 5 dernières images sont vides (et encodées comme telles)
    assert stats["frames"] == 90 and stats["frames_with_graphics"] == 85
    info = probe(tmp_path / "g.mov")
    assert (info.width, info.height) == (360, 640)


# -- 2. Editing -----------------------------------------------------------------------
def _fake_analysis(path, hue=0.6, epm=20, events=None):
    return {"path": str(path), "duration": 60.0, "fps": 30, "has_audio": True,
            "highlights": [{"start": 8, "end": 12, "peak": 10, "score": 3, "label": "action"},
                           {"start": 28, "end": 32, "peak": 30, "score": 2.5, "label": "action"},
                           {"start": 48, "end": 52, "peak": 50, "score": 2, "label": "action"}],
            "events": events or [{"t": 10.0, "kind": "explosion", "strength": 0.9},
                                 {"t": 10.6, "kind": "impact", "strength": 0.6},
                                 {"t": 30.0, "kind": "impact", "strength": 0.7},
                                 {"t": 31.5, "kind": "shot", "strength": 0.5},
                                 {"t": 50.0, "kind": "burst", "strength": 0.8, "end": 51,
                                  "count": 3}],
            "color": {"hue": hue, "saturation": 0.35, "luma": 0.4},
            "pace": {"events_per_minute": epm, "motion_mean": 0.08},
            "focus": {"step": 0.5, "x": [0.5] * 120, "spread": 0.02}, "ui_windows": [],
            "scene_cuts": []}


def test_director_adapts_effects_to_events(cfg):
    a = _fake_analysis("/x/video.mp4")
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "clé", None)
    fx = {round(e["t"], 1): e["fx"] for s in plan.segments for e in s.events}
    assert "explosion_gfx" in fx[10.0] and "sfx_boom" in fx[10.0]
    assert "combo" in fx[50.0]
    assert sum(1 for s in plan.segments if s.ramp) == 1, "un seul ralenti en format court"
    for s in plan.segments:  # jamais deux effets lourds à moins de 1,5 s
        heavy = sorted(e["t"] for e in s.events if {"shake", "glitch", "distortion"} & set(e["fx"])
                       and "climax" not in e["fx"])
        assert all(b - a >= 1.5 for a, b in zip(heavy, heavy[1:]))
    beat = 60.0 / plan.music["bpm"]
    for j in plan.junctions():  # coupes sur le tempo
        assert abs(j / beat - round(j / beat)) * beat < 0.02
    assert plan.music.get("drop_at") and abs(plan.music["drop_at"] / beat
                                             - round(plan.music["drop_at"] / beat)) < 1e-3
    kinds = {g["type"] for g in plan.graphics}
    assert {"hook", "progress", "explosion", "word", "combo", "focus_ring"} <= kinds
    assert "hud" not in kinds, "HUD caméscope désactivé par défaut"
    assert all(g["text"] == g["text"].upper() for g in plan.graphics if g["type"] == "word")
    assert {c["kind"] for c in plan.sfx} >= {"boom", "whoosh", "riser"}


def test_two_videos_do_not_get_the_same_template(cfg):
    a1 = _fake_analysis("/x/a.mp4", hue=0.6)
    a2 = _fake_analysis("/x/b.mp4", hue=0.07, epm=4)
    d1 = editing.style_dna([a1], cfg, "short", "a")
    d2 = editing.style_dna([a2], cfg, "short", "b")
    assert (d1["palette_name"], d1["look"], d1["transitions"]) != \
        (d2["palette_name"], d2["look"], d2["transitions"])
    assert editing.style_dna([a1], cfg, "short", "a") == d1, "même vidéo = même identité"
    cfg.data["style"]["accent_color"] = "#123456"
    assert editing.style_dna([a2], cfg, "short", "b")["palette"]["accent"] == "#123456"


def test_ai_labels_drive_semantic_graphics(cfg):
    a = _fake_analysis("/x/v.mp4")
    a["highlights"][0].update({"source": "gemini", "label": "headshot"})
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "k", None)
    assert any(g["type"] == "headshot" for g in plan.graphics)
    b = _fake_analysis("/x/v.mp4")  # sans IA : jamais de « headshot » inventé
    plan = build_plan("short", [b], cfg)
    editing.direct(plan, [b], cfg, None, "k", None)
    assert not any(g["type"] in ("headshot", "victory") for g in plan.graphics)
    assert not any(g.get("icon") == "skull" for g in plan.graphics)


# -- 8 + 9. Rendu multicouche et QA ---------------------------------------------------
def test_styled_render_end_to_end(cfg, tmp_path):
    src = tmp_path / "game.mp4"
    synth.make_gameplay(src, duration=40, width=640, height=360, fps=30,
                        bursts=[(8, 8.6), (9.6, 10.1), (20, 20.6), (31, 31.6)], cut_every=10,
                        noise=30)
    from gaming_ai_editor.analysis import analyze_video

    a = analyze_video(src, cfg["analysis"])
    cfg.data["short"]["min_seconds"] = 10
    cfg.data["short"]["target_seconds"] = 15
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "e2e", None)
    assert plan.graphics and plan.sfx and any(s.camera for s in plan.segments)
    res = Renderer(cfg).render(plan, cfg.output_dir / "styled.mp4", cfg.work_dir / "styled")
    info = probe(res["path"])
    assert (info.width, info.height) == (360, 640)
    assert info.duration == pytest.approx(plan.duration, abs=0.15)
    assert res["music"] == "générée" and res["graphics"]["frames_with_graphics"] > 0
    report = run_qa(res["path"], plan.to_dict(), cfg, True, res)
    names = {c["name"]: c for c in report["checks"]}
    assert names["synchro des impacts"]["status"] == "pass", names["synchro des impacts"]
    assert names["couche graphique"]["status"] == "pass"
    assert report["status"] != "fail", [c for c in report["checks"] if c["status"] == "fail"]
    assert report["motion_score"] >= 70


def test_qa_detects_missing_graphics_and_bad_sync(cfg, tmp_path):
    from gaming_ai_editor.qa.motion import sync_errors

    energy = np.full(2000, -60.0)
    energy[600:640] = -10.0  # attaque à 3,0 s (pas de 5 ms)
    assert sync_errors(energy, [3.0])[0] < 0.01
    assert sync_errors(energy, [2.85])[0] == pytest.approx(0.15, abs=0.01)


# -- 10. Registre : statuts, quarantaine, jamais moins bien -----------------------------
def test_registry_statuses_and_quarantine(orch):
    reg = SkillRegistry(orch.db)
    base = reg.active("motion_preset")
    assert base["status"] == ACTIVE
    reg.record_result(base["id"], ACTIVE, {"metric": "motion_v1"}, 80.0)
    worse = reg.add_candidate("motion_preset", "moins-bien", "1", {"punch": 2})
    reg.record_result(worse, VALIDATED, {"metric": "motion_v1"}, 70.0)
    with pytest.raises(ValueError):
        reg.promote(worse, "test")  # jamais remplacer par moins performant
    better = reg.add_candidate("motion_preset", "mieux", "1", {"punch": 1.1})
    reg.record_result(better, VALIDATED, {"metric": "motion_v1"}, 85.0)
    reg.promote(better, "test")
    assert reg.get(base["id"])["status"] == VALIDATED  # l'ancienne version est gardée
    restored = reg.quarantine_active("motion_preset", "rendu en échec")
    assert restored["id"] == base["id"] and reg.get(better)["status"] == QUARANTINE
    with pytest.raises(ValueError):
        reg.promote(better, "retour")  # une version en quarantaine n'est plus réactivée seule
    assert reg.quarantine_active("motion_preset", "x") is None  # la version d'origine reste


def test_resources_snapshot():
    from gaming_ai_editor import resources

    snap = resources.snapshot()
    assert "ram_available_gb" in snap and "vram" in snap
    ok, why = resources.has_room(0.0, 0.0)
    assert ok, why
    assert resources.has_room(10_000.0, 0.0)[0] is False


# -- Anti-robot : bornes adaptatives, structure narrative, légendes, emballage ----------
def _score_analysis(path="/x/v.mp4"):
    """Courbe d'intensité synthétique : calme, montée, pic à 20 s, retombée lente."""
    w = 0.5
    score = [0.2] * 120
    for k in range(34, 37):
        score[k] = 0.05  # vrai calme juste avant l'action (17-18,5 s)
    for k in range(37, 48):  # action de 18,5 à 24 s, pic à 20 s
        score[k] = 3.0 if k == 40 else 2.0
    return {"path": path, "duration": 60.0, "window": w, "score": score, "fps": 30,
            "scene_cuts": [], "highlights": [], "events": []}


def test_adaptive_bounds_follow_the_action(cfg):
    from gaming_ai_editor.editor.plan import _adaptive_bounds

    a = _score_analysis()
    h = {"start": 19.5, "end": 21.0, "peak": 20.0, "score": 3.0}
    start, end = _adaptive_bounds(h, a, cfg["short"])
    assert 17.0 <= start <= 18.5, "le clip démarre dans le calme qui précède"
    assert 24.0 <= end <= 25.0, "le clip attend que l'action retombe"
    a["score"][48:60] = [2.0] * 12  # action qui ne retombe pas : plafond tail_max (4 s)
    _, end = _adaptive_bounds(h, a, cfg["short"])
    assert end == pytest.approx(21.0 + cfg["short"]["tail_max"] + 0.4)
    a["score"][48:60] = [0.2] * 12
    a["scene_cuts"] = [16.6]
    start, _ = _adaptive_bounds(h, a, cfg["short"])
    assert start == 16.6, "un changement de plan proche est le meilleur point d'entrée"
    fixed = dict(cfg["short"], bounds="fixed")
    from gaming_ai_editor.editor.plan import _candidate

    seg = _candidate(h, a["path"], 60.0, fixed, a)
    assert seg.start == pytest.approx(19.5 - cfg["short"]["pre_roll"])


def test_story_structure_cold_open_build_climax_loop(cfg):
    a = _fake_analysis("/x/video.mp4")
    plan = build_plan("short", [a], cfg)
    roles = [s.role for s in plan.segments]
    assert roles[0] == "teaser" and roles[-1] == "climax" and set(roles[1:-1]) == {"build"}
    teaser, climax = plan.segments[0], plan.segments[-1]
    assert teaser.source == climax.source and teaser.end <= climax.peak + 0.4
    assert plan.loop is True
    editing.direct(plan, [a], cfg, None, "clé", None)
    assert all(not s.ramp for s in plan.segments[:-1]) and plan.segments[-1].ramp
    laps = plan.overlaps()
    assert laps[0] >= 0.15 and laps[-1] >= 0.15, "transitions visibles entre séquences"
    assert any(lap < 0.1 for lap in laps[1:-1]) or len(laps) < 3, "coupes sèches dans la montée"
    assert not any(g["type"] == "subscribe" for g in plan.graphics), "boucle : pas d'outro"
    durs = [s.out_duration for s in plan.segments]
    assert all(abs(a - b) / max(a, b) >= 0.2 for a, b in zip(durs[1:-2], durs[2:-1])), durs
    from gaming_ai_editor.tools.critic import checklist

    assert all(c["ok"] for c in checklist(plan, plan.duration)
               if c["point"].startswith(("climax", "accroche", "fin")))
    cfg.data["short"]["structure"] = "chrono"
    plan = build_plan("short", [a], cfg)
    starts = [s.start for s in plan.segments]
    assert starts == sorted(starts) and not any(s.role for s in plan.segments)


def test_beat_sync_and_variation_never_extend_into_a_menu(cfg):
    a = _fake_analysis("/x/video.mp4")
    a["ui_windows"] = [[33.6, 40.0]]  # écran fixe juste après le 2e moment
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "menu", None)
    for seg in plan.segments:
        assert not any(u0 < seg.end - 1e-6 and seg.peak < u0 for u0, _ in a["ui_windows"]), \
            (seg.start, seg.end)


def test_offsets_with_mixed_transition_durations():
    plan = EditPlan("short", 360, 640, 30, transition="xfade", transition_seconds=0.3,
                    segments=[Segment("a", 0, 4), Segment("a", 10, 13), Segment("a", 20, 25)],
                    transition_durations=[0.3, 0.0667])
    assert plan.offsets() == pytest.approx([0.0, 3.7, 6.6333], abs=1e-3)
    assert plan.duration == pytest.approx(12 - 0.3667, abs=1e-3)
    assert plan.junctions()[0] == pytest.approx(3.85)


def test_loop_render_has_no_fade_and_mixed_transitions(cfg, tmp_path):
    src = tmp_path / "g.mp4"
    synth.make_gameplay(src, duration=30, width=480, height=270, fps=30, cut_every=None,
                        bursts=[(5, 5.6), (14, 14.6), (22, 22.6)], noise=30)
    plan = EditPlan("short", 360, 640, 30, transition="xfade", transition_seconds=0.3,
                    segments=[Segment(str(src), 3, 7), Segment(str(src), 12, 16),
                              Segment(str(src), 20, 25)],
                    transition_durations=[0.3, 2 / 30], transitions=["fadewhite", "fade"],
                    loop=True)
    res = Renderer(cfg).render(plan, cfg.output_dir / "loop.mp4", cfg.work_dir / "loop")
    assert probe(res["path"]).duration == pytest.approx(plan.duration, abs=0.1)
    from gaming_ai_editor.qa.motion import _gray_frames

    luma, _ = _gray_frames(Path(res["path"]))
    assert luma[-1] > 0.6 * luma[len(luma) // 2], "pas de fondu au noir en boucle"


def test_word_captions_from_subtitles(cfg, tmp_path):
    from gaming_ai_editor.editor.subtitles import Cue, words_from_cues

    words = words_from_cues([Cue(1.0, 3.0, "Attention il arrive"), Cue(3.2, 4.0, "Boom")])
    assert [w["w"] for w in words] == ["Attention", "il", "arrive", "Boom"]
    assert words[0]["t0"] == 1.0 and words[2]["t1"] == pytest.approx(3.0)
    assert words[0]["t1"] - words[0]["t0"] > words[1]["t1"] - words[1]["t0"]
    timed = words_from_cues([Cue(0, 2, "a b", [(0.1, 0.4, " a"), (1.5, 1.9, " b")])])
    assert timed[1]["t0"] == 1.5, "horodatage réel par mot (Whisper) prioritaire"
    caps = graphics.Captions({"type": "captions", "start": 1, "end": 4, "words": words},
                             graphics.Context(360, 640, 30, 5))
    # 4 mots et 22 caractères au plus par ligne ; jamais à cheval sur deux répliques
    assert [[w["w"] for w in c] for c in caps.chunks] == [["Attention", "il", "arrive"],
                                                          ["Boom"]]
    # rendu réel : les légendes passent dans la couche graphique
    src = tmp_path / "s.mp4"
    synth.make_gameplay(src, duration=12, width=480, height=270, fps=30, cut_every=None,
                        bursts=[(4, 4.6)], noise=30)
    from gaming_ai_editor.analysis import analyze_video

    a = analyze_video(src, cfg["analysis"])
    plan = EditPlan("short", 360, 640, 30, segments=[Segment(str(src), 1, 9, 1, "", 4.3)])
    editing.direct(plan, [a], cfg, None, "cap", None)
    res = Renderer(cfg).render(plan, cfg.output_dir / "cap.mp4", cfg.work_dir / "cap",
                               cues_by_source={str(src): [Cue(2.0, 4.0, "Regarde bien ça")]})
    assert "mots animés" in res["subtitles"]
    assert any(g["type"] == "captions" for g in plan.graphics)


def test_critic_tool_produces_frames_and_checklist(cfg, tmp_path):
    from gaming_ai_editor.tools.critic import critique

    src = tmp_path / "g.mp4"
    synth.make_gameplay(src, duration=20, width=480, height=270, fps=30, cut_every=None,
                        bursts=[(5, 5.6)], noise=20)
    plan = EditPlan("short", 360, 640, 30, transition="xfade", transition_seconds=0.3,
                    segments=[Segment(str(src), 2, 7), Segment(str(src), 10, 14)],
                    transitions=["fade"])
    res = Renderer(cfg).render(plan, cfg.output_dir / "c.mp4", cfg.work_dir / "c")
    rep = critique(Path(res["path"]), tmp_path / "critique", plan.to_dict(), Path(res["path"]))
    for key in ("planche", "clip1_milieu", "jonction1_avant", "avant_apres"):
        assert Path(rep["images"][key]).exists(), key
    assert isinstance(rep["checklist"], list) and rep["score_checklist"]
    # grille /10 sur 9 lignes, chacune avec pourquoi, correction et fichier ; verdict < 8 seulement
    lines = {g["ligne"] for g in rep["grille"]}
    assert len(rep["grille"]) == 9 and {"accroche", "courbe d'intensité", "temps morts"} <= lines
    assert all(0 <= g["note"] <= 10 and g["pourquoi"] and g["fichier"] for g in rep["grille"])
    assert len(rep["verdict"]) <= 3 and 0 <= rep["note_globale"] <= 10
    assert rep["métriques"]["clips"] == 2 and rep["métriques"]["durée"] > 8
    assert Path(rep["images"]["images_clés"]).exists()
    assert Path(rep["images"]["avant_apres_images_clés"]).exists()
    assert set(rep["avant_après"]["grille"]) == lines
    md = (tmp_path / "critique" / "critique.md").read_text(encoding="utf-8")
    assert "accroche" in md and "/10" in md


def test_layout_auto_fills_the_vertical_frame(cfg):
    """Plein écran 9:16 : recadrage centré si l'action est au centre (viseur), suivi sinon ;
    la zone 4:5 entre bandes floues (hybrid) seulement sur demande."""
    a = _fake_analysis("/x/v.mp4")
    assert editing.style_dna([a], cfg, "short", "k")["layout"] == "crop"
    a["focus"]["spread"] = 0.2
    assert editing.style_dna([a], cfg, "short", "k")["layout"] == "track"
    cfg.data["short"]["layout"] = "hybrid"
    assert editing.style_dna([a], cfg, "short", "k")["layout"] == "hybrid"


def test_ai_impact_words_are_off_by_default(cfg):
    a = _fake_analysis("/x/v.mp4")
    texts = {"captions": ["CLICHÉ AVEUGLE"] * 6}
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, dict(texts), "k", None)
    words = [g["text"] for g in plan.graphics if g["type"] == "word"]
    assert words and "CLICHÉ AVEUGLE" not in words
    cfg.data["style"]["ai_captions"] = True
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, dict(texts), "k", None)
    assert "CLICHÉ AVEUGLE" in [g["text"] for g in plan.graphics if g["type"] == "word"]


def test_looks_add_color_instead_of_removing_it(tmp_path):
    """Un look « froid » ne doit plus rendre le jeu grisâtre (saturation 0,82 auparavant)."""
    rgb = np.array([[0.7, 0.35, 0.3], [0.3, 0.45, 0.7], [0.4, 0.6, 0.35]])
    sat = lambda x: float((x.max(axis=1) - x.min(axis=1)).mean())  # noqa: E731
    for look in color.LOOKS:
        assert sat(color._apply_look(rgb, look, 1.0)) >= sat(rgb) * 0.98, look
