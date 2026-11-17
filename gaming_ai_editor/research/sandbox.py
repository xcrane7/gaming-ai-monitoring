"""Bac à sable : chaque outil externe vit dans son propre venv et s'exécute hors processus.

Garanties :
- l'environnement Python de l'application n'est jamais modifié ;
- installation depuis PyPI en version épinglée, roues binaires uniquement
  (aucun script d'installation arbitraire exécuté) ;
- les variables d'environnement sensibles (clés, jetons) ne sont pas transmises ;
- tout appel a un délai maximal.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..ffutil import IS_WINDOWS, _popen_kwargs

RUNNERS = Path(__file__).resolve().parent / "runners"
_SENSITIVE = re.compile(r"(?i)(key|token|secret|password|passwd|credential|auth)")


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not _SENSITIVE.search(k)}


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, data_dir: Path, timeout: int = 600):
        self.root = Path(data_dir) / "sandbox"
        self.timeout = timeout

    def path(self, name: str, version: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{name}-{version}")
        return self.root / safe

    def create(self, name: str, version: str, requirements: list[str]) -> Path:
        """Crée le venv et installe les paquets épinglés. Retourne l'interpréteur."""
        venv = self.path(name, version)
        python = venv_python(venv)
        marker = venv / ".installed"
        if marker.exists() and python.exists():
            return python
        shutil.rmtree(venv, ignore_errors=True)
        venv.parent.mkdir(parents=True, exist_ok=True)
        self._run([sys.executable, "-m", "venv", str(venv)], "création du venv")
        self._run([str(python), "-m", "pip", "install", "--no-input",
                   "--disable-pip-version-check", "--only-binary=:all:", *requirements],
                  "installation des paquets")
        marker.write_text(json.dumps({"requirements": requirements, "at": time.time()}),
                          encoding="utf-8")
        return python

    def _run(self, cmd: list[str], what: str) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.timeout, env=clean_env(),
                                  **_popen_kwargs(low_priority=True))
        except subprocess.TimeoutExpired:
            raise SandboxError(f"{what} : délai dépassé ({self.timeout}s)")
        except OSError as exc:
            raise SandboxError(f"{what} : {exc}")
        if proc.returncode != 0:
            raise SandboxError(f"{what} : code {proc.returncode} — "
                               f"{(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc.stdout

    def remove(self, name: str, version: str) -> None:
        shutil.rmtree(self.path(name, version), ignore_errors=True)


def run_runner(python: str | Path, runner: str, payload: dict, timeout: int = 600,
               low_priority: bool = False) -> dict:
    """Lance un runner dans l'interpréteur isolé et retourne son JSON."""
    script = RUNNERS / runner
    if not script.is_file() or script.parent != RUNNERS:
        raise SandboxError(f"runner inconnu : {runner}")
    if not Path(python).exists():
        raise SandboxError(f"interpréteur du bac à sable introuvable : {python}")
    try:
        proc = subprocess.run([str(python), "-I", str(script)], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=clean_env(),
                              **_popen_kwargs(low_priority))
    except subprocess.TimeoutExpired:
        raise SandboxError(f"{runner} : délai dépassé ({timeout}s)")
    if proc.returncode != 0:
        raise SandboxError(f"{runner} : code {proc.returncode} — {proc.stderr.strip()[-500:]}")
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise SandboxError(f"{runner} : sortie illisible")


def run_scene_detector(params: dict, video: str | Path, timeout: int = 1800) -> list[float]:
    """Utilisé par le pipeline quand un détecteur de scènes externe est le skill actif."""
    result = run_runner(params["python"], params["runner"],
                        {"video": str(video), "threshold": params.get("threshold", 27.0)},
                        timeout)
    cuts = result.get("cuts")
    if not isinstance(cuts, list) or not all(isinstance(c, (int, float)) for c in cuts):
        raise SandboxError("résultat du détecteur invalide")
    return [float(c) for c in cuts]
