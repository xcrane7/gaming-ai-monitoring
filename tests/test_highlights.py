"""Moments forts « intelligents » : réactions, surprises, séries, texte à l'écran,
doublons, variété, vision IA sur plusieurs images (skill highlight-intelligence)."""
import json
import stat
import sys

import numpy as np
import pytest

from gaming_ai_editor import synth
from gaming_ai_editor.analysis import analyze_video, ocr, rerank
from gaming_ai_editor.config import DEFAULTS


@pytest.fixture(scope="module")
def voice_game(tmp_path_factory):
    path = tmp_path_factory.mktemp("hl") / "voix.mp4"
    truth = synth.make_gameplay(path, duration=60, width=480, height=270, fps=30,
                                cut_every=None, noise=30,
                                bursts=[(10, 10.5), (30, 30.4), (32, 32.4), (34, 34.4),
                                        (51, 51.6)],
                                voices=[(20, 21.2), (42, 43)], quiet=[(47, 51)])
    return path, truth, analyze_video(path, DEFAULTS["analysis"])


def _kinds_near(a, t, kind, tol=0.4):
    return any(e["kind"] == kind and abs(e["t"] - t) <= tol for e in a["events"])


def test_player_reactions_are_found_and_explosions_are_not_voices(voice_game):
    _, truth, a = voice_game
    for start, _ in truth["voices"]:
        assert _kinds_near(a, start, "reaction"), (start, a["events"])
    for start, _ in truth["bursts"]:  # bruit large bande : pas une voix
        assert not _kinds_near(a, start + 0.1, "reaction", 0.6), start


def test_silence_then_peak_is_a_surprise(voice_game):
    _, _, a = voice_game
    assert _kinds_near(a, 51.0, "surprise")
    assert not any(e["kind"] == "surprise" and e["t"] < 45 for e in a["events"])


def test_streak_groups_close_actions_and_boosts_the_moment(voice_game):
    _, _, a = voice_game
    streak = next(s for s in a["streaks"] if s["start"] <= 30.1 <= s["end"] + 0.1)
    assert streak["count"] >= 3 and streak["end"] >= 34.0
    h = next(h for h in a["highlights"] if streak["start"] - 1 <= h["peak"] <= streak["end"] + 1)
    assert h["label"].startswith("série") and h["start"] <= 30.0 and h["end"] >= 34.0
    # bonus de série (la vidéo de test répète la même mire : la pénalité de doublon peut
    # ensuite s'appliquer, c'est voulu)
    assert h["meta"]["bonus"] >= 1.25 and "série" in h["meta"]["why"]


def test_no_false_reactions_on_plain_gameplay(media):
    a = analyze_video(media["short"], DEFAULTS["analysis"])
    assert not any(e["kind"] in ("reaction", "surprise") for e in a["events"])


def test_ocr_keyword_rules_do_not_invent_kills():
    assert ocr.classify("Player eliminated by Bob") == "fail"  # on s'est fait éliminer
    assert ocr.classify("You eliminated Bob") == "kill"
    assert ocr.classify("VICT0RY ROYALE") == "victoire"
    assert ocr.classify("Level Complete!") == "victoire"
    for harmless in ("limited", "estimated", "race", "Score 1200", "animated", "grace"):
        assert ocr.classify(harmless) is None, harmless
    assert ocr.game_profile({"fortnite": {"match": ["fortnite"], "regions": {}}},
                            "C:/INPUT/Fortnite_2026.mp4")["name"] == "fortnite"


@pytest.mark.skipif(not ocr.available(), reason="RapidOCR non installé (couche facultative)")
def test_on_screen_text_labels_moments(tmp_path):
    path = tmp_path / "texte.mp4"
    synth.make_gameplay(path, duration=45, width=640, height=360, fps=30, cut_every=None,
                        noise=30, bursts=[(8, 8.5), (25, 25.5), (38, 38.5)],
                        texts=[(8.6, 11, "ELIMINATED", "topright"),
                               (25.8, 29, "VICTORY", "center")])
    a = analyze_video(path, DEFAULTS["analysis"])
    by_peak = {round(h["peak"]): h for h in a["highlights"]}
    kill = min(a["highlights"], key=lambda h: abs(h["peak"] - 8.3))
    win = min(a["highlights"], key=lambda h: abs(h["peak"] - 25.8))
    plain = min(a["highlights"], key=lambda h: abs(h["peak"] - 38.8))
    assert kill["label"] == "kill" and kill["source"] == "ocr", by_peak
    assert win["label"] == "victoire" and "VICTORY" in win["meta"]["ocr_hits"][0]["text"]
    assert plain.get("source") != "ocr"
    assert a["rerank"]["ocr_moments"] == 2


def test_duplicates_and_repetitions_are_penalised(monkeypatch):
    frame_a = np.zeros((90, 160, 3), dtype=np.uint8)
    frame_a[:, :80] = 200
    frame_b = np.zeros((90, 160, 3), dtype=np.uint8)
    frame_b[:45] = 200
    frames = {10.0: frame_a, 20.0: frame_a, 30.0: frame_b, 40.0: frame_b + 30}
    monkeypatch.setattr(rerank, "grab_frame",
                        lambda path, t, width=960: frames.get(round(t, 1), frame_b))
    monkeypatch.setattr(rerank.ocr, "available", lambda: False)
    analysis = {"duration": 60.0, "events": [], "streaks": [], "highlights": [
        {"start": p - 1, "end": p + 1, "peak": p, "score": s, "label": "pic sonore",
         "source": "local", "meta": {}}
        for p, s in ((10.0, 3.0), (20.0, 2.9), (30.0, 2.0), (40.0, 1.0))]}
    rerank.rerank("x.mp4", analysis, {"rerank_top": 10})
    h = {x["peak"]: x for x in analysis["highlights"]}
    assert h[20.0]["meta"]["dup_of"] == 10.0, "même image que le moment à 10 s"
    assert h[20.0]["score"] < 2.9 * 0.6 + 1e-6
    assert "dup_of" not in h[10.0]["meta"]
    # variété : même étiquette répétée -> 0,9^k
    assert h[30.0]["score"] == pytest.approx(2.0 * 0.9 ** 1, rel=0.01) or \
        h[30.0]["score"] < 2.0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="stubs exécutables POSIX")
def test_ai_sees_start_peak_and_end_of_each_moment(cfg, orch, media, tmp_path, monkeypatch):
    from gaming_ai_editor.ai.providers import CostGuard, qualify_moments

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    log = tmp_path / "appel.json"
    reply = json.dumps([{"index": 0, "label": "clutch", "interest": 10, "why": "sauvé à 1 PV"},
                        {"index": 1, "label": "boring", "interest": 0, "why": "rien"}])
    stub = tmp_path / "claude"
    stub.write_text(f"#!{sys.executable}\nimport sys, json, os\n"
                    f"open({str(log)!r}, 'w').write(json.dumps({{'stdin': sys.stdin.read(), "
                    "'files': sorted(os.listdir('.'))}))\n"
                    f"print({reply!r})\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    cfg.data["ai"]["gemini"]["mode"] = "off"
    cfg.data["ai"]["claude"].update({"mode": "auto", "command": str(stub)})
    cfg.data["ai"]["vision_top"] = 2
    a = analyze_video(media["square"], cfg["analysis"])
    base = [h["score"] for h in a["highlights"][:2]]
    out = qualify_moments(cfg, CostGuard(cfg, orch.db), a, tmp_path / "ia", lambda m: None)
    call = json.loads(log.read_text())
    jpgs = [f for f in call["files"] if f.endswith(".jpg")]
    assert {f[:9] for f in jpgs} == {"moment_00", "moment_01"}
    assert len(jpgs) >= 4, "plusieurs images par moment (début, pic, fin)"
    assert "début, pic, fin" in call["stdin"]
    clutch = next(h for h in out["highlights"] if h["label"] == "clutch")
    assert clutch["score"] == pytest.approx(base[0] * 1.5, rel=1e-3)  # × (0,5 + 10/10)
    assert clutch["meta"]["ai_why"] == "sauvé à 1 PV"
    boring = next(h for h in out["highlights"] if h["meta"].get("ai_why") == "rien")
    assert boring["label"] == "rien" and boring["score"] == pytest.approx(base[1] * 0.5, rel=1e-3)


def test_ocr_label_drives_kill_graphics_but_local_signals_do_not(cfg):
    from gaming_ai_editor.editor.plan import build_plan
    from gaming_ai_editor.engines import editing

    from test_engines import _fake_analysis

    a = _fake_analysis("/x/v.mp4")
    a["highlights"][0].update({"source": "ocr", "label": "kill"})
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "ocr", None)
    assert any(g["type"] == "notify" and g.get("icon") == "skull" for g in plan.graphics)
    b = _fake_analysis("/x/v.mp4")
    b["highlights"][0].update({"label": "série x4"})  # signal local : pas d'affirmation
    plan = build_plan("short", [b], cfg)
    editing.direct(plan, [b], cfg, None, "ocr", None)
    assert not any(g.get("icon") == "skull" for g in plan.graphics)


def test_ai_label_is_kept_even_without_an_audio_event(cfg):
    """Une chute silencieuse reconnue « fail » par l'IA garde son étiquette dans le montage."""
    from gaming_ai_editor.editor.plan import build_plan
    from gaming_ai_editor.engines import editing

    from test_engines import _fake_analysis

    a = _fake_analysis("/x/v.mp4", events=[{"t": 10.0, "kind": "explosion", "strength": 0.9}])
    a["highlights"][1].update({"source": "claude", "label": "fail"})  # moment à 30 s, sans son
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "fail", None)
    seg = next(s for s in plan.segments if s.start <= 30 <= s.end and s.role != "teaser")
    assert any(e.get("label") == "fail" for e in seg.events)
    assert any("glitch" in e["fx"] for e in seg.events), "un fail est souligné (glitch)"
