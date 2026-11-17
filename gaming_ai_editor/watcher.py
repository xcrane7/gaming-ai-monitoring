"""Surveillance du dossier INPUT et file de traitement.

Un fichier n'est pris en charge que lorsque sa taille est stable (un
enregistrement OBS / ShadowPlay en cours n'est pas traité à moitié).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .config import VIDEO_EXTENSIONS
from .pipeline import Orchestrator


class InputWatcher:
    def __init__(self, orch: Orchestrator):
        self.orch = orch
        self.cfg = orch.cfg
        self._seen: dict[str, tuple[int, float, float]] = {}  # chemin -> (taille, mtime, depuis)
        self._known: set[tuple[str, int, float]] = set()

    def scan(self, now: float | None = None) -> list[int]:
        """Un passage : retourne les identifiants des nouvelles tâches créées."""
        now = now if now is not None else time.time()
        stable = float(self.cfg["watcher"]["stable_seconds"])
        created: list[int] = []
        present = set()
        for path in sorted(self.cfg.input_dir.rglob("*")):
            if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
                continue
            if path.name.startswith(".") or ".partial" in path.name:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            present.add(key)
            if (key, st.st_size, st.st_mtime) in self._known:
                continue
            seen = self._seen.get(key)
            if not seen or seen[0] != st.st_size or seen[1] != st.st_mtime:
                self._seen[key] = (st.st_size, st.st_mtime, now)
                continue
            if now - seen[2] < stable or st.st_size == 0:
                continue
            self._known.add((key, st.st_size, st.st_mtime))
            self._seen.pop(key, None)
            try:
                video_id = self.orch.register_video(path)
            except OSError as exc:
                self.orch.log(f"Lecture impossible de {path.name} : {exc}", "warn")
                continue
            if video_id is not None:
                job_id = self.orch.enqueue(video_id)
                created.append(job_id)
                self.orch.log(f"Nouveau fichier détecté : {path.name} (tâche {job_id})")
        for key in list(self._seen):
            if key not in present:
                del self._seen[key]
        return created


class Service:
    """Threads de fond : surveillance d'INPUT + exécution des tâches (une à la fois)."""

    def __init__(self, orch: Orchestrator):
        self.orch = orch
        self.watcher = InputWatcher(orch)
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []

    def _watch_loop(self) -> None:
        poll = float(self.orch.cfg["watcher"]["poll_seconds"])
        while not self.stop_event.is_set():
            try:
                self.watcher.scan()
            except Exception as exc:  # la surveillance ne doit jamais mourir
                self.orch.log(f"Erreur de surveillance : {exc}", "error")
            self.stop_event.wait(poll)

    def _work_loop(self) -> None:
        while not self.stop_event.is_set():
            job = self.orch.next_job()
            if not job:
                self.stop_event.wait(2)
                continue
            from . import resources

            # un seul rendu à la fois (verrou de l'orchestrateur) et assez de mémoire libre
            resources.wait_for_room(1.5, 0.5, timeout=300, stop=self.stop_event,
                                    log=lambda m: self.orch.log(m, "warn"))
            if self.stop_event.is_set():
                break
            try:
                self.orch.process_job(job["id"])
            except Exception:
                pass  # déjà journalisé et marqué « failed »

    def start(self) -> None:
        recovered = self.orch.requeue_interrupted()
        if recovered:
            self.orch.log(f"{recovered} tâche(s) interrompue(s) remise(s) en file d'attente.")
        for target in (self._watch_loop, self._work_loop):
            thread = threading.Thread(target=target, daemon=True, name=target.__name__)
            thread.start()
            self.threads.append(thread)

    def stop(self) -> None:
        self.stop_event.set()

    def run_until_idle(self, timeout: float = 3600) -> None:
        """Pour les tests / le mode « une passe » : attend que la file soit vide."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            busy = self.orch.db.one(
                "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
            if not busy["n"]:
                return
            time.sleep(0.5)
        raise TimeoutError("File de traitement non vidée dans le délai imparti.")


def list_inputs(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.rglob("*")
                  if p.suffix.lower() in VIDEO_EXTENSIONS and p.is_file())
