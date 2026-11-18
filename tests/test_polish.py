"""Finition FFmpeg (skill edit-polish-ffmpeg) : punch-in net, vitesse et hauteur du son,
dérive A/V, temps morts accélérés, sons variés, musique automatisée, coupes en J, whip."""
import numpy as np
import pytest

from gaming_ai_editor import synth
from gaming_ai_editor.editor.plan import EditPlan, Segment, build_plan
from gaming_ai_editor.editor.render import Renderer
from gaming_ai_editor.engines import audio, editing
from gaming_ai_editor.ffutil import probe, run_ffmpeg
from test_engines import _fake_analysis


def _tone_video(path, seconds=10, freq=440, burst_at=None):
    """Vidéo de test : son pur (hauteur mesurable) + un « impact » (flash + bruit) à burst_at."""
    v = "testsrc2=s=320x180:r=30:d=%d" % seconds
    graph = "[0:v]null[v];[1:a]volume=0.5[a]"
    if burst_at is not None:
        graph = (f"[0:v]drawbox=c=white:t=fill:enable='between(t,{burst_at},{burst_at + 0.2})'[v];"
                 f"[1:a]volume=0.3[t];[2:a]volume='if(between(t,{burst_at},{burst_at + 0.2}),1,0)'"
                 ":eval=frame[n];[t][n]amix=inputs=2:normalize=0[a]")
    run_ffmpeg(["-f", "lavfi", "-i", v, "-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={seconds}",
                "-f", "lavfi", "-i", f"anoisesrc=r=48000:a=0.9:d={seconds}",
                "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(path)])
    return path


def _styled_plan(src, start, end, ramp=None, w=180, h=320):
    seg = Segment(str(src), start, end, 1.0, "", (start + end) / 2, ramp=ramp or [],
                  camera={"push": 0.02, "punches": [[1.0, 1.0]]})
    return EditPlan("short", w, h, 30, segments=[seg], style={"intensity": 1.0})


def _dominant_freq(path, t0, t1):
    y = audio.load_mono(path, 16000)[int(t0 * 16000):int(t1 * 16000)]
    spec = np.abs(np.fft.rfft(y * np.hanning(y.size)))
    return float(np.fft.rfftfreq(y.size, 1 / 16000)[int(np.argmax(spec[20:])) + 20])


def test_punch_in_zooms_inside_a_larger_image(cfg, tmp_path):
    src = _tone_video(tmp_path / "s.mp4", 4)
    plan = _styled_plan(src, 0.5, 3.5)
    r = Renderer(cfg)
    graph = r._clip_video_graph(plan, plan.segments[0], "crop", None)
    assert "scale=206:368" in graph, "mise au format 1,15× avant le zoom"
    out = r.render(plan, cfg.output_dir / "ss.mp4", cfg.work_dir / "ss")
    assert (probe(out["path"]).width, probe(out["path"]).height) == (180, 320)
    cfg.data["render"]["supersample"] = 1.0
    assert "scale=180:320" in Renderer(cfg)._clip_video_graph(plan, plan.segments[0], "crop", None)


@pytest.mark.parametrize("mode", ["keep_pitch", "deep"])
def test_slow_motion_keeps_or_lowers_the_pitch(cfg, tmp_path, mode):
    src = _tone_video(tmp_path / "t.mp4", 8, 440)
    cfg.data["style"]["slowmo_audio"] = mode
    plan = _styled_plan(src, 1.0, 7.0, ramp=[3.0, 4.5, 0.35])
    out = Renderer(cfg).render(plan, cfg.output_dir / f"{mode}.mp4", cfg.work_dir / mode)
    ramp = plan.segments[0].speed_ramp()
    a, b = ramp.start + 0.4, ramp.map(ramp.end) - 0.4  # cœur du ralenti, en temps monté
    f = _dominant_freq(out["path"], a, b)
    if mode == "keep_pitch":
        assert f == pytest.approx(440, abs=25), f
    else:
        assert f < 300, f


def test_no_audio_video_drift_after_speed_changes(cfg, tmp_path):
    """Un impact placé APRÈS un ralenti puis une accélération reste synchrone (± 1 image)."""
    src = _tone_video(tmp_path / "d.mp4", 12, 300, burst_at=9.0)
    for ramp in ([3.0, 4.2, 0.35], [3.0, 6.5, 1.7]):
        plan = _styled_plan(src, 1.0, 11.0, ramp=ramp)
        plan.segments[0].camera = {}
        plan.segments[0].vfx = {}
        plan.style = {"intensity": 1.0}
        plan.graphics = [{"type": "hud", "start": 0, "end": plan.duration}]
        out = Renderer(cfg).render(plan, cfg.output_dir / "drift.mp4", cfg.work_dir / "drift")
        info = probe(out["path"])
        assert abs(info.video_duration - info.audio_duration) < 0.05
        expected = plan.segments[0].map_time(9.0)
        from gaming_ai_editor.qa.motion import _audio_energy, _gray_frames

        luma, _ = _gray_frames(out["path"])
        flash = int(np.argmax(np.diff(luma))) + 1
        energy = _audio_energy(out["path"])
        onset = int(np.argmax(np.diff(energy[int((expected - 0.5) / 0.005):]))) * 0.005 \
            + expected - 0.5
        assert flash / 30 == pytest.approx(expected, abs=0.05), (ramp, flash / 30, expected)
        assert onset == pytest.approx(flash / 30, abs=1 / 30 + 0.01), (ramp, onset, flash / 30)


def test_dead_time_is_sped_up_in_build_clips_only(cfg):
    a = _fake_analysis("/x/v.mp4", events=[{"t": 10.0, "kind": "explosion", "strength": 0.9},
                                            {"t": 30.0, "kind": "impact", "strength": 0.7},
                                            {"t": 50.0, "kind": "impact", "strength": 0.6}])
    a["score"] = [0.1] * 120
    a["window"] = 0.5
    for k in (19, 20, 59, 60, 99, 100):
        a["score"][k] = 3.0
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "dead", None)
    fast = [s for s in plan.segments if s.ramp and s.ramp[2] > 1]
    assert fast and all(s.role == "build" for s in fast)
    for s in fast:
        assert s.ramp[1] - s.ramp[0] >= 2.5
        assert all(not s.ramp[0] - 0.8 < e["t"] < s.ramp[1] + 0.8 for e in s.events)
        assert s.out_duration < s.duration
    assert next(s for s in plan.segments if s.role == "climax").ramp[2] < 1
    cfg.data["style"]["speed_up_dead_time"] = False
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "dead", None)
    assert not any(s.ramp and s.ramp[2] > 1 for s in plan.segments)


def test_impact_sounds_vary_and_custom_sounds_are_used(cfg, tmp_path):
    path = audio.render_sfx_track([{"t": 0.5, "kind": "impact"}, {"t": 2.0, "kind": "impact"}],
                                  3.5, tmp_path / "s.wav")
    y = audio.load_mono(path, 48000)
    a, b = y[int(0.5 * 48000):int(1.1 * 48000)], y[int(2.0 * 48000):int(2.6 * 48000)]
    assert not np.allclose(a, b, atol=1e-3), "deux impacts identiques"
    custom = tmp_path / "beep.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=1000:d=0.3:r=48000", str(custom)])
    synth_sfx = cfg.path("sfx")
    synth_sfx.mkdir(parents=True, exist_ok=True)
    (synth_sfx / "impact.wav").write_bytes(custom.read_bytes())
    plan = EditPlan("short", 180, 320, 30, segments=[Segment("x", 0, 2)],
                    sfx=[{"t": 0.5, "kind": "impact"}])
    out, n = Renderer(cfg)._sfx_track(plan, tmp_path, 2.0)
    assert n == 1 and _dominant_freq(out, 0.55, 0.75) == pytest.approx(1000, rel=0.08)


def test_user_music_rises_then_drops_before_the_climax():
    plan = EditPlan("short", 180, 320, 30, music={"mode": "user", "bpm": 120.0,
                                                  "calm": [20.0, 22.0], "drop_at": 22.0})
    env = Renderer._music_envelope(plan)
    assert env and "0.4" in env and "between(t,19.7,22)" in env
    assert "between(t,15.7,19.7)" in env  # deux mesures de montée à 120 BPM
    assert Renderer._music_envelope(EditPlan("short", 1, 1, 30, music={"mode": "generated"})) == ""


def test_j_cuts_bring_the_next_sound_early_without_drift(cfg, tmp_path):
    src = _tone_video(tmp_path / "j.mp4", 14, 300, burst_at=7.85)
    plan = EditPlan("long", 320, 180, 30, transition="cut",
                    segments=[Segment(str(src), 1.0, 5.0), Segment(str(src), 8.0, 12.0)])
    r = Renderer(cfg)
    assert r.audio_leads(plan) == [0.0, 0.2]
    out = r.render(plan, cfg.output_dir / "j.mp4", cfg.work_dir / "j")
    info = probe(out["path"])
    assert abs(info.video_duration - info.audio_duration) < 0.05
    assert info.duration == pytest.approx(8.0, abs=0.1)
    from gaming_ai_editor.qa.motion import _audio_energy

    with_j = _audio_energy(out["path"])[int(3.88 / 0.005):int(3.98 / 0.005)].max()
    cfg.data["long"]["jl_cuts"] = 0.0
    r0 = Renderer(cfg)
    assert r0.audio_leads(plan) == [0.0, 0.0]
    out0 = r0.render(plan, cfg.output_dir / "j0.mp4", cfg.work_dir / "j0")
    without = _audio_energy(out0["path"])[int(3.88 / 0.005):int(3.98 / 0.005)].max()
    assert with_j > without + 6, ("le son du clip suivant arrive avant la coupe", with_j, without)


def test_whip_transition_between_similar_moments(cfg, tmp_path):
    a = _fake_analysis("/x/v.mp4")
    for h in a["highlights"]:
        h["score"] = 2.0  # trois moments d'énergie identique
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "whip", None)
    climax_in = plan.transitions[-1]
    assert climax_in in ("fadewhite", "zoomin")
    src = _tone_video(tmp_path / "w.mp4", 10)
    plan = EditPlan("short", 180, 320, 30, transition="xfade", transition_seconds=0.12,
                    segments=[Segment(str(src), 1, 4), Segment(str(src), 5, 8)],
                    transitions=["whipleft"], transition_durations=[0.12])
    out = Renderer(cfg).render(plan, cfg.output_dir / "w.mp4", cfg.work_dir / "w")
    assert probe(out["path"]).duration == pytest.approx(plan.duration, abs=0.1)
