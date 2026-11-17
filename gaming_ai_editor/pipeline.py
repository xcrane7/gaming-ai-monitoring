"""Orchestrateur : coordonne Analyste, Video Editor, QA et mémoire.

Chaîne complète d'un fichier : sonde -> analyse -> (Gemini) -> plans ->
rendus short + long -> QA -> dépôt dans OUTPUT -> rapport.
Les fichiers d'INPUT ne sont jamais écrits, déplacés ni supprimés.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import analysis as analysis_mod
from .ai.providers import ClaudeDirector, CostGuard, GeminiAnalyst
from .config import VIDEO_EXTENSIONS, Config
from .db import Database
from .editor import subtitles as subs
from .editor.plan import EditPlan, build_plan
from .editor.render import Renderer
from .engines import editing
from .ffutil import RenderActivity, probe
from .qa.checks import FAIL, fingerprint, run_qa
from .research.registry import SkillRegistry

FORMATS = {"short": ("shorts", "_short"), "long": ("youtube", "_youtube")}


class Orchestrator:
    def __init__(self, cfg: Config, db: Database | None = None):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.db = db or Database(cfg.db_path)
        self.skills = SkillRegistry(self.db)
        self.activity = RenderActivity(cfg.data_dir)
        self.guard = CostGuard(cfg, self.db)
        self._lock = threading.Lock()

    def log(self, message: str, level: str = "info") -> None:
        self.db.log("orchestrateur", message, level)

    # -- enregistrement ------------------------------------------------------
    def register_video(self, path: str | Path) -> int | None:
        """Enregistre un fichier d'INPUT. Retourne None s'il est déjà connu."""
        path = Path(path).resolve()
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
            return None
        fp = fingerprint(path)
        if self.db.one("SELECT id FROM videos WHERE fingerprint=?", (fp,)):
            return None
        st = path.stat()
        return self.db.insert("videos", path=str(path), fingerprint=fp, size=st.st_size,
                              mtime=st.st_mtime, added_at=time.time())

    def enqueue(self, video_id: int, kind: str = "single", params: dict | None = None) -> int:
        return self.db.insert("jobs", video_id=video_id, kind=kind, status="queued",
                              stage="en attente", params=json.dumps(params or {}),
                              created_at=time.time())

    def requeue_interrupted(self) -> int:
        """Après un arrêt brutal : les tâches « running » repartent en file d'attente."""
        rows = self.db.query("SELECT id FROM jobs WHERE status='running'")
        for row in rows:
            self.db.update("jobs", row["id"], status="queued", stage="reprise après arrêt",
                           progress=0)
        self.activity.end()
        return len(rows)

    # -- étapes ----------------------------------------------------------------
    def _stage(self, job_id: int, stage: str, progress: float) -> None:
        self.db.update("jobs", job_id, stage=stage, progress=round(progress, 4))
        self.activity.heartbeat()

    def analyze(self, video: dict, job_id: int | None = None) -> dict:
        path = video["path"]
        info = probe(path)
        self.db.update("videos", video["id"], duration=info.duration, width=info.width,
                       height=info.height, fps=info.fps, has_audio=int(info.has_audio))
        if not info.has_video or info.duration <= 0:
            raise RuntimeError("Fichier sans flux vidéo exploitable.")

        def progress(p: float) -> None:
            if job_id:
                self._stage(job_id, "analyse", 0.02 + 0.18 * p)

        acfg = dict(self.cfg["analysis"])
        accel = self.skills.active("decode_accel")
        if accel:
            acfg["hwaccel"] = accel["params"].get("hwaccel", acfg.get("hwaccel", "auto"))
        result = analysis_mod.analyze_video(path, acfg, info, progress)
        detector = self.skills.active("scene_detector")
        if detector and detector["params"].get("impl") != "builtin":
            from .research.sandbox import run_scene_detector

            try:
                result["scene_cuts"] = run_scene_detector(detector["params"], path)
                result["scene_detector"] = detector["name"]
            except Exception as exc:
                self.log(f"Détecteur de scènes « {detector['name']} » en échec ({exc}) : "
                         "détecteur intégré conservé.", "warn")
        gemini = GeminiAnalyst(self.cfg, self.guard, self.log)
        if gemini.available():
            if job_id:
                self._stage(job_id, "analyse Gemini", 0.21)
            result = gemini.enrich(result, self.cfg.work_dir / f"gemini_{video['id']}")
            shutil.rmtree(self.cfg.work_dir / f"gemini_{video['id']}", ignore_errors=True)
        analysis_path = self.cfg.data_dir / "analysis" / f"video_{video['id']}.json"
        analysis_mod.save_analysis(result, analysis_path)
        self.db.execute("DELETE FROM highlights WHERE video_id=?", (video["id"],))
        for h in result["highlights"]:
            self.db.insert("highlights", video_id=video["id"], start=h["start"], end=h["end"],
                           peak=h["peak"], score=h["score"], label=h["label"],
                           source=h.get("source", "local"), meta=json.dumps(h.get("meta", {})))
        self.db.update("videos", video["id"], status="analyzed",
                       analysis_path=str(analysis_path), analyzed_at=time.time())
        return result

    def _unique(self, directory: Path, stem: str, suffix: str) -> Path:
        candidate = directory / f"{stem}{suffix}.mp4"
        n = 2
        while candidate.exists():
            candidate = directory / f"{stem}{suffix}_v{n}.mp4"
            n += 1
        return candidate

    def _render_format(self, fmt: str, plan: EditPlan, stem: str, job_id: int,
                       video_id: int | None, base: float, span: float,
                       cues: dict, originals: dict[str, str]) -> dict:
        """Rend un format, le contrôle, puis le dépose dans OUTPUT s'il est valide."""
        folder, suffix = FORMATS[fmt]
        work = self.cfg.work_dir / f"job_{job_id}_{fmt}"
        staged = self.cfg.work_dir / f"job_{job_id}_{fmt}.mp4"
        active_profile = self.skills.active("encode_profile") or {}
        active_motion = self.skills.active("motion_preset") or {}
        profile = active_profile.get("params") or None
        preset = active_motion.get("params") or None
        attempts = [(plan, self.cfg["render"]["encoder"])]
        safe = EditPlan.from_dict(plan.to_dict())
        safe.transition, safe.transition_seconds = "cut", 0.0
        # mode prudent : sans effets, sans ralentis (durées et calage recalculés)
        safe.style, safe.graphics, safe.sfx, safe.transitions = {}, [], [], []
        for seg in safe.segments:
            seg.ramp, seg.camera, seg.vfx, seg.focus, seg.events = [], {}, {}, [], []
        attempts.append((safe, "cpu"))  # second essai prudent : CPU + coupes franches
        report: dict = {}
        result: dict = {}
        for attempt, (p, encoder) in enumerate(attempts, 1):
            cfg = self.cfg
            if encoder != self.cfg["render"]["encoder"]:
                cfg = Config(self.cfg.root, {**self.cfg.data,
                                             "render": {**self.cfg["render"],
                                                        "encoder": encoder}})
            renderer = Renderer(cfg, profile if attempt == 1 else None, self.log,
                                preset if attempt == 1 else None)
            label = f"rendu {fmt}" + (" (2e essai)" if attempt > 1 else "")
            try:
                result = renderer.render(
                    p, staged, work,
                    lambda v, label=label: self._stage(job_id, label, base + span * 0.85 * v),
                    cues)
                self._stage(job_id, f"QA {fmt}", base + span * 0.9)
                intact = all(fingerprint(src) == fp for src, fp in originals.items())
                report = run_qa(staged, p.to_dict(), self.cfg, intact, result)
            except Exception as exc:
                report = {"status": FAIL, "checks": [
                    {"name": "rendu", "status": FAIL, "detail": str(exc)[:600], "value": None}]}
                self.log(f"{label} en échec : {str(exc)[:300]}", "error")
            plan_used = p
            if report["status"] != FAIL:
                if attempt > 1:
                    # le mode prudent réussit là où le réglage actif échouait :
                    # les skills non d'origine en production sont mis en quarantaine
                    for kind in ("encode_profile", "motion_preset"):
                        restored = self.skills.quarantine_active(
                            kind, f"rendu {fmt} de la tâche {job_id} en échec")
                        if restored:
                            self.log(f"Skill {kind} mis en quarantaine, retour à "
                                     f"« {restored['name']} ».", "warn")
                break
            if attempt == 1:
                self.log(f"QA {fmt} en échec, nouvel essai en mode prudent.", "warn")
        failed = report["status"] == FAIL
        if failed:
            rejected = self.cfg.data_dir / "rejected"
            rejected.mkdir(parents=True, exist_ok=True)
            final = rejected / f"{stem}{suffix}_job{job_id}.mp4"
        else:
            final = self._unique(self.cfg.output_dir / folder, stem, suffix)
        if staged.exists():
            shutil.move(str(staged), str(final))
        report["path"] = str(final)
        info = probe(final) if final.exists() else None
        self.db.insert(
            "renders", job_id=job_id, video_id=video_id, format=fmt, path=str(final),
            duration=info.duration if info else None, width=info.width if info else None,
            height=info.height if info else None, encoder=result.get("encoder"),
            render_seconds=result.get("render_seconds"), qa_status=report["status"],
            qa_report=json.dumps(report), plan=json.dumps(plan_used.to_dict()),
            created_at=time.time())
        self.log(f"{fmt} : QA {report['status'].upper()} -> {final}",
                 "error" if failed else "info")
        return {"format": fmt, "path": str(final), "qa": report, "render": result,
                "plan": plan_used.to_dict()}

    def _produce(self, job_id: int, analyses: list[dict], stem: str, video_id: int | None,
                 formats: list[str]) -> dict:
        originals = {a["path"]: fingerprint(a["path"]) for a in analyses}
        cues: dict = {}
        for a in analyses:
            found, origin = subs.cues_for_source(a["path"], self.cfg["subtitles"])
            if origin == "whisper-error":
                self.log("Whisper n'a pas pu transcrire (modèle absent ou erreur) : "
                         "pas de sous-titres automatiques pour cette vidéo.", "warn")
            if found:
                cues[a["path"]] = found
                self.log(f"Sous-titres ({origin}) : {len(found)} répliques pour "
                         f"{Path(a['path']).name}")
        plans = {fmt: build_plan(fmt, analyses, self.cfg) for fmt in formats}
        if self.cfg["style"]["enabled"]:
            texts = ClaudeDirector(self.cfg, self.guard, self.log).creative_texts(stem, analyses)
            music_info = self._music_info(stem)
            key = originals[analyses[0]["path"]][:16]
            for fmt, plan in plans.items():
                editing.direct(plan, analyses, self.cfg, texts.get(fmt), key + fmt, music_info)
                self.log(f"{fmt} : style « {plan.style.get('palette_name')} / "
                         f"{plan.style.get('look')} / {plan.style.get('transitions')} », "
                         f"{sum(len(s.events) for s in plan.segments)} moments animés, "
                         f"{len(plan.graphics)} éléments graphiques, {len(plan.sfx)} sons")
        outputs = []
        span = 0.75 / max(1, len(formats))
        self.activity.begin()
        try:
            for i, fmt in enumerate(formats):
                outputs.append(self._render_format(fmt, plans[fmt], stem, job_id, video_id,
                                                   0.22 + i * span, span, cues, originals))
        finally:
            self.activity.end()
        intact = all(fingerprint(src) == fp for src, fp in originals.items())
        meta = ClaudeDirector(self.cfg, self.guard, self.log).metadata(
            stem, {o["format"]: o["plan"] for o in outputs})
        report = {
            "source": [a["path"] for a in analyses], "job_id": job_id,
            "metadata": meta, "originals_intact": intact,
            "ai": analyses[0].get("ai", {"mode": "local"}),
            "outputs": outputs, "at": time.time(),
        }
        report_path = self.cfg.output_dir / "reports" / f"{stem}_job{job_id}.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        report["report_path"] = str(report_path)
        return report

    def _music_info(self, stem: str) -> dict | None:
        """Musique fournie dans assets/music : tempo et temps détectés pour caler les coupes."""
        if not self.cfg["music"]["enabled"]:
            return None
        from .editor.render import pick_music
        from .engines.audio import detect_beats

        found = pick_music(self.cfg.path("music"), stem)
        if not found:
            return None
        try:
            beats = detect_beats(found)
        except Exception as exc:
            self.log(f"Analyse rythmique impossible pour {found.name} : {exc}", "warn")
            return None
        if beats["bpm"] <= 0:
            return None
        self.log(f"Musique {found.name} : {beats['bpm']} BPM détectés.")
        return {"path": str(found), **beats}

    # -- tâches ------------------------------------------------------------------
    def process_job(self, job_id: int) -> dict:
        with self._lock:
            return self._process_job(job_id)

    def _process_job(self, job_id: int) -> dict:
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise ValueError(f"tâche {job_id} inconnue")
        params = json.loads(job["params"] or "{}")
        self.db.update("jobs", job_id, status="running", started_at=time.time(), error=None,
                       stage="démarrage", progress=0.01)
        try:
            formats = params.get("formats") or [
                f for f in ("short", "long") if self.cfg[f]["enabled"]]
            if job["kind"] == "compilation":
                videos = [self.db.one("SELECT * FROM videos WHERE id=?", (vid,))
                          for vid in params["video_ids"]]
                stem = params.get("name") or f"compilation_{job_id}"
            else:
                videos = [self.db.one("SELECT * FROM videos WHERE id=?", (job["video_id"],))]
                stem = Path(videos[0]["path"]).stem
            analyses = []
            for video in videos:
                if not Path(video["path"]).exists():
                    raise FileNotFoundError(f"Source introuvable : {video['path']}")
                if video["analysis_path"] and Path(video["analysis_path"]).exists() \
                        and job["kind"] == "compilation":
                    analyses.append(analysis_mod.load_analysis(Path(video["analysis_path"])))
                else:
                    self.db.update("videos", video["id"], status="analyzing")
                    analyses.append(self.analyze(video, job_id))
            report = self._produce(job_id, analyses, stem,
                                   videos[0]["id"] if len(videos) == 1 else None, formats)
            ok = all(o["qa"]["status"] != FAIL for o in report["outputs"])
            for video in videos:
                self.db.update("videos", video["id"], status="done" if ok else "failed")
            self.db.update("jobs", job_id, status="done" if ok else "failed",
                           stage="terminé" if ok else "QA en échec", progress=1.0,
                           finished_at=time.time(),
                           error=None if ok else "Au moins un rendu a échoué à la QA.")
            return report
        except Exception as exc:
            self.db.update("jobs", job_id, status="failed", stage="erreur",
                           error=f"{type(exc).__name__}: {exc}"[:1000],
                           finished_at=time.time())
            if job["video_id"]:
                self.db.update("videos", job["video_id"], status="failed")
            self.log(f"Tâche {job_id} en échec : {exc}\n{traceback.format_exc()[-800:]}",
                     "error")
            raise

    def process_file(self, path: str | Path, formats: list[str] | None = None) -> dict:
        """Traitement direct d'un fichier (CLI)."""
        path = Path(path).resolve()
        video_id = self.register_video(path)
        if video_id is None:
            row = self.db.one("SELECT id FROM videos WHERE fingerprint=?", (fingerprint(path),))
            if not row:
                raise ValueError(f"Fichier non pris en charge : {path}")
            video_id = row["id"]
        job_id = self.enqueue(video_id, params={"formats": formats} if formats else None)
        return self.process_job(job_id)

    def compile(self, video_ids: list[int], name: str | None = None,
                formats: list[str] | None = None) -> int:
        """Met en file une compilation multi-vidéos (ex. plusieurs clips courts -> 1 YouTube)."""
        return self.enqueue(video_ids[0], "compilation",
                            {"video_ids": video_ids, "name": name,
                             "formats": formats or ["long"]})

    def next_job(self) -> dict | None:
        return self.db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
