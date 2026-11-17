"""Intégration IA optionnelle (Gemini analyste, Claude directeur) sous garde-fou de coûts.

Principes :
- Le système fonctionne entièrement sans IA (heuristiques locales).
- Par défaut seuls les CLI de tes abonnements (`gemini`, `claude`) sont utilisés,
  s'ils sont installés et connectés : aucun achat d'API ni de crédits.
- Tout appel à une API payante exige `ai.allow_paid_api: true` dans config.json
  ET une clé fournie par toi. Sinon l'appel est refusé et journalisé.
- Toute réponse d'IA est validée ; en cas d'erreur on garde le résultat local.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from ..db import Database
from ..ffutil import _popen_kwargs, run_ffmpeg

LABELS = ["kill", "victoire", "fail", "drôle", "action", "rien"]


class CostGuard:
    """Point de passage unique de toute dépense potentielle."""

    def __init__(self, cfg, db: Database | None = None):
        self.cfg = cfg
        self.db = db

    def authorize(self, provider: str, purpose: str, cost_class: str) -> bool:
        if cost_class == "paid_api":
            allowed = bool(self.cfg["ai"]["allow_paid_api"])
            detail = ("autorisé par ai.allow_paid_api" if allowed
                      else "REFUSÉ : API payante sans autorisation explicite")
        else:  # local | subscription_cli
            allowed, detail = True, "aucun coût supplémentaire"
        if self.db:
            self.db.insert("ai_calls", at=time.time(), provider=provider, purpose=purpose,
                           allowed=int(allowed), cost_class=cost_class, detail=detail)
        return allowed


def extract_json(text: str):
    """Extrait le premier objet/tableau JSON valide d'une réponse de modèle."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(text[i:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("Aucun JSON dans la réponse")


def run_cli(command: str, prompt: str, timeout: int, extra_args: list[str] | None = None,
            cwd: Path | None = None) -> str:
    exe = shutil.which(command)
    if not exe:
        raise FileNotFoundError(command)
    proc = subprocess.run([exe, *(extra_args or []), "-p", prompt], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=cwd, **_popen_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"{command} code {proc.returncode}: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def provider_status(cfg) -> dict:
    """État des accès IA, affiché dans le dashboard."""
    out = {"allow_paid_api": bool(cfg["ai"]["allow_paid_api"])}
    for name, env_key in (("gemini", "GEMINI_API_KEY"), ("claude", None)):
        conf = cfg["ai"][name]
        cli = shutil.which(conf["command"])
        mode = conf["mode"]
        if mode == "off":
            state = "désactivé"
        elif cli and mode in ("auto", "cli"):
            state = "CLI d'abonnement détecté"
        elif (env_key and mode in ("auto", "api") and os.environ.get(env_key)
              and cfg["ai"]["allow_paid_api"]):
            state = "API autorisée"
        else:
            state = "indisponible — mode local"
        out[name] = {"mode": mode, "cli": cli, "state": state}
    return out


class GeminiAnalyst:
    """Qualifie les moments forts (kill, victoire, fail, drôle...) à partir d'images clés."""

    MAX_MOMENTS = 10

    def __init__(self, cfg, guard: CostGuard, log=print):
        self.cfg, self.guard, self.log = cfg, guard, log
        self.conf = cfg["ai"]["gemini"]

    def _route(self) -> str | None:
        mode = self.conf["mode"]
        if mode == "off":
            return None
        if mode in ("auto", "cli") and shutil.which(self.conf["command"]):
            return "cli"
        if mode in ("auto", "api") and os.environ.get("GEMINI_API_KEY"):
            return "api"
        return None

    def available(self) -> bool:
        return self._route() is not None

    def _frames(self, analysis: dict, work: Path) -> list[tuple[int, Path]]:
        work.mkdir(parents=True, exist_ok=True)
        frames = []
        for i, h in enumerate(analysis["highlights"][: self.MAX_MOMENTS]):
            out = work / f"moment_{i:02d}.jpg"
            try:
                run_ffmpeg(["-ss", f"{h['peak']:.2f}", "-i", analysis["path"], "-frames:v", "1",
                            "-vf", "scale=640:-2", "-q:v", "5", str(out)])
                frames.append((i, out))
            except Exception:
                continue
        return frames

    def _prompt(self, analysis: dict, frames: list[tuple[int, Path]], with_refs: bool) -> str:
        lines = [
            "Tu es l'analyste d'un monteur vidéo gaming. Voici des images extraites de moments "
            "forts détectés dans un gameplay. Pour chaque moment, donne une étiquette parmi "
            f"{LABELS} et un intérêt de 0 à 10 pour un montage.",
            "Réponds UNIQUEMENT avec un tableau JSON : "
            '[{"index": 0, "label": "kill", "interest": 8, "comment": "..."}].',
            "Moments :",
        ]
        for i, path in frames:
            h = analysis["highlights"][i]
            ref = f" image: @{path.name}" if with_refs else ""
            lines.append(f"- index {i} : t={h['peak']:.1f}s, score local {h['score']:.2f}{ref}")
        return "\n".join(lines)

    def _call_api(self, prompt: str, frames: list[tuple[int, Path]]) -> str:
        model = self.conf.get("model") or "gemini-2.5-flash"
        parts: list[dict] = [{"text": prompt}]
        for _, path in frames:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(path.read_bytes()).decode()}})
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps({"contents": [{"parts": parts}]}).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        with urllib.request.urlopen(req, timeout=int(self.conf["timeout"])) as resp:
            data = json.loads(resp.read())
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def enrich(self, analysis: dict, work: Path) -> dict:
        """Ajoute étiquettes et intérêt aux moments forts. Ne lève jamais d'exception."""
        route = self._route()
        if not route or not analysis.get("highlights"):
            return analysis
        cost_class = "subscription_cli" if route == "cli" else "paid_api"
        if not self.guard.authorize("gemini", "qualification des moments forts", cost_class):
            self.log("Gemini (API) refusé par le garde-fou des coûts : analyse locale conservée.")
            return analysis
        try:
            frames = self._frames(analysis, work)
            if not frames:
                return analysis
            if route == "cli":
                extra = ["-m", self.conf["model"]] if self.conf.get("model") else []
                text = run_cli(self.conf["command"], self._prompt(analysis, frames, True),
                               int(self.conf["timeout"]), extra, cwd=work)
            else:
                text = self._call_api(self._prompt(analysis, frames, False), frames)
            verdicts = extract_json(text)
            applied = 0
            for v in verdicts if isinstance(verdicts, list) else []:
                i = v.get("index")
                if not isinstance(i, int) or not 0 <= i < len(analysis["highlights"]):
                    continue
                h = analysis["highlights"][i]
                label = str(v.get("label", "")).lower()
                interest = v.get("interest")
                if label in LABELS:
                    h["label"] = label
                if isinstance(interest, (int, float)) and 0 <= interest <= 10:
                    # le score local est pondéré par l'intérêt jugé par Gemini (x0.2 à x2.0)
                    h["score"] = round(h["score"] * (0.2 + 0.18 * float(interest)), 4)
                    h["meta"] = {**h.get("meta", {}), "gemini_interest": interest,
                                 "comment": str(v.get("comment", ""))[:200]}
                h["source"] = "gemini"
                applied += 1
            analysis["highlights"].sort(key=lambda h: h["score"], reverse=True)
            analysis["ai"] = {"gemini": route, "moments_qualifiés": applied}
            self.log(f"Gemini ({route}) : {applied} moments qualifiés.")
        except Exception as exc:  # jamais bloquant
            self.log(f"Gemini indisponible ({type(exc).__name__}: {str(exc)[:120]}) : "
                     "analyse locale conservée.")
        return analysis


class ClaudeDirector:
    """Titre, description et hashtags proposés par Claude (CLI de l'abonnement)."""

    def __init__(self, cfg, guard: CostGuard, log=print):
        self.cfg, self.guard, self.log = cfg, guard, log
        self.conf = cfg["ai"]["claude"]

    def available(self) -> bool:
        return self.conf["mode"] in ("auto", "cli") and bool(shutil.which(self.conf["command"]))

    @staticmethod
    def local_metadata(stem: str, plans: dict) -> dict:
        title = stem.replace("_", " ").replace("-", " ").strip().title() or "Gameplay"
        return {
            "title": f"{title} — Meilleurs moments",
            "description": "Montage automatique des meilleurs moments de la session.",
            "hashtags": ["#gaming", "#gameplay", "#highlights", "#shorts"],
            "source": "local",
        }

    def metadata(self, stem: str, plans: dict) -> dict:
        meta = self.local_metadata(stem, plans)
        if not self.available():
            return meta
        if not self.guard.authorize("claude", "titre et description", "subscription_cli"):
            return meta
        moments = []
        for fmt, plan in plans.items():
            for seg in plan["segments"][:12]:
                moments.append(f"{fmt}: {seg['label'] or 'action'} à {seg['peak']:.0f}s "
                               f"(score {seg['score']:.2f})")
        prompt = (
            "Tu es le directeur éditorial d'une chaîne gaming. Propose un titre accrocheur "
            "(max 70 caractères), une description (max 300 caractères) et 5 hashtags pour "
            f"une vidéo issue du fichier « {stem} ». Moments retenus :\n" + "\n".join(moments) +
            '\nRéponds UNIQUEMENT en JSON : {"title": "...", "description": "...", '
            '"hashtags": ["#..."]}')
        try:
            extra = ["--model", self.conf["model"]] if self.conf.get("model") else []
            data = extract_json(run_cli(self.conf["command"], prompt,
                                        int(self.conf["timeout"]), extra))
            if isinstance(data, dict) and isinstance(data.get("title"), str) and data["title"]:
                meta["title"] = data["title"][:100]
                if isinstance(data.get("description"), str):
                    meta["description"] = data["description"][:500]
                tags = data.get("hashtags")
                if isinstance(tags, list) and tags:
                    meta["hashtags"] = [str(t)[:40] for t in tags[:8]]
                meta["source"] = "claude"
        except Exception as exc:
            self.log(f"Claude indisponible ({type(exc).__name__}: {str(exc)[:120]}) : "
                     "métadonnées locales conservées.")
        return meta
