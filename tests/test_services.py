"""Surveillance INPUT, Skills Researcher, garde-fou IA, dashboard."""
import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import copy_in
from gaming_ai_editor.ai.providers import (ClaudeDirector, CostGuard, GeminiAnalyst,
                                           extract_json, provider_status)
from gaming_ai_editor.config import Config
from gaming_ai_editor.dashboard.server import make_server
from gaming_ai_editor.research.registry import SkillRegistry
from gaming_ai_editor.research.researcher import (ResearchScheduler, SkillsResearcher, f1_score,
                                                  license_allowed, normalize_license)
from gaming_ai_editor.research.sandbox import SandboxError, clean_env, run_runner
from gaming_ai_editor.watcher import InputWatcher, Service


# -- surveillance du dossier INPUT ---------------------------------------------------
def test_watcher_waits_for_stable_files(cfg, orch, media):
    watcher = InputWatcher(orch)
    target = cfg.input_dir / "capture.mp4"
    data = Path(media["silent"]).read_bytes()
    target.write_bytes(data[: len(data) // 2])  # enregistrement en cours
    assert watcher.scan(now=1000) == []
    assert watcher.scan(now=1005) == []
    target.write_bytes(data)  # le fichier grossit : le délai repart de zéro
    os.utime(target, (2000, 2000))
    assert watcher.scan(now=1012) == []
    assert watcher.scan(now=1018) == []
    created = watcher.scan(now=1023)
    assert len(created) == 1
    assert watcher.scan(now=1100) == [], "pas de doublon"
    (cfg.input_dir / "notes.txt").write_text("ignoré")
    assert watcher.scan(now=1200) == [] and watcher.scan(now=1300) == []
    assert InputWatcher(orch).scan(now=5000) == []
    assert InputWatcher(orch).scan(now=5000) == []


def test_service_processes_dropped_file_and_recovers_interrupted_jobs(cfg, orch, media):
    cfg.data["watcher"]["stable_seconds"] = 1
    cfg.data["long"]["enabled"] = False
    vid = orch.register_video(copy_in(cfg, media["square"], "ancien.mp4"))
    stuck = orch.enqueue(vid)
    orch.db.update("jobs", stuck, status="running")  # simulation d'une coupure de courant
    service = Service(orch)
    service.start()
    try:
        copy_in(cfg, media["silent"], "nouveau.mp4")
        deadline = time.time() + 240
        while time.time() < deadline:
            done = orch.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='done'")["n"]
            if done == 2:
                break
            time.sleep(0.5)
    finally:
        service.stop()
    assert orch.db.one("SELECT status FROM jobs WHERE id=?", (stuck,))["status"] == "done"
    names = sorted(p.name for p in (cfg.output_dir / "shorts").iterdir())
    assert names == ["ancien_short.mp4", "nouveau_short.mp4"]


# -- registre de skills ---------------------------------------------------------------
def test_registry_promote_and_rollback(orch):
    reg = SkillRegistry(orch.db)
    assert reg.active("encode_profile")["name"] == "builtin"
    a = reg.add_candidate("encode_profile", "essai-a", "1", {"cpu": {}})
    b = reg.add_candidate("encode_profile", "essai-b", "1", {"cpu": {}})
    reg.promote(a, "test")
    reg.promote(b, "test")
    assert reg.active("encode_profile")["name"] == "essai-b"
    assert reg.rollback("encode_profile")["name"] == "essai-a"
    assert reg.rollback("encode_profile")["name"] == "builtin"
    assert reg.rollback("encode_profile") is None
    assert reg.get(a)["status"] == "QUARANTINE" and reg.get(b)["status"] == "QUARANTINE"
    assert [h["action"] for h in reg.history()][:2] == ["quarantine", "quarantine"]


def test_license_gate():
    allow = Config().data["research"]["license_allowlist"]
    assert license_allowed("MIT", allow) and license_allowed("BSD-3-Clause", allow)
    assert license_allowed("Apache-2.0", allow) and license_allowed("LGPL-3.0", allow)
    for refused in ("AGPL-3.0", "GPL-3.0", "inconnue", "", "NOASSERTION", "Proprietary"):
        assert not license_allowed(refused, allow), refused
    assert normalize_license({"license_expression": "BSD-3-Clause"}) == "BSD-3-Clause"
    assert normalize_license({"classifiers": [
        "License :: OSI Approved :: GNU Affero General Public License v3"]}) == "AGPL-3.0"
    assert normalize_license({}) == "inconnue"
    assert f1_score([5.0, 10.2, 31.0], [5.0, 10.0, 15.0]) == pytest.approx(0.6667, abs=1e-3)


def test_encode_judgement_rules(cfg, orch):
    r = SkillsResearcher(cfg, orch.db, orch.activity)
    base = {"fps": 100, "bitrate_kbps": 8000, "ssim": 0.970}
    assert r.judge_encode(base, {"fps": 130, "bitrate_kbps": 8100, "ssim": 0.9695})[0]
    assert not r.judge_encode(base, {"fps": 200, "bitrate_kbps": 8000, "ssim": 0.960})[0]
    assert not r.judge_encode(base, {"fps": 200, "bitrate_kbps": 12000, "ssim": 0.980})[0]
    assert not r.judge_encode(base, {"fps": 104, "bitrate_kbps": 8000, "ssim": 0.970})[0]
    assert r.judge_encode(base, {"fps": 98, "bitrate_kbps": 8000, "ssim": 0.975})[0]
    assert not r.judge_encode(base, {"fps": 300, "bitrate_kbps": 100, "ssim": None})[0]


# -- chercheur : pause, cycle, planification --------------------------------------------
def test_researcher_pauses_while_rendering(cfg, orch):
    r = SkillsResearcher(cfg, orch.db, orch.activity)
    orch.activity.begin()
    threading.Timer(2.5, orch.activity.end).start()
    started = time.time()
    assert r._guarded(lambda: "mesure") == "mesure"
    assert time.time() - started >= 2.0 and r.pauses == 1


def test_research_cycle_offline_promotes_only_verified_winners(cfg, orch):
    cfg.data["research"]["experiments"] = ["encode_profile"]
    r = SkillsResearcher(cfg, orch.db, orch.activity)
    summary = r.run_cycle(offline=True)
    assert summary["status"] == "done", summary
    tested = summary["encode_profile"]["tested"]
    assert len(tested) == 2 and all("verdict" in t for t in tested)
    active = r.registry.active("encode_profile")
    promoted = summary["encode_profile"]["promoted"]
    if promoted:
        assert active["name"] == promoted
        assert r.registry.rollback("encode_profile")["name"] == "builtin"
    else:
        assert active["name"] == "builtin"
    rejected = orch.db.query("SELECT metrics FROM skills WHERE status='REJECTED'")
    assert all(json.loads(row["metrics"]).get("verdict") for row in rejected)
    assert orch.db.one("SELECT status FROM research_runs")["status"] == "done"
    # un second cycle ne reteste pas ce qui a déjà été évalué
    again = r.run_cycle(offline=True)
    names = {t["name"] for t in tested} & {t["name"] for t in again["encode_profile"]["tested"]}
    assert not names


def test_reference_clip_comes_from_real_gameplay_when_available(cfg, orch, media):
    from gaming_ai_editor.ffutil import probe

    r = SkillsResearcher(cfg, orch.db, orch.activity)
    assert r._reference().name.startswith("ref_synth")
    vid = orch.register_video(copy_in(cfg, media["square"]))
    orch.analyze(orch.db.one("SELECT * FROM videos WHERE id=?", (vid,)))
    ref = r._reference()
    info = probe(ref)
    assert ref.name.startswith("ref_real") and (info.width, info.height) == (480, 270)
    assert abs(info.duration - 3) < 0.3


def test_motion_and_gpu_experiments_record_measured_verdicts(cfg, orch):
    cfg.data["research"]["candidates_per_cycle"] = 1
    cfg.data["research"]["experiments"] = ["motion_preset", "decode_accel"]
    r = SkillsResearcher(cfg, orch.db, orch.activity)
    summary = r.run_cycle(offline=True)
    assert summary["status"] == "done", summary
    motion = summary["motion_preset"]
    assert motion["baseline"]["qa_score"] > 0 and len(motion["tested"]) == 1
    assert "score" in motion["tested"][0]["verdict"]
    row = orch.db.one("SELECT status FROM skills WHERE kind='motion_preset' AND origin != 'builtin'")
    assert row["status"] in ("VALIDATED", "REJECTED", "ACTIVE")
    assert "none" in summary["decode_accel"]["options"]
    assert summary["resources"]["ram_available_gb"] is None or \
        summary["resources"]["ram_available_gb"] > 0


def test_research_cycle_never_runs_forever(cfg, orch):
    cfg.data["research"]["max_cycle_minutes"] = 0
    summary = SkillsResearcher(cfg, orch.db, orch.activity).run_cycle(offline=True)
    assert summary["status"] == "done"
    assert summary["encode_profile"] == {"skipped": "durée maximale du cycle atteinte"}


def test_scheduler_survives_shutdown(cfg, orch):
    sched = ResearchScheduler(cfg, orch.db, orch.activity)
    now = 1_000_000.0
    assert sched.due_at(now) == now + 120  # premier démarrage
    orch.db.kv_set(sched.KEY, now + 3 * 3600)
    assert sched.due_at(now) == now + 3 * 3600  # échéance future conservée
    assert sched.due_at(now + 9 * 3600) == now + 9 * 3600 + 120  # PC éteint : rattrapage
    orch.db.insert("research_runs", started_at=now, status="running")  # cycle coupé net
    assert sched.due_at(now + 60) == now + 60 + 120
    assert orch.db.one("SELECT status FROM research_runs")["status"] == "interrupted"
    assert sched.interval == 6 * 3600


def test_scheduler_runs_a_cycle_when_triggered(cfg, orch):
    cfg.data["research"]["allow_network"] = False
    cfg.data["research"]["candidates_per_cycle"] = 1
    cfg.data["research"]["experiments"] = ["encode_profile"]
    sched = ResearchScheduler(cfg, orch.db, orch.activity)
    sched.start()
    try:
        sched.trigger()
        deadline = time.time() + 240
        while time.time() < deadline:
            row = orch.db.one("SELECT status FROM research_runs ORDER BY id DESC LIMIT 1")
            if row and row["status"] != "running":
                break
            time.sleep(0.5)
    finally:
        sched.stop()
    assert row and row["status"] == "done"
    assert orch.db.kv_get(sched.KEY) > time.time() + 5 * 3600


def test_sandbox_isolation(tmp_path):
    os.environ["GAE_FAKE_API_KEY"] = "secret"
    try:
        env = clean_env()
    finally:
        del os.environ["GAE_FAKE_API_KEY"]
    assert "GAE_FAKE_API_KEY" not in env and "PATH" in env
    with pytest.raises(SandboxError):
        run_runner(sys.executable, "../../pipeline.py", {})
    with pytest.raises(SandboxError):
        run_runner(tmp_path / "pas-de-python", "scene_pyscenedetect.py", {})


@pytest.mark.skipif(os.environ.get("GAE_TEST_NETWORK") != "1",
                    reason="test réseau : GAE_TEST_NETWORK=1 pour l'activer")
def test_full_online_cycle_installs_tool_in_sandbox(cfg, orch):
    r = SkillsResearcher(cfg, orch.db, orch.activity)
    summary = r.run_cycle()
    assert summary["status"] == "done", summary
    assert summary["discovery"]["found"] > 5
    refused = orch.db.one("SELECT * FROM discoveries WHERE name='ultralytics'")
    assert refused and not refused["license_ok"]
    tested = summary["scene_detector"]["tested"]
    assert tested and tested[0]["name"] == "scenedetect" and "précision F1" in tested[0]["verdict"]


# -- IA : garde-fou des coûts et CLI d'abonnement ----------------------------------------
def _stub(tmp_path: Path, name: str, reply: str) -> str:
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({reply!r})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="stubs exécutables POSIX")
def test_cli_prompt_goes_through_stdin(tmp_path):
    """Le long texte (multi-ligne, caractères spéciaux) arrive intact par l'entrée standard."""
    from gaming_ai_editor.ai.providers import run_cli

    echo = tmp_path / "echo_cli"
    echo.write_text(f"#!{sys.executable}\nimport sys, json\n"
                    "print(json.dumps({'stdin': sys.stdin.read(), 'argv': sys.argv[1:]}))\n")
    echo.chmod(echo.stat().st_mode | stat.S_IEXEC)
    text = "Ligne 1 & 2 | 50 % ^ <x>\nLigne 2 « accents »"
    out = json.loads(run_cli(str(echo), text, 20, ["-m", "x"], arg_prompt="court"))
    assert out["stdin"] == text
    assert out["argv"] == ["-m", "x", "-p", "court"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="stubs exécutables POSIX")
def test_claude_labels_moments_when_gemini_is_unavailable(cfg, orch, media, monkeypatch,
                                                          tmp_path):
    """Sans clé Gemini : Claude (abonnement) regarde les images, avec le seul outil Read."""
    from gaming_ai_editor.ai.providers import qualify_moments
    from gaming_ai_editor.analysis import analyze_video

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    log = tmp_path / "args.json"
    reply = json.dumps([{"index": 0, "label": "kill", "interest": 9, "comment": "élimination"}])
    stub = tmp_path / "claude"
    stub.write_text(f"#!{sys.executable}\nimport sys, json, os\n"
                    f"open({str(log)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], "
                    "'stdin': sys.stdin.read(), 'files': sorted(os.listdir('.'))}))\n"
                    f"print({reply!r})\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    cfg.data["ai"]["gemini"]["mode"] = "auto"
    cfg.data["ai"]["claude"].update({"mode": "auto", "command": str(stub)})
    analysis = analyze_video(media["square"], cfg["analysis"])
    first = analysis["highlights"][0]["peak"]
    out = qualify_moments(cfg, CostGuard(cfg, orch.db), analysis, tmp_path / "ia", lambda m: None)
    assert out["ai"]["provider"] == "claude"
    top = out["highlights"][0]
    assert top["peak"] == first and top["label"] == "kill" and top["source"] == "claude"
    call = json.loads(log.read_text())
    assert call["argv"][:2] == ["--allowedTools", "Read"]
    assert {"Bash", "Edit", "Write"} <= set(call["argv"])  # aucun autre outil autorisé
    assert "moment_00_1.jpg" in call["files"] and "moment_00_1.jpg" in call["stdin"]
    assert orch.db.one("SELECT cost_class FROM ai_calls")["cost_class"] == "subscription_cli"


def test_extract_json():
    assert extract_json('Voici :\n```json\n[{"index": 0}]\n```') == [{"index": 0}]
    assert extract_json('ok {"title": "A"} fin') == {"title": "A"}
    with pytest.raises(ValueError):
        extract_json("aucun json ici")


def test_paid_api_is_refused_without_explicit_authorization(cfg, orch, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "cle-de-test")
    cfg.data["ai"]["gemini"].update({"mode": "api", "free_tier_only": False})
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    analyst = GeminiAnalyst(cfg, CostGuard(cfg, orch.db), lambda m: None)
    analysis = {"path": "x.mp4", "highlights": [
        {"peak": 1.0, "score": 2.0, "label": "action", "start": 0, "end": 2}]}
    assert analyst.enrich(analysis, cfg.work_dir / "g") is analysis
    assert not called, "aucun appel réseau ne doit partir"
    row = orch.db.one("SELECT * FROM ai_calls")
    assert row["allowed"] == 0 and row["cost_class"] == "paid_api"
    assert "bloquée" in provider_status(cfg)["gemini"]["state"]


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_free_gemini_key_labels_moments(cfg, orch, media, monkeypatch, tmp_path):
    """Clé gratuite : une requête par vidéo, images jointes, étiquettes appliquées."""
    from gaming_ai_editor.analysis import analyze_video

    cfg.data["ai"]["gemini"]["mode"] = "auto"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (cfg.root / "gemini_key.txt").write_text("cle-gratuite\n")
    analysis = analyze_video(media["square"], cfg["analysis"])
    first = analysis["highlights"][0]["peak"]
    sent = []
    verdicts = [{"index": 0, "label": "headshot", "interest": 9, "comment": "propre"}]

    def fake(req, timeout=0):
        sent.append(req)
        body = json.loads(req.data)
        if "gemini-flash-latest" in req.full_url:  # modèle retiré : on passe au suivant
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)
        return _FakeResponse({"candidates": [{"content": {"parts": [
            {"text": json.dumps(verdicts)}]}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    logs = []
    out = GeminiAnalyst(cfg, CostGuard(cfg, orch.db), logs.append).enrich(
        analysis, tmp_path / "frames")
    top = out["highlights"][0]
    assert top["peak"] == first and top["label"] == "headshot" and top["source"] == "gemini"
    body = json.loads(sent[-1].data)
    images = [p for p in body["contents"][0]["parts"] if "inline_data" in p]
    moments = min(12, len([h for h in analysis["highlights"] if h.get("label") != "menu"]))
    assert moments <= len(images) <= 3 * moments and len(sent) == 2
    assert sent[-1].headers["X-goog-api-key"] == "cle-gratuite"
    row = orch.db.one("SELECT * FROM ai_calls")
    assert row["cost_class"] == "free_tier_api" and row["allowed"] == 1
    assert "cle-gratuite" not in json.dumps(orch.db.query("SELECT * FROM ai_calls"))
    assert "cle-gratuite" not in " ".join(logs)
    assert provider_status(cfg)["gemini"]["state"] == "API gratuite (clé détectée)"


def test_free_gemini_quota_and_daily_cap(cfg, orch, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "cle")
    cfg.data["ai"]["gemini"].update({"mode": "auto", "max_requests_per_day": 2})
    analysis = {"path": "x.mp4", "highlights": [
        {"peak": 1.0, "score": 2.0, "label": "action", "start": 0, "end": 2}]}
    calls = []
    analyst = GeminiAnalyst(cfg, CostGuard(cfg, orch.db), lambda m: None)
    monkeypatch.setattr(analyst, "_frames", lambda a, w: [(0, tmp_path / "f.jpg")])
    (tmp_path / "f.jpg").write_bytes(b"jpg")

    def quota(req, timeout=0):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", quota)
    analyst.enrich(analysis, tmp_path)
    assert len(calls) == 1, "429 : on n'essaie pas d'autres modèles"
    analyst.enrich(analysis, tmp_path)
    assert len(calls) == 1, "quota épuisé : plus aucun appel aujourd'hui"
    orch.db.kv_set("gemini.quota_exhausted", "hier")
    guard = CostGuard(cfg, orch.db)
    assert guard.authorize("gemini", "t", "free_tier_api")  # 2e requête du jour
    assert not guard.authorize("gemini", "t", "free_tier_api")  # plafond de 2 atteint


@pytest.mark.skipif(sys.platform.startswith("win"), reason="stubs exécutables POSIX")
def test_subscription_cli_enriches_and_bad_answers_fall_back(cfg, orch, media, tmp_path):
    from gaming_ai_editor.analysis import analyze_video

    analysis = analyze_video(media["square"], cfg["analysis"])
    first = analysis["highlights"][0]["peak"]
    reply = json.dumps([{"index": 0, "label": "kill", "interest": 9, "comment": "beau tir"},
                        {"index": 1, "label": "rien", "interest": 0},
                        {"index": 99, "label": "kill", "interest": 9},
                        {"index": 2, "label": "inventé", "interest": 55}])
    cfg.data["ai"]["gemini"] = {"mode": "cli", "command": _stub(tmp_path, "gemini", reply),
                                "model": None, "timeout": 30}
    guard = CostGuard(cfg, orch.db)
    out = GeminiAnalyst(cfg, guard, lambda m: None).enrich(analysis, tmp_path / "frames")
    top = out["highlights"][0]
    assert top["peak"] == first and top["label"] == "kill" and top["source"] == "gemini"
    # l'index 99 est ignoré ; l'étiquette inventée est refusée mais le moment reste compté
    assert out["ai"]["moments_qualifiés"] == min(3, len(analysis["highlights"]))
    assert all(h["label"] != "inventé" for h in out["highlights"])
    assert orch.db.one("SELECT cost_class FROM ai_calls")["cost_class"] == "subscription_cli"

    cfg.data["ai"]["gemini"]["command"] = _stub(tmp_path, "gemini2", "désolé, pas de JSON")
    before = json.dumps(analysis["highlights"])
    GeminiAnalyst(cfg, guard, lambda m: None).enrich(analysis, tmp_path / "frames2")
    assert json.dumps(analysis["highlights"]) == before, "réponse invalide = analyse inchangée"

    plans = {"short": {"segments": [{"label": "kill", "peak": 12.0, "score": 3.0}]}}
    cfg.data["ai"]["claude"] = {"mode": "cli", "timeout": 30, "model": None, "command": _stub(
        tmp_path, "claude", '{"title": "Clutch 1v3", "description": "d", "hashtags": ["#fps"]}')}
    meta = ClaudeDirector(cfg, guard, lambda m: None).metadata("match", plans)
    assert meta == {"title": "Clutch 1v3", "description": "d", "hashtags": ["#fps"],
                    "source": "claude"}
    cfg.data["ai"]["claude"]["command"] = _stub(tmp_path, "claude2", "erreur")
    assert ClaudeDirector(cfg, guard, lambda m: None).metadata("match", plans)["source"] == "local"


# -- dashboard --------------------------------------------------------------------------
def test_dashboard_api(cfg, orch, media):
    cfg.data["long"]["enabled"] = False
    orch.process_file(copy_in(cfg, media["square"]))
    reg = SkillRegistry(orch.db)
    reg.promote(reg.add_candidate("encode_profile", "essai", "1",
                                  reg.active("encode_profile")["params"]), "test")
    server = make_server(orch, None, "127.0.0.1", 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(path, method="GET", headers=None, body=None):
        req = urllib.request.Request(base + path, method=method, headers=headers or {},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    try:
        status, _, body = call("/")
        assert status == 200 and b"GAMING" in body
        status, _, body = call("/api/state")
        state = json.loads(body)
        assert status == 200 and len(state["renders"]) == 1 and state["videos"][0]["highlights"]
        assert state["ai"]["allow_paid_api"] is False
        render_id = state["renders"][0]["id"]
        status, headers, body = call(f"/media/{render_id}", headers={"Range": "bytes=0-99"})
        assert status == 206 and len(body) == 100 and headers["Content-Range"].startswith(
            "bytes 0-99/")
        assert call("/media/9999")[0] == 404
        assert call("/media/../../etc/passwd")[0] == 404
        status, _, body = call(f"/api/video/{state['videos'][0]['id']}/timeline")
        assert status == 200 and json.loads(body)["highlights"]
        status, _, _ = call("/api/skills/rollback", "POST", {"Origin": "http://evil.example"},
                            {"kind": "encode_profile"})
        assert status == 403 and reg.active("encode_profile")["name"] == "essai"
        status, _, body = call("/api/skills/rollback", "POST", {"Origin": base},
                               {"kind": "encode_profile"})
        assert status == 200 and json.loads(body)["ok"]
        assert reg.active("encode_profile")["name"] == "builtin"
    finally:
        server.shutdown()
        server.server_close()


def test_youtube_package_and_tempo_matched_music(cfg, orch, media, tmp_path):
    from gaming_ai_editor.engines import audio
    from gaming_ai_editor.ffutil import probe

    for bpm in (100, 150):
        audio.generate_music(cfg.path("music") / f"track{bpm}.wav", 20, bpm=bpm, seed=bpm)
    assert orch._music_info("x", 148)["path"].endswith("track150.wav")
    assert orch._music_info("x", 102)["path"].endswith("track100.wav")
    for f in cfg.path("music").glob("*"):
        f.unlink()
    cfg.data["short"]["enabled"] = False
    report = orch.process_file(copy_in(cfg, media["long"]), ["long"])
    out = report["outputs"][0]
    yt = out["youtube"]
    times = [c["t"] for c in yt["chapters"]]
    assert times[0] == 0 and all(b - a >= 10 for a, b in zip(times, times[1:]))
    assert yt["chapters_text"].startswith("0:00 Intro")
    assert yt["chapters_text"] in report["metadata"]["description"]
    thumb = Path(yt["thumbnail"])
    assert thumb.exists() and probe(thumb).width == 1280
