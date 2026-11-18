"""Dashboard web local (bibliothèque standard uniquement, écoute sur 127.0.0.1)."""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__
from ..ai.providers import provider_status
from ..analysis import downsample, load_analysis
from ..editor.render import AUDIO_EXT
from ..ffutil import resolve_encoder_family
from ..pipeline import Orchestrator

STATIC = Path(__file__).resolve().parent / "static"


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, json.JSONDecodeError):
        return default


def build_state(orch: Orchestrator, scheduler=None) -> dict:
    db, cfg = orch.db, orch.cfg
    env_file = cfg.data_dir / "environment.json"
    env = _loads(env_file.read_text(encoding="utf-8"), {}) if env_file.exists() else {}
    renders = db.query(
        "SELECT r.*, v.path AS source FROM renders r LEFT JOIN videos v ON v.id = r.video_id "
        "ORDER BY r.id DESC LIMIT 40")
    for r in renders:
        r["qa_report"] = _loads(r.pop("qa_report"), {})
        plan = _loads(r.pop("plan"), {})
        r["clips"] = len(plan.get("segments", []))
        style = plan.get("style") or {}
        r["style"] = (f"{style.get('palette_name')} · {style.get('look')} · "
                      f"{style.get('transitions')}") if style.get("palette_name") else ""
        r["effects"] = sum(len(seg.get("events", [])) for seg in plan.get("segments", []))
        r["graphics"] = len(plan.get("graphics", []))
        r["motion_score"] = r["qa_report"].get("motion_score")
        r["notes"] = plan.get("notes", [])
        r["name"] = Path(r["path"]).name
        r["exists"] = Path(r["path"]).exists()
        r["source"] = Path(r["source"]).name if r.get("source") else "compilation"
    videos = db.query("SELECT id, path, status, duration, width, height, fps, added_at, "
                      "analyzed_at FROM videos ORDER BY id DESC LIMIT 40")
    for v in videos:
        v["name"] = Path(v["path"]).name
        v["highlights"] = db.one("SELECT COUNT(*) AS n FROM highlights WHERE video_id=?",
                                 (v["id"],))["n"]
    skills = db.query("SELECT id, kind, name, version, status, license, origin, score, metrics, "
                      "created_at, activated_at FROM skills ORDER BY id DESC LIMIT 60")
    for s in skills:
        s["verdict"] = _loads(s.pop("metrics"), {}).get("verdict", "")
    music = [p.name for p in cfg.path("music").glob("*") if p.suffix.lower() in AUDIO_EXT]
    sfx = [p.name for p in cfg.path("sfx").glob("*") if p.suffix.lower() in AUDIO_EXT]
    runs = db.query("SELECT id, started_at, finished_at, status FROM research_runs "
                    "ORDER BY id DESC LIMIT 8")
    return {
        "version": __version__,
        "now": time.time(),
        "paths": {"input": str(cfg.input_dir), "output": str(cfg.output_dir)},
        "environment": env,
        "encoder": resolve_encoder_family(cfg["render"]["encoder"]),
        "rendering": orch.activity.is_active(),
        "ai": provider_status(cfg),
        "ai_calls": db.query("SELECT at, provider, purpose, allowed, cost_class, detail "
                             "FROM ai_calls ORDER BY id DESC LIMIT 10"),
        "assets": {"music": music, "sfx": sfx},
        "jobs": db.query("SELECT j.*, v.path AS source FROM jobs j LEFT JOIN videos v "
                         "ON v.id = j.video_id ORDER BY j.id DESC LIMIT 25"),
        "videos": videos,
        "renders": renders,
        "packages": packages(cfg),
        "research": {
            "enabled": bool(cfg["research"]["enabled"]),
            "scheduler": scheduler is not None,
            "interval_hours": cfg["research"]["interval_hours"],
            "next_run_at": db.kv_get("research.next_run_at"),
            "running": bool(db.one("SELECT id FROM research_runs WHERE status='running'")),
            "runs": runs,
            "skills": skills,
            "history": orch.skills.history(15),
            "discoveries": db.query(
                "SELECT source, name, version, license, license_ok, url, summary, status "
                "FROM discoveries ORDER BY license_ok DESC, source, name LIMIT 60"),
        },
        "events": db.query("SELECT at, level, source, message FROM events "
                           "ORDER BY id DESC LIMIT 60"),
    }


def packages(cfg, limit: int = 10) -> list[dict]:
    """Emballages YouTube (OUTPUT/reports/<vidéo>.metadata.json), du plus récent au plus ancien."""
    out = []
    files = sorted((cfg.output_dir / "reports").glob("*.metadata.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        data = _loads(f.read_text(encoding="utf-8"), None)
        if not isinstance(data, dict):
            continue
        thumb = (data.get("thumbnail") or {}).get("path")
        data["id"] = f.name[: -len(".metadata.json")]
        data["has_thumbnail"] = bool(thumb and Path(thumb).is_file())
        out.append(data)
    return out


def package_thumbnail(cfg, pkg_id: str) -> Path | None:
    """Miniature d'un emballage, seulement si elle est bien dans OUTPUT (pas de chemin arbitraire)."""
    f = cfg.output_dir / "reports" / f"{pkg_id}.metadata.json"
    if "/" in pkg_id or "\\" in pkg_id or not f.is_file():
        return None
    thumb = (_loads(f.read_text(encoding="utf-8"), {}).get("thumbnail") or {}).get("path")
    if not thumb:
        return None
    path = Path(thumb).resolve()
    root = cfg.output_dir.resolve()
    return path if path.is_file() and root in path.parents else None


def video_timeline(orch: Orchestrator, video_id: int) -> dict | None:
    video = orch.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video or not video["analysis_path"] or not Path(video["analysis_path"]).exists():
        return None
    a = load_analysis(Path(video["analysis_path"]))
    return {"duration": a["duration"], "score": downsample(a["score"], 500),
            "highlights": a["highlights"][:40], "scene_cuts": a["scene_cuts"][:200],
            "backend": a.get("backend"), "ai": a.get("ai")}


class Handler(BaseHTTPRequestHandler):
    orch: Orchestrator
    scheduler = None
    server_version = "GamingAIEditor"

    def log_message(self, *args) -> None:  # silence : le journal applicatif suffit
        pass

    # -- réponses ---------------------------------------------------------------
    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self._json({"error": "introuvable"}, 404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        partial = False
        if match and (match.group(1) or match.group(2)):
            partial = True
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            else:
                start = max(0, size - int(match.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type or
                         mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # -- routes -------------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                self._file(STATIC / "index.html", "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(build_state(self.orch, self.scheduler))
            elif m := re.fullmatch(r"/api/video/(\d+)/timeline", path):
                data = video_timeline(self.orch, int(m.group(1)))
                self._json(data or {"error": "analyse indisponible"}, 200 if data else 404)
            elif m := re.fullmatch(r"/thumbnail/([^/]+)", path):
                from urllib.parse import unquote

                thumb = package_thumbnail(self.orch.cfg, unquote(m.group(1)))
                if thumb:
                    self._file(thumb, "image/png")
                else:
                    self._json({"error": "miniature introuvable"}, 404)
            elif m := re.fullmatch(r"/media/(\d+)", path):
                row = self.orch.db.one("SELECT path FROM renders WHERE id=?", (int(m.group(1)),))
                if row:
                    self._file(Path(row["path"]), "video/mp4")
                else:
                    self._json({"error": "rendu inconnu"}, 404)
            else:
                self._json({"error": "route inconnue"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _same_origin(self) -> bool:
        """Refuse les requêtes d'écriture venant d'un autre site (protection CSRF)."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return origin is None or origin in (f"http://{host}", f"https://{host}")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._same_origin():
            self._json({"error": "origine refusée"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = _loads(self.rfile.read(length).decode("utf-8", "replace") if length else "", {})
        try:
            if path == "/api/research/run":
                if self.orch.db.one("SELECT id FROM research_runs WHERE status='running'"):
                    self._json({"ok": False, "message": "Un cycle est déjà en cours."}, 409)
                elif self.scheduler:
                    self.scheduler.trigger()
                    self._json({"ok": True, "message": "Cycle de recherche lancé."})
                else:
                    from ..research.researcher import SkillsResearcher

                    researcher = SkillsResearcher(self.orch.cfg, self.orch.db,
                                                  self.orch.activity)
                    threading.Thread(target=researcher.run_cycle, daemon=True).start()
                    self._json({"ok": True, "message": "Cycle de recherche lancé."})
            elif path == "/api/skills/rollback":
                restored = self.orch.skills.rollback(str(body.get("kind", "")),
                                                     "rollback depuis le dashboard",
                                                     quarantine=False)
                self._json({"ok": bool(restored),
                            "message": f"Version restaurée : {restored['name']}" if restored
                            else "Aucune version précédente à restaurer."})
            elif m := re.fullmatch(r"/api/jobs/(\d+)/retry", path):
                job = self.orch.db.one("SELECT * FROM jobs WHERE id=?", (int(m.group(1)),))
                if not job or job["status"] not in ("failed", "done"):
                    self._json({"ok": False, "message": "Tâche introuvable ou en cours."}, 409)
                else:
                    new_id = self.orch.db.insert(
                        "jobs", video_id=job["video_id"], kind=job["kind"], status="queued",
                        stage="en attente", params=job["params"], created_at=time.time())
                    self._json({"ok": True, "message": f"Tâche {new_id} mise en file."})
            else:
                self._json({"error": "route inconnue"}, 404)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)


def make_server(orch: Orchestrator, scheduler=None, host: str = "127.0.0.1",
                port: int = 8765) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"orch": orch, "scheduler": scheduler})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(orch: Orchestrator, scheduler=None, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = make_server(orch, scheduler, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
