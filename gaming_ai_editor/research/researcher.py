"""Agent Skills Researcher : amélioration continue, prudente et réversible.

Un cycle (toutes les 6 h par défaut) :
  1. Découverte : métadonnées PyPI d'une liste de veille + recherche GitHub
     (API publiques gratuites, aucune clé, aucun service payant).
  2. Licences et dépendances vérifiées pour chaque découverte.
  3. Expériences mesurées :
       - réglages d'encodage (vitesse / taille / SSIM sur un clip de référence) ;
       - détecteur de scènes externe, installé dans un bac à sable isolé.
  4. Intégration automatique UNIQUEMENT si tous les critères passent
     (licence, bac à sable, benchmark, puis rendu de contrôle validé par la QA).
  5. Version précédente conservée ; rollback automatique si la vérification
     après activation échoue, et rollback manuel possible à tout moment.

Faible priorité : les processus lourds tournent en priorité basse et sont
interrompus dès qu'un rendu démarre ; le cycle reprend ensuite.
"""
from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import synth
from ..analysis.signals import video_signals
from ..db import Database
from ..editor.plan import EditPlan, Segment
from ..editor.render import Renderer
from ..ffutil import Cancelled, FFmpegError, RenderActivity, encoder_works, probe, run_ffmpeg
from ..qa.benchmark import measure_encode
from ..qa.checks import FAIL, run_qa
from .registry import SkillRegistry
from .sandbox import RUNNERS, Sandbox, SandboxError, run_runner

PYPI_WATCHLIST = ["scenedetect", "faster-whisper", "auto-editor", "ffmpeg-normalize",
                  "librosa", "ultralytics", "moviepy"]
GITHUB_QUERIES = ["gaming highlight detection", "video highlight detection ffmpeg",
                  "automatic video editor shorts"]

# Outils externes que le chercheur a le droit d'installer et d'évaluer tout seul :
# uniquement ceux pour lesquels un adaptateur (runner) relu existe dans ce dépôt.
ADAPTERS = {
    "scenedetect": {
        "kind": "scene_detector", "impl": "pyscenedetect",
        "runner": "scene_pyscenedetect.py",
        "requirements": lambda v: [f"scenedetect=={v}", "opencv-python-headless"],
    },
}

ENCODE_CANDIDATES = {
    "cpu": [
        ("x264-superfast-crf19", ["-c:v", "libx264", "-preset", "superfast", "-crf", "19"]),
        ("x264-faster-crf21", ["-c:v", "libx264", "-preset", "faster", "-crf", "21"]),
        ("x264-veryfast-crf20-aq3", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                     "-aq-mode", "3"]),
        ("x264-superfast-crf20", ["-c:v", "libx264", "-preset", "superfast", "-crf", "20"]),
        ("x264-fast-crf22", ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]),
    ],
    "nvenc": [
        ("nvenc-p4-cq21", ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr",
                           "-cq", "21", "-b:v", "0"]),
        ("nvenc-p5-cq21-aq", ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                              "-cq", "21", "-b:v", "0", "-spatial-aq", "1",
                              "-temporal-aq", "1"]),
        ("nvenc-p6-cq22-la", ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr",
                              "-cq", "22", "-b:v", "0", "-rc-lookahead", "20"]),
        ("nvenc-p7-cq22-aq", ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                              "-cq", "22", "-b:v", "0", "-spatial-aq", "1"]),
    ],
}
_RATE_CAP = ["-maxrate", "16M", "-bufsize", "32M", "-pix_fmt", "yuv420p"]


def normalize_license(info: dict) -> str:
    """Licence d'un paquet PyPI à partir de ses métadonnées."""
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr
    for classifier in info.get("classifiers") or []:
        if classifier.startswith("License ::"):
            name = classifier.split("::")[-1].strip()
            for token, spdx in (("Affero", "AGPL-3.0"), ("Lesser", "LGPL"), ("GNU General", "GPL"),
                                ("MIT", "MIT"), ("Apache", "Apache-2.0"), ("BSD", "BSD"),
                                ("Mozilla", "MPL-2.0"), ("ISC", "ISC"), ("Unlicense", "Unlicense"),
                                ("Python Software", "PSF")):
                if token in name:
                    return spdx
            return name
    text = (info.get("license") or "").strip()
    return text.splitlines()[0][:60] if text else "inconnue"


def license_allowed(license_name: str, allowlist: list[str]) -> bool:
    """Vrai si la licence est dans la liste autorisée (AGPL/GPL/inconnue = refus)."""
    name = (license_name or "").upper()
    if not name or "INCONNUE" in name or "NOASSERTION" in name:
        return False
    if "AGPL" in name or ("GPL" in name and "LGPL" not in name):
        return False
    return any(allowed.upper() in name for allowed in allowlist)


def f1_score(found: list[float], truth: list[float], tolerance: float = 0.5) -> float:
    remaining = list(truth)
    tp = 0
    for t in found:
        match = next((g for g in remaining if abs(g - t) <= tolerance), None)
        if match is not None:
            remaining.remove(match)
            tp += 1
    if not found or not truth or tp == 0:
        return 1.0 if not found and not truth else 0.0
    precision, recall = tp / len(found), tp / len(truth)
    return round(2 * precision * recall / (precision + recall), 4)


def _http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "gaming-ai-editor-researcher",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class SkillsResearcher:
    def __init__(self, cfg, db: Database, activity: RenderActivity | None = None):
        self.cfg = cfg
        self.rc = cfg["research"]
        self.db = db
        self.registry = SkillRegistry(db)
        self.activity = activity or RenderActivity(cfg.data_dir)
        self.sandbox = Sandbox(cfg.data_dir, int(self.rc["sandbox_timeout"]))
        self.stop_event = threading.Event()
        self.pauses = 0

    def log(self, message: str, level: str = "info") -> None:
        self.db.log("chercheur", message, level)

    # -- pause pendant les rendus -------------------------------------------------
    def _wait_if_rendering(self) -> None:
        announced = False
        while self.activity.is_active() and not self.stop_event.is_set():
            if not announced:
                self.pauses += 1
                self.log("Rendu en cours : recherche en pause.")
                announced = True
            self.stop_event.wait(2)
        if announced:
            self.log("Rendu terminé : reprise de la recherche.")

    def _guarded(self, func, *args, **kwargs):
        """Exécute une mesure ; si un rendu démarre, elle est interrompue puis relancée."""
        for _ in range(50):
            self._wait_if_rendering()
            if self.stop_event.is_set():
                raise Cancelled("arrêt demandé")
            try:
                return func(*args, **kwargs)
            except Cancelled:
                continue
        raise Cancelled("trop d'interruptions")

    # -- 1. découverte -------------------------------------------------------------
    def discover(self) -> dict:
        found, errors = [], []
        now = time.time()
        allow = self.rc["license_allowlist"]

        def upsert(source, name, version, lic, url, summary, requires):
            ok = license_allowed(lic, allow)
            adapter = source == "pypi" and name in ADAPTERS
            status = ("licence refusée" if not ok else
                      "évaluable automatiquement" if adapter else "à examiner manuellement")
            self.db.execute(
                "INSERT INTO discoveries(source, name, version, license, license_ok, url, "
                "summary, requires, status, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source, name) DO UPDATE SET version=excluded.version, "
                "license=excluded.license, license_ok=excluded.license_ok, url=excluded.url, "
                "summary=excluded.summary, requires=excluded.requires, status=excluded.status, "
                "last_seen=excluded.last_seen",
                (source, name, version, lic, int(ok), url, (summary or "")[:300],
                 json.dumps(requires or []), status, now, now))
            found.append({"source": source, "name": name, "version": version, "license": lic,
                          "license_ok": ok, "status": status})

        for name in PYPI_WATCHLIST:
            try:
                info = _http_json(f"https://pypi.org/pypi/{name}/json")["info"]
                requires = [r for r in (info.get("requires_dist") or []) if "extra ==" not in r]
                upsert("pypi", name, info["version"], normalize_license(info),
                       info.get("project_url") or f"https://pypi.org/project/{name}/",
                       info.get("summary"), requires)
            except Exception as exc:
                errors.append(f"pypi:{name}: {type(exc).__name__}")
        for query in GITHUB_QUERIES:
            try:
                data = _http_json("https://api.github.com/search/repositories?"
                                  + urllib.parse.urlencode({"q": query, "sort": "stars",
                                                            "per_page": 5}))
                for item in data.get("items", []):
                    lic = (item.get("license") or {}).get("spdx_id") or "inconnue"
                    upsert("github", item["full_name"], str(item.get("pushed_at", ""))[:10], lic,
                           item["html_url"],
                           f"{item.get('stargazers_count', 0)}★ — {item.get('description') or ''}",
                           [])
            except Exception as exc:
                errors.append(f"github:{query}: {type(exc).__name__}")
        return {"found": len(found), "errors": errors, "items": found}

    # -- 2. réglages d'encodage ------------------------------------------------------
    def _reference(self) -> Path:
        ref_cfg = self.rc.get("reference") or {}
        seconds = int(ref_cfg.get("seconds", 10))
        w, h = int(ref_cfg.get("width", 1920)), int(ref_cfg.get("height", 1080))
        fps = int(ref_cfg.get("fps", 60))
        bench = self.cfg.data_dir / "benchmark"
        bench.mkdir(parents=True, exist_ok=True)
        # De préférence un extrait du dernier vrai gameplay analysé (plus représentatif).
        video = self.db.one("SELECT v.id, v.path, v.fingerprint, h.peak FROM videos v "
                            "JOIN highlights h ON h.video_id = v.id "
                            "ORDER BY v.analyzed_at DESC, h.score DESC LIMIT 1")
        if video and Path(video["path"]).exists():
            ref = bench / f"ref_real_{video['fingerprint'][:12]}_{w}x{h}_{seconds}s.mp4"
            if not ref.exists():
                start = max(0.0, video["peak"] - seconds / 2)
                try:
                    run_ffmpeg(["-ss", f"{start:.2f}", "-t", str(seconds), "-i", video["path"],
                                "-an", "-vf", f"scale={w}:{h}:force_original_aspect_ratio="
                                f"decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},"
                                "format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                                "-crf", "8", str(ref)], low_priority=True)
                except FFmpegError:
                    ref.unlink(missing_ok=True)
            if ref.exists():
                return ref
        ref = bench / f"ref_synth_{w}x{h}_{fps}_{seconds}s.mp4"
        if not ref.exists():
            synth.make_gameplay(ref, duration=seconds, width=w, height=h, fps=fps,
                                bursts=[(seconds * 0.3, seconds * 0.45)],
                                cut_every=max(2.0, seconds / 3), audio=False)
        return ref

    def _measure(self, ref: Path, args: list[str]) -> dict:
        """Deux passes, on garde la plus rapide (réduit le bruit de mesure)."""
        out = self.cfg.data_dir / "benchmark" / "candidate.mp4"
        best = None
        for _ in range(int(self.rc.get("measure_passes", 2))):
            m = self._guarded(measure_encode, ref, args, out, True, self.activity.is_active)
            if best is None or m["seconds"] < best["seconds"]:
                best = m
        return best

    def judge_encode(self, base: dict, cand: dict) -> tuple[bool, str]:
        if cand.get("ssim") is None or base.get("ssim") is None:
            return False, "qualité non mesurable"
        dq = cand["ssim"] - base["ssim"]
        speed = cand["fps"] / max(base["fps"], 1e-6)
        size = cand["bitrate_kbps"] / max(base["bitrate_kbps"], 1e-6)
        facts = f"vitesse x{speed:.2f}, taille x{size:.2f}, SSIM {dq:+.4f}"
        if dq < -float(self.rc["max_quality_loss"]):
            return False, f"qualité en baisse ({facts})"
        if size > float(self.rc["max_size_growth"]):
            return False, f"fichiers trop gros ({facts})"
        if speed >= float(self.rc["min_speed_gain"]):
            return True, f"plus rapide à qualité égale ({facts})"
        if dq >= 0.003 and speed >= 0.95 and size <= 1.05:
            return True, f"meilleure qualité à vitesse égale ({facts})"
        return False, f"pas de gain suffisant ({facts})"

    def _verify_profile(self, profile: dict) -> tuple[bool, str]:
        """Rendu de contrôle complet (2 clips verticaux) validé par la QA."""
        work = self.cfg.data_dir / "benchmark" / "verify"
        src = self.cfg.data_dir / "benchmark" / "verify_source.mp4"
        if not src.exists():
            synth.make_gameplay(src, duration=14, width=640, height=360, fps=30,
                                bursts=[(3, 5), (9, 11)], cut_every=7)
        plan = EditPlan(format="short", width=540, height=960, fps=30,
                        segments=[Segment(str(src), 2.0, 6.0, 1.0, "test", 4.0),
                                  Segment(str(src), 8.0, 12.0, 1.0, "test", 10.0)],
                        transition="xfade", transition_seconds=0.25, min_seconds=5,
                        max_seconds=60)
        out = self.cfg.data_dir / "benchmark" / "verify_out.mp4"
        try:
            self._guarded(Renderer(self.cfg, profile, self.log).render, plan, out, work)
            report = run_qa(out, plan.to_dict(), self.cfg)
        except Exception as exc:
            return False, f"rendu de contrôle en échec : {str(exc)[:200]}"
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out.unlink(missing_ok=True)
        failed = [c["name"] for c in report["checks"] if c["status"] == FAIL]
        return (not failed), ("QA du rendu de contrôle validée" if not failed
                              else f"QA en échec : {', '.join(failed)}")

    def research_encode_profile(self) -> dict:
        active = self.registry.active("encode_profile")
        families = ["cpu"] + (["nvenc"] if encoder_works("h264_nvenc") else [])
        ref = self._reference()
        per_cycle = int(self.rc.get("candidates_per_cycle", 3))
        report = {"reference": ref.name, "tested": [], "promoted": None}
        for family in families:
            base_args = active["params"][family]["final"]
            todo = []
            for name, args in ENCODE_CANDIDATES[family]:
                known = self.db.one("SELECT id FROM skills WHERE kind='encode_profile' "
                                    "AND name=? AND status != 'candidate'", (name,))
                if not known and args + _RATE_CAP != base_args:
                    todo.append((name, args + _RATE_CAP))
            todo = todo[:per_cycle]
            if not todo:
                continue
            base = self._measure(ref, base_args)
            winner = None
            for name, args in todo:
                profile = copy.deepcopy(active["params"])
                profile[family]["final"] = args
                skill_id = self.registry.add_candidate("encode_profile", name, "1", profile)
                try:
                    cand = self._measure(ref, args)
                except FFmpegError as exc:
                    self.registry.record_result(skill_id, "rejected",
                                                {"error": str(exc)[:300]})
                    report["tested"].append({"name": name, "verdict": "erreur d'encodage"})
                    continue
                ok, why = self.judge_encode(base, cand)
                metrics = {"baseline": base, "candidate": cand, "verdict": why,
                           "family": family, "reference": ref.name}
                score = round(cand["fps"] / max(base["fps"], 1e-6), 3)
                self.registry.record_result(skill_id, "validated" if ok else "rejected",
                                            metrics, score)
                report["tested"].append({"name": name, "ok": ok, "verdict": why})
                self.log(f"Encodage « {name} » : {'RETENU' if ok else 'rejeté'} — {why}")
                if ok and (winner is None or score > winner[1]):
                    winner = (skill_id, score, name, why, profile)
            if winner and self.rc["auto_integrate"]:
                skill_id, _, name, why, profile = winner
                verified, detail = self._verify_profile(profile)
                if not verified:
                    self.registry.record_result(skill_id, "rejected", {"verdict": detail})
                    self.log(f"« {name} » non intégré : {detail}", "warn")
                    continue
                self.registry.promote(skill_id, why)
                verified, detail = self._verify_profile(
                    self.registry.active("encode_profile")["params"])
                if not verified:
                    self.registry.rollback("encode_profile", f"vérification après activation : "
                                                             f"{detail}")
                else:
                    report["promoted"] = name
                    active = self.registry.active("encode_profile")
        return report

    # -- 3. détecteur de scènes externe (bac à sable) ----------------------------------
    def research_scene_detector(self) -> dict:
        if not (self.rc["allow_network"] and self.rc["allow_package_install"]):
            return {"skipped": "installation de paquets désactivée"}
        report: dict = {"tested": []}
        for name, adapter in ADAPTERS.items():
            row = self.db.one("SELECT * FROM discoveries WHERE source='pypi' AND name=?", (name,))
            if not row:
                continue
            version = row["version"]
            skill_name = f"{adapter['impl']}"
            if self.db.one("SELECT id FROM skills WHERE kind=? AND name=? AND version=? "
                           "AND status != 'candidate'", (adapter["kind"], skill_name, version)):
                continue  # cette version a déjà été évaluée
            if not row["license_ok"]:
                report["tested"].append({"name": name, "verdict": "licence refusée"})
                continue
            if not (RUNNERS / adapter["runner"]).exists():
                continue
            clip = self.cfg.data_dir / "benchmark" / "scene_truth.mp4"
            truth_file = clip.with_suffix(".json")
            if not clip.exists() or not truth_file.exists():
                truth = synth.make_gameplay(clip, duration=40, width=640, height=360, fps=30,
                                            bursts=[], cut_every=5)
                truth_file.write_text(json.dumps(truth), encoding="utf-8")
            truth = json.loads(truth_file.read_text(encoding="utf-8"))["cuts"]
            info = probe(clip)
            ac = self.cfg["analysis"]
            t0 = time.time()
            _, builtin_cuts = video_signals(clip, info, float(ac["window_seconds"]),
                                            float(ac["video_fps"]), int(ac["video_width"]),
                                            int(ac["video_height"]), "none")
            base = {"f1": f1_score(builtin_cuts, truth), "seconds": round(time.time() - t0, 3)}
            self._wait_if_rendering()
            skill_id = None
            try:
                python = self.sandbox.create(name, version, adapter["requirements"](version))
                params = {"impl": adapter["impl"], "python": str(python),
                          "runner": adapter["runner"], "package": f"{name}=={version}"}
                skill_id = self.registry.add_candidate(adapter["kind"], skill_name, version,
                                                       params, row["license"], f"pypi:{name}")
                t0 = time.time()
                result = run_runner(python, adapter["runner"], {"video": str(clip)},
                                    int(self.rc["sandbox_timeout"]), low_priority=True)
                cand = {"f1": f1_score(result["cuts"], truth),
                        "seconds": round(time.time() - t0, 3)}
            except (SandboxError, KeyError) as exc:
                if skill_id:
                    self.registry.record_result(skill_id, "rejected", {"error": str(exc)[:400]})
                report["tested"].append({"name": name, "version": version,
                                         "verdict": f"bac à sable en échec : {str(exc)[:200]}"})
                self.log(f"{name} {version} : bac à sable en échec ({str(exc)[:160]})", "warn")
                continue
            better = cand["f1"] >= base["f1"] + 0.02 and cand["seconds"] <= 3 * base["seconds"]
            faster = cand["f1"] >= base["f1"] and cand["seconds"] <= base["seconds"] / 1.1
            ok = better or faster
            why = (f"précision F1 {cand['f1']} (détecteur actuel : {base['f1']}), "
                   f"durée {cand['seconds']}s (actuel : {base['seconds']}s)")
            verdict = ("plus précis" if better else "plus rapide" if faster
                       else "pas de gain suffisant") + f" — {why}"
            self.registry.record_result(skill_id, "validated" if ok else "rejected",
                                        {"baseline": base, "candidate": cand, "verdict": verdict,
                                         "license": row["license"]}, cand["f1"])
            self.log(f"Détecteur de scènes {name} {version} : "
                     f"{'RETENU' if ok else 'rejeté'} — {verdict}")
            entry = {"name": name, "version": version, "ok": ok, "verdict": verdict}
            if ok and self.rc["auto_integrate"]:
                self.registry.promote(skill_id, verdict)
                entry["promoted"] = True
            elif not ok:
                self.sandbox.remove(name, version)  # ne garde pas un outil rejeté sur le disque
            report["tested"].append(entry)
        return report

    # -- cycle -------------------------------------------------------------------------
    def run_cycle(self, offline: bool = False) -> dict:
        run_id = self.db.insert("research_runs", started_at=time.time(), status="running")
        summary: dict = {"run_id": run_id}
        self.pauses = 0
        self.log("Début du cycle de recherche.")
        status = "done"
        try:
            self._wait_if_rendering()
            if self.rc["allow_network"] and not offline:
                summary["discovery"] = self.discover()
            else:
                summary["discovery"] = {"skipped": "hors ligne"}
            summary["encode_profile"] = self.research_encode_profile()
            if not offline:
                summary["scene_detector"] = self.research_scene_detector()
        except Cancelled:
            status = "interrupted"
        except Exception as exc:
            status = "error"
            summary["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            self.log(f"Cycle de recherche en erreur : {summary['error']}", "error")
        summary["pauses_for_render"] = self.pauses
        summary["status"] = status
        self.db.update("research_runs", run_id, finished_at=time.time(), status=status,
                       summary=json.dumps(summary, ensure_ascii=False))
        self.log(f"Fin du cycle de recherche ({status}).")
        return summary

    def status(self) -> dict:
        return {
            "enabled": bool(self.rc["enabled"]),
            "interval_hours": self.rc["interval_hours"],
            "next_run_at": self.db.kv_get("research.next_run_at"),
            "active": {kind: (self.registry.active(kind) or {}).get("name")
                       for kind in ("encode_profile", "scene_detector")},
            "last_runs": self.db.query(
                "SELECT id, started_at, finished_at, status FROM research_runs "
                "ORDER BY id DESC LIMIT 5"),
        }


class ResearchScheduler:
    """Planifie un cycle toutes les N heures ; l'échéance survit aux redémarrages."""

    KEY = "research.next_run_at"

    def __init__(self, cfg, db: Database, activity: RenderActivity | None = None):
        self.cfg = cfg
        self.db = db
        self.researcher = SkillsResearcher(cfg, db, activity)
        self.thread: threading.Thread | None = None
        self.run_now = threading.Event()

    @property
    def interval(self) -> float:
        return float(self.cfg["research"]["interval_hours"]) * 3600

    def due_at(self, now: float | None = None) -> float:
        """Prochaine échéance. Si elle est dépassée (PC éteint), on repart au démarrage."""
        now = now if now is not None else time.time()
        delay = float(self.cfg["research"]["startup_delay_seconds"])
        stored = self.db.kv_get(self.KEY)
        interrupted = self.db.one("SELECT id FROM research_runs WHERE status='running'")
        if interrupted:  # un cycle a été coupé par un arrêt : on le marque et on relance
            self.db.execute("UPDATE research_runs SET status='interrupted', finished_at=? "
                            "WHERE status='running'", (now,))
            return now + delay
        if not isinstance(stored, (int, float)) or stored <= now:
            return now + delay
        return float(stored)

    def _loop(self) -> None:
        stop = self.researcher.stop_event
        due = self.due_at()
        self.db.kv_set(self.KEY, due)
        while not stop.is_set():
            wait = max(0.0, due - time.time())
            triggered = self.run_now.wait(min(wait, 30)) if wait else True
            if stop.is_set():
                break
            if not triggered and time.time() < due:
                continue
            self.run_now.clear()
            try:
                self.researcher.run_cycle()
            except Exception as exc:  # le planificateur ne doit jamais mourir
                self.db.log("chercheur", f"Erreur inattendue : {exc}", "error")
            due = time.time() + self.interval
            self.db.kv_set(self.KEY, due)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True, name="research")
        self.thread.start()

    def trigger(self) -> None:
        self.run_now.set()

    def stop(self) -> None:
        self.researcher.stop_event.set()
        self.run_now.set()
