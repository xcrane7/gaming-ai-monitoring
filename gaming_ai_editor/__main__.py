"""Ligne de commande : python -m gaming_ai_editor <commande>."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config


def _print_report(report: dict) -> None:
    meta = report.get("metadata", {})
    print(f"\nTitre proposé : {meta.get('title')} (source : {meta.get('source')})")
    for out in report["outputs"]:
        qa = out["qa"]
        print(f"\n[{out['format'].upper()}] QA {qa['status'].upper()} -> {out['path']}")
        for check in qa["checks"]:
            mark = {"pass": "ok ", "warn": "/!\\", "fail": "XX "}[check["status"]]
            print(f"   {mark} {check['name']}: {check['detail']}")
    print(f"\nOriginaux intacts : {'oui' if report['originals_intact'] else 'NON'}")
    print(f"Rapport : {report.get('report_path')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gaming_ai_editor",
                                     description="Montage vidéo gaming automatisé, 100 % local.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="Inspecte l'environnement (FFmpeg, GPU, modules).")
    p = sub.add_parser("process", help="Traite un fichier (ou tout INPUT) immédiatement.")
    p.add_argument("file", nargs="?", help="Vidéo à traiter ; par défaut, tout le dossier INPUT.")
    p.add_argument("--format", choices=["short", "long"], action="append",
                   help="Limiter à un format (répétable).")
    p = sub.add_parser("run", help="Lance tout : surveillance INPUT, dashboard, chercheur.")
    p.add_argument("--no-dashboard", action="store_true")
    p.add_argument("--no-research", action="store_true")
    p.add_argument("--once", action="store_true",
                   help="Traite ce qui est dans INPUT puis s'arrête.")
    sub.add_parser("dashboard", help="Dashboard seul (lecture de l'état).")
    p = sub.add_parser("research", help="Skills Researcher.")
    p.add_argument("action", choices=["run", "status", "rollback"])
    p.add_argument("--kind", help="Type de skill pour le rollback (ex. encode_profile).")
    p.add_argument("--offline", action="store_true", help="Sans accès réseau.")
    sub.add_parser("benchmark", help="Mesure les encodeurs disponibles.")
    p = sub.add_parser("compile", help="Compilation de plusieurs vidéos déjà analysées.")
    p.add_argument("video_ids", nargs="+", type=int)
    p.add_argument("--name")
    sub.add_parser("status", help="État des vidéos, tâches et rendus.")
    sub.add_parser("sfx", help="Génère des effets sonores libres de droits dans assets/sfx.")
    args = parser.parse_args(argv)

    cfg = Config()
    cfg.ensure_dirs()

    if args.command == "inspect":
        from .env import format_report, inspect

        report = inspect(cfg)
        print(format_report(report))
        return 0 if report["ready"] else 1

    from .ffutil import have_ffmpeg

    if not have_ffmpeg():
        print("FFmpeg introuvable. Lance d'abord : python -m gaming_ai_editor inspect")
        return 2

    if args.command == "sfx":
        from . import synth

        for path in synth.make_sfx(cfg.path("sfx")):
            print(f"Créé : {path}")
        return 0

    from .pipeline import Orchestrator

    orch = Orchestrator(cfg)

    if args.command == "process":
        from .watcher import list_inputs

        files = [Path(args.file)] if args.file else list_inputs(cfg.input_dir)
        if not files:
            print(f"Aucune vidéo dans {cfg.input_dir}")
            return 0
        code = 0
        for file in files:
            print(f"=== Traitement : {file}")
            try:
                report = orch.process_file(file, args.format)
                _print_report(report)
                if any(o["qa"]["status"] == "fail" for o in report["outputs"]):
                    code = 1
            except Exception as exc:
                print(f"ÉCHEC : {exc}")
                code = 1
        return code

    if args.command == "status":
        for row in orch.db.query("SELECT id, path, status, duration FROM videos ORDER BY id"):
            print(f"vidéo {row['id']}: {row['status']:<10} {row['duration'] or 0:8.1f}s "
                  f"{row['path']}")
        for row in orch.db.query(
                "SELECT id, kind, status, stage, progress, error FROM jobs ORDER BY id"):
            print(f"tâche {row['id']}: {row['kind']} {row['status']} — {row['stage']} "
                  f"({(row['progress'] or 0) * 100:.0f}%) {row['error'] or ''}")
        for row in orch.db.query(
                "SELECT format, qa_status, duration, path FROM renders ORDER BY id"):
            print(f"rendu {row['format']}: QA {row['qa_status']} {row['duration'] or 0:.1f}s "
                  f"{row['path']}")
        return 0

    if args.command == "benchmark":
        from .qa.benchmark import run_benchmark

        result = run_benchmark(cfg)
        orch.db.insert("benchmarks", name="encodeurs", at=time.time(),
                       results=json.dumps(result))
        return 0

    if args.command == "compile":
        job_id = orch.compile(args.video_ids, args.name)
        _print_report(orch.process_job(job_id))
        return 0

    if args.command == "research":
        from .research.researcher import SkillsResearcher

        researcher = SkillsResearcher(cfg, orch.db, orch.activity)
        if args.action == "run":
            summary = researcher.run_cycle(offline=args.offline)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        elif args.action == "status":
            print(json.dumps(researcher.status(), indent=2, ensure_ascii=False, default=str))
        else:
            if not args.kind:
                print("--kind est requis pour un rollback.")
                return 2
            restored = researcher.registry.rollback(args.kind, quarantine=False)
            print(f"Version restaurée : {restored['name']} v{restored['version']}"
                  if restored else "Aucune version précédente à restaurer.")
        return 0

    if args.command in ("run", "dashboard"):
        from .watcher import Service

        service = None
        scheduler = None
        if args.command == "run":
            service = Service(orch)
            service.start()
            if args.once:
                # un passage de détection sans attendre le délai de stabilité
                for file in __import__("gaming_ai_editor.watcher", fromlist=["x"]).list_inputs(
                        cfg.input_dir):
                    video_id = orch.register_video(file)
                    if video_id is not None:
                        orch.enqueue(video_id)
                service.run_until_idle()
                service.stop()
                return 0
            if cfg["research"]["enabled"] and not args.no_research:
                from .research.researcher import ResearchScheduler

                scheduler = ResearchScheduler(cfg, orch.db, orch.activity)
                scheduler.start()
        if args.command == "dashboard" or not args.no_dashboard:
            from .dashboard.server import serve

            host, port = cfg["dashboard"]["host"], int(cfg["dashboard"]["port"])
            print(f"Dashboard : http://{host}:{port}  (Ctrl+C pour arrêter)")
            try:
                serve(orch, scheduler, host, port)
            except KeyboardInterrupt:
                pass
        else:
            print(f"Surveillance de {cfg.input_dir} (Ctrl+C pour arrêter)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        if service:
            service.stop()
        if scheduler:
            scheduler.stop()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
