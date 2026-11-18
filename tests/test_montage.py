"""Analyse, plan de montage, rendu, QA, pipeline complet."""
import hashlib
import json
from pathlib import Path

import pytest

from conftest import copy_in
from gaming_ai_editor import synth
from gaming_ai_editor.analysis import analyze_video
from gaming_ai_editor.editor import subtitles as subs
from gaming_ai_editor.editor.plan import EditPlan, Segment, build_plan
from gaming_ai_editor.editor.render import Renderer
from gaming_ai_editor.ffutil import (Cancelled, encoder_works, probe, resolve_encoder_family,
                                     run_ffmpeg)
from gaming_ai_editor.qa.checks import run_qa


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def analysis_short(media):
    from gaming_ai_editor.config import DEFAULTS

    return analyze_video(media["short"], DEFAULTS["analysis"])


@pytest.fixture(scope="module")
def analysis_long(media):
    from gaming_ai_editor.config import DEFAULTS

    return analyze_video(media["long"], DEFAULTS["analysis"])


# -- analyse ---------------------------------------------------------------------
def test_analysis_finds_every_action_burst(media, analysis_short):
    peaks = [h["peak"] for h in analysis_short["highlights"]]
    for start, end in media["short_truth"]["bursts"]:
        assert any(start - 1.5 <= p <= end + 1.5 for p in peaks), f"moment {start}-{end} manqué"
    assert json.dumps(analysis_short)  # sérialisable (pas de types NumPy)


def test_analysis_detects_scene_cuts(media, analysis_short):
    found = analysis_short["scene_cuts"]
    hits = sum(any(abs(c - t) <= 0.5 for c in found) for t in media["short_truth"]["cuts"])
    assert hits >= len(media["short_truth"]["cuts"]) - 1


def test_analysis_without_audio(media):
    from gaming_ai_editor.config import DEFAULTS

    a = analyze_video(media["silent"], DEFAULTS["analysis"])
    assert a["has_audio"] is False and a["highlights"]


# -- plan ------------------------------------------------------------------------
def test_short_plan_respects_duration_and_hook(cfg, analysis_short):
    plan = build_plan("short", [analysis_short], cfg)
    assert 30 <= plan.duration <= 60
    assert len(plan.segments) >= 2
    assert plan.segments[0].score == max(s.score for s in plan.segments)
    for seg in plan.segments:
        assert 0 <= seg.start < seg.end <= analysis_short["duration"] + 1e-6


def test_long_plan_from_long_source_is_5_to_12_minutes(cfg, analysis_long):
    plan = build_plan("long", [analysis_long], cfg)
    assert 300 <= plan.duration <= 720
    starts = [s.start for s in plan.segments]
    assert starts == sorted(starts), "le format long reste chronologique"
    for a, b in zip(plan.segments, plan.segments[1:]):
        assert a.end <= b.start, "pas de chevauchement"


def test_long_plan_from_short_source_keeps_everything(cfg, analysis_short):
    plan = build_plan("long", [analysis_short], cfg)
    assert abs(plan.duration - analysis_short["duration"]) < 0.1
    assert any("trop courte" in n for n in plan.notes)


def test_plan_roundtrip(cfg, analysis_short):
    plan = build_plan("short", [analysis_short], cfg)
    again = EditPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again.to_dict() == plan.to_dict()


# -- sous-titres -------------------------------------------------------------------
def test_subtitles_are_remapped_to_the_edit_timeline():
    cues = subs.parse_srt("1\n00:00:11,000 --> 00:00:13,000\nBonjour\n\n"
                          "2\n00:00:50,500 --> 00:00:52,000\n<i>Fin</i>\n")
    assert [(c.start, c.end, c.text) for c in cues] == [(11.0, 13.0, "Bonjour"),
                                                        (50.5, 52.0, "Fin")]
    plan = EditPlan("short", 360, 640, 30, transition="xfade", transition_seconds=0.5,
                    segments=[Segment("a.mp4", 48.0, 55.0), Segment("a.mp4", 10.0, 20.0)])
    out = subs.remap_cues(plan, {"a.mp4": cues})
    assert [(round(c.start, 2), c.text) for c in out] == [(2.5, "Fin"), (7.5, "Bonjour")]


# -- rendu + QA ----------------------------------------------------------------------
def test_render_vertical_with_music_sfx_subtitles(cfg, media, analysis_short):
    synth.make_tone(cfg.path("music") / "loop.wav", duration=8)
    synth.make_sfx(cfg.path("sfx"))
    plan = build_plan("short", [analysis_short], cfg)
    cues = {str(media["short"]): [subs.Cue(plan.segments[0].start + 1,
                                           plan.segments[0].start + 3, "Test")]}
    before = sha256(media["short"])
    result = Renderer(cfg).render(plan, cfg.output_dir / "v.mp4", cfg.work_dir / "v",
                                  cues_by_source=cues)
    assert result["music"] == "loop.wav" and result["sfx"] == len(plan.segments) - 1
    # structure narrative : le cold open rejoue le climax, la réplique peut apparaître 2 fois
    expected = len(subs.remap_cues(plan, cues))
    assert expected >= 1 and result["subtitles"] == f"{expected} répliques"
    info = probe(result["path"])
    assert (info.width, info.height) == (360, 640) and info.has_audio
    report = run_qa(result["path"], plan.to_dict(), cfg, sha256(media["short"]) == before)
    assert report["status"] == "pass", [c for c in report["checks"] if c["status"] != "pass"]
    assert not (cfg.work_dir / "v").exists(), "fichiers de travail nettoyés"


@pytest.mark.parametrize("layout", ["crop", "blur", "hybrid"])
def test_vertical_layouts(cfg, media, layout):
    cfg.data["short"]["layout"] = layout
    plan = EditPlan("short", 360, 640, 30, segments=[Segment(str(media["short"]), 10, 13)])
    result = Renderer(cfg).render(plan, cfg.output_dir / f"{layout}.mp4", cfg.work_dir / layout)
    info = probe(result["path"])
    assert (info.width, info.height) == (360, 640) and abs(info.duration - 3) < 0.2


def test_silent_and_square_sources_render(cfg, media):
    for key in ("silent", "square"):
        for fmt, size in (("short", (360, 640)), ("long", (640, 360))):
            plan = EditPlan(fmt, size[0], size[1], 30,
                            segments=[Segment(str(media[key]), 5, 8),
                                      Segment(str(media[key]), 20, 23)])
            out = Renderer(cfg).render(plan, cfg.output_dir / f"{key}_{fmt}.mp4",
                                       cfg.work_dir / f"{key}_{fmt}")
            info = probe(out["path"])
            assert (info.width, info.height) == size and info.has_audio
            assert abs(info.duration - 6) < 0.2


def test_nvenc_failure_falls_back_to_cpu(cfg, media):
    renderer = Renderer(cfg)
    renderer.family = "nvenc"  # on force le GPU ; sans carte, le repli CPU doit prendre le relais
    plan = EditPlan("long", 640, 360, 30, segments=[Segment(str(media["short"]), 2, 4)])
    out = renderer.render(plan, cfg.output_dir / "fb.mp4", cfg.work_dir / "fb")
    assert Path(out["path"]).exists()
    if not encoder_works("h264_nvenc"):
        assert out["encoder"] == "cpu"
        assert resolve_encoder_family("auto") == "cpu"


def test_qa_rejects_bad_renders(cfg, media, tmp_path):
    plan = {"width": 1080, "height": 1920, "fps": 30, "duration": 60.0,
            "min_seconds": 30, "max_seconds": 60, "notes": []}
    report = run_qa(media["short"], plan, cfg)
    failed = {c["name"] for c in report["checks"] if c["status"] == "fail"}
    assert report["status"] == "fail" and "résolution" in failed

    black = tmp_path / "noir.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "color=black:s=360x640:r=30:d=31", "-f", "lavfi", "-i",
                "sine=f=300:d=31", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-shortest", str(black)])
    plan = {"width": 360, "height": 640, "fps": 30, "duration": 31.0, "min_seconds": 30,
            "max_seconds": 60, "notes": []}
    failed = {c["name"] for c in run_qa(black, plan, cfg)["checks"] if c["status"] == "fail"}
    assert "images noires" in failed

    data = Path(media["silent"]).read_bytes()
    broken = tmp_path / "tronque.mp4"
    broken.write_bytes(data[: len(data) // 3])
    assert run_qa(broken, plan, cfg)["status"] == "fail"
    assert run_qa(tmp_path / "absent.mp4", plan, cfg)["status"] == "fail"
    report = run_qa(media["square"], {**plan, "width": 480, "height": 480, "fps": 25,
                                      "duration": 40.0}, cfg, originals_intact=False)
    assert "originaux intacts" in {c["name"] for c in report["checks"] if c["status"] == "fail"}


def test_ffmpeg_can_be_cancelled(media, tmp_path):
    with pytest.raises(Cancelled):
        run_ffmpeg(["-i", str(media["long"]), "-c:v", "libx264", "-preset", "veryslow",
                    str(tmp_path / "x.mp4")], cancel_check=lambda: True)


# -- pipeline complet ------------------------------------------------------------------
def test_pipeline_end_to_end_keeps_originals_intact(cfg, orch, media):
    src = copy_in(cfg, media["short"], "ranked match #1.mp4")
    before = sha256(src)
    report = orch.process_file(src)
    assert sha256(src) == before and report["originals_intact"] is True
    assert sorted(p.name for p in cfg.input_dir.iterdir()) == ["ranked match #1.mp4"]
    by_format = {o["format"]: o for o in report["outputs"]}
    short, long_ = by_format["short"], by_format["long"]
    assert short["qa"]["status"] == "pass"
    assert long_["qa"]["status"] == "warn"  # source de 60 s : trop courte pour 5 minutes
    assert Path(short["path"]).parent == cfg.output_dir / "shorts"
    assert Path(long_["path"]).parent == cfg.output_dir / "youtube"
    assert 30 <= probe(short["path"]).duration <= 60
    assert Path(report["report_path"]).exists()
    job = orch.db.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    assert job["status"] == "done" and job["progress"] == 1.0
    assert orch.db.one("SELECT COUNT(*) AS n FROM renders")["n"] == 2
    assert not orch.activity.is_active()
    assert orch.register_video(src) is None, "un fichier déjà traité n'est pas repris"


def test_long_format_is_really_5_to_12_minutes(cfg, orch, media):
    src = copy_in(cfg, media["long"])
    report = orch.process_file(src, ["long"])
    out = report["outputs"][0]
    info = probe(out["path"])
    assert 300 <= info.duration <= 720
    assert out["qa"]["status"] == "pass", [c for c in out["qa"]["checks"]
                                           if c["status"] != "pass"]


def test_compilation_of_several_clips(cfg, orch, media):
    ids = []
    for key in ("short", "square"):
        src = copy_in(cfg, media[key])
        vid = orch.register_video(src)
        orch.analyze(orch.db.one("SELECT * FROM videos WHERE id=?", (vid,)))
        ids.append(vid)
    report = orch.process_job(orch.compile(ids, "soiree", ["short"]))
    out = report["outputs"][0]
    assert out["qa"]["status"] == "pass", [c for c in out["qa"]["checks"] if c["status"] != "pass"]
    sources = {s["source"] for s in out["plan"]["segments"]}
    assert len(sources) == 2, "la compilation mélange les deux vidéos"
    assert Path(out["path"]).name == "soiree_short.mp4"


def test_broken_input_marks_job_failed(cfg, orch):
    bad = cfg.input_dir / "casse.mp4"
    bad.write_bytes(b"pas une video" * 100)
    with pytest.raises(Exception):
        orch.process_file(bad)
    job = orch.db.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    assert job["status"] == "failed" and job["error"]
    assert bad.exists(), "même un fichier illisible n'est jamais supprimé"
