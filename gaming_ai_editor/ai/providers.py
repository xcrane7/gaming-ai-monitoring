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
import urllib.error
import urllib.request
from pathlib import Path

from ..db import Database
from ..ffutil import _popen_kwargs, run_ffmpeg

LABELS = ["kill", "headshot", "victoire", "fail", "drôle", "action", "rien"]


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
        elif cost_class == "free_tier_api":
            # clé gratuite d'un projet sans facturation : Google refuse (erreur 429) au-delà
            # du quota gratuit au lieu de facturer. Plafond quotidien en plus, par prudence.
            limit = int(self.cfg["ai"]["gemini"].get("max_requests_per_day", 20))
            used = self._count_today(provider, cost_class)
            allowed = used < limit
            detail = (f"clé gratuite, {used + 1}/{limit} aujourd'hui" if allowed
                      else f"REFUSÉ : plafond quotidien atteint ({limit} requêtes)")
        else:  # local | subscription_cli
            allowed, detail = True, "aucun coût supplémentaire"
        if self.db:
            self.db.insert("ai_calls", at=time.time(), provider=provider, purpose=purpose,
                           allowed=int(allowed), cost_class=cost_class, detail=detail)
        return allowed


    def _count_today(self, provider: str, cost_class: str) -> int:
        if not self.db:
            return 0
        start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        row = self.db.one("SELECT COUNT(*) AS n FROM ai_calls WHERE provider=? AND cost_class=? "
                          "AND allowed=1 AND at>=?", (provider, cost_class, start))
        return int(row["n"]) if row else 0


def gemini_api_key(cfg) -> str | None:
    """Clé Gemini : variable d'environnement GEMINI_API_KEY ou fichier gemini_key.txt du projet."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    path = Path(cfg.root) / "gemini_key.txt"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


GEMINI_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]


class QuotaExhausted(RuntimeError):
    pass


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


SHORT_INSTRUCTION = "Reponds a la demande ci-dessus, uniquement en JSON."


def run_cli(command: str, prompt: str, timeout: int, extra_args: list[str] | None = None,
            cwd: Path | None = None, arg_prompt: str = SHORT_INSTRUCTION) -> str:
    """Appelle le CLI d'un abonnement (gemini, claude) en mode non interactif.

    Le texte long passe par l'entrée standard ; seule une courte ligne sans caractère
    spécial passe en argument `-p`. Sous Windows, `gemini` et `claude` sont des
    fichiers .cmd : un argument multi-ligne y serait tronqué et les caractères
    & | % ^ < > interprétés par l'invite de commandes.
    """
    exe = shutil.which(command)
    if not exe:
        raise FileNotFoundError(command)
    proc = subprocess.run([exe, *(extra_args or []), "-p", arg_prompt], input=prompt,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=cwd, **_popen_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"{command} code {proc.returncode}: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def provider_status(cfg) -> dict:
    """État des accès IA, affiché dans le dashboard."""
    out = {"allow_paid_api": bool(cfg["ai"]["allow_paid_api"])}
    for name in ("gemini", "claude"):
        conf = cfg["ai"][name]
        cli = shutil.which(conf["command"])
        mode = conf["mode"]
        key = gemini_api_key(cfg) if name == "gemini" else None
        if mode == "off":
            state = "désactivé"
        elif key and mode in ("auto", "api"):
            free = conf.get("free_tier_only", True)
            state = ("API gratuite (clé détectée)" if free else
                     "API payante autorisée" if cfg["ai"]["allow_paid_api"] else
                     "API payante bloquée (allow_paid_api = false)")
        elif cli and mode in ("auto", "cli"):
            state = ("CLI d'abonnement détecté" + (" (analyse des images + textes)"
                                                    if name == "claude" and conf.get("vision", True)
                                                    else ""))
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
        """API (clé) d'abord : depuis juin 2026, Gemini CLI refuse les comptes Google
        personnels ; un CLI installé mais non connecté ne doit pas masquer la clé."""
        mode = self.conf["mode"]
        if mode == "off":
            return None
        if mode in ("auto", "api") and gemini_api_key(self.cfg):
            return "api"
        if mode in ("auto", "cli") and shutil.which(self.conf["command"]):
            return "cli"
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
        """Une seule requête par vidéo : texte + toutes les images clés."""
        key = gemini_api_key(self.cfg)
        parts: list[dict] = [{"text": prompt}]
        for _, path in frames:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(path.read_bytes()).decode()}})
        body = json.dumps({"contents": [{"parts": parts}],
                           "generationConfig": {"responseMimeType": "application/json",
                                                "temperature": 0.2}}).encode()
        models = ([self.conf["model"]] if self.conf.get("model") else []) + GEMINI_MODELS
        last_error = None
        for model in dict.fromkeys(models):
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=int(self.conf["timeout"])) as resp:
                    data = json.loads(resp.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise QuotaExhausted("quota gratuit épuisé pour aujourd'hui")
                if exc.code in (400, 401, 403):
                    raise RuntimeError(f"clé refusée par Google (HTTP {exc.code})")
                last_error = f"{model}: HTTP {exc.code}"  # 404 : modèle retiré, on essaie le suivant
        raise RuntimeError(f"aucun modèle Gemini disponible ({last_error})")

    def enrich(self, analysis: dict, work: Path) -> dict:
        """Ajoute étiquettes et intérêt aux moments forts. Ne lève jamais d'exception."""
        route = self._route()
        if not route or not analysis.get("highlights"):
            return analysis
        if route == "api":
            today = time.strftime("%Y-%m-%d")
            if self.guard.db and self.guard.db.kv_get("gemini.quota_exhausted") == today:
                self.log("Quota gratuit Gemini épuisé aujourd'hui : analyse locale.")
                return analysis
            cost_class = "free_tier_api" if self.conf.get("free_tier_only", True) else "paid_api"
        else:
            cost_class = "subscription_cli"
        if not self.guard.authorize("gemini", "qualification des moments forts", cost_class):
            self.log("Gemini refusé par le garde-fou des coûts : analyse locale conservée.")
            return analysis
        try:
            frames = self._frames(analysis, work)
            if not frames:
                return analysis
            if route == "cli":
                extra = ["-m", self.conf["model"]] if self.conf.get("model") else []
                refs = " ".join(f"@{path.name}" for _, path in frames)  # noms sans espace
                text = run_cli(self.conf["command"], self._prompt(analysis, frames, True),
                               int(self.conf["timeout"]), extra, cwd=work,
                               arg_prompt=f"Images des moments : {refs} . {SHORT_INSTRUCTION}")
            else:
                text = self._call_api(self._prompt(analysis, frames, False), frames)
            applied = apply_verdicts(analysis, extract_json(text), "gemini")
            analysis["ai"] = {"provider": "gemini", "route": route, "moments_qualifiés": applied}
            self.log(f"Gemini ({route}) : {applied} moments qualifiés.")
        except QuotaExhausted as exc:
            if self.guard.db:
                self.guard.db.kv_set("gemini.quota_exhausted", time.strftime("%Y-%m-%d"))
            self.log(f"Gemini : {exc}. Analyse locale jusqu'à demain.")
        except Exception as exc:  # jamais bloquant
            self.log(f"Gemini indisponible ({type(exc).__name__}: {str(exc)[:120]}) : "
                     "analyse locale conservée.")
        return analysis


def apply_verdicts(analysis: dict, verdicts, provider: str) -> int:
    """Applique les étiquettes et l'intérêt donnés par une IA. Ignore tout ce qui est invalide."""
    applied = 0
    for v in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(v, dict):
            continue
        i = v.get("index")
        if not isinstance(i, int) or not 0 <= i < len(analysis["highlights"]):
            continue
        h = analysis["highlights"][i]
        label = str(v.get("label", "")).lower()
        interest = v.get("interest")
        if label in LABELS:
            h["label"] = label
        if isinstance(interest, (int, float)) and 0 <= interest <= 10:
            # le score local est pondéré par l'intérêt jugé par l'IA (x0.2 à x2.0)
            h["score"] = round(h["score"] * (0.2 + 0.18 * float(interest)), 4)
            h["meta"] = {**h.get("meta", {}), "ai_interest": interest,
                         "comment": str(v.get("comment", ""))[:200]}
        h["source"] = provider
        applied += 1
    analysis["highlights"].sort(key=lambda h: h["score"], reverse=True)
    return applied


class ClaudeAnalyst(GeminiAnalyst):
    """Même travail que Gemini, fait par Claude via le CLI de ton abonnement (sans clé API).

    Claude ouvre lui-même les images avec son outil de lecture. Tous ses autres outils
    (commandes, modification de fichiers, web) sont interdits pendant cet appel.
    """

    def __init__(self, cfg, guard: CostGuard, log=print):
        super().__init__(cfg, guard, log)
        self.conf = cfg["ai"]["claude"]

    def available(self) -> bool:
        return (self.conf["mode"] in ("auto", "cli") and self.conf.get("vision", True)
                and bool(shutil.which(self.conf["command"])))

    def _prompt(self, analysis: dict, frames: list[tuple[int, Path]], with_refs: bool) -> str:
        names = ", ".join(path.name for _, path in frames)
        return (super()._prompt(analysis, frames, False).replace(
            "Voici des images extraites", "Ouvre et regarde (outil Read) les images extraites")
            + f"\nFichiers images, dans le dossier courant : {names}.\n"
            "N'invente rien que l'image ne montre pas : sans élimination visible, ce n'est "
            "pas un kill.")

    def enrich(self, analysis: dict, work: Path) -> dict:
        if not self.available() or not analysis.get("highlights"):
            return analysis
        if not self.guard.authorize("claude", "qualification des moments forts",
                                    "subscription_cli"):
            return analysis
        try:
            frames = self._frames(analysis, work)
            if not frames:
                return analysis
            extra = ["--allowedTools", "Read",
                     "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch"]
            if self.conf.get("model"):
                extra += ["--model", self.conf["model"]]
            text = run_cli(self.conf["command"], self._prompt(analysis, frames, False),
                           int(self.conf["timeout"]), extra, cwd=work)
            applied = apply_verdicts(analysis, extract_json(text), "claude")
            analysis["ai"] = {"provider": "claude", "route": "cli", "moments_qualifiés": applied}
            self.log(f"Claude (abonnement) : {applied} moments qualifiés.")
        except Exception as exc:  # jamais bloquant
            self.log(f"Claude indisponible pour l'analyse ({type(exc).__name__}: "
                     f"{str(exc)[:120]}) : analyse locale conservée.")
        return analysis


ANALYSTS = {"gemini": GeminiAnalyst, "claude": ClaudeAnalyst}


def qualify_moments(cfg, guard: CostGuard, analysis: dict, work: Path, log=print) -> dict:
    """Essaie les IA dans l'ordre de `ai.vision_order` ; s'arrête à la première qui répond."""
    for name in cfg["ai"].get("vision_order", ["gemini", "claude"]):
        cls = ANALYSTS.get(name)
        if cls is None:
            continue
        analyst = cls(cfg, guard, log)
        if not analyst.available():
            continue
        analysis = analyst.enrich(analysis, work / name)
        if analysis.get("ai"):
            break
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

    def creative_texts(self, stem: str, analyses: list[dict]) -> dict:
        """Accroche, mots d'impact et titre écrits par Claude. {} si indisponible.

        Format : {"short": {"hook", "captions"}, "long": {"title"}}.
        """
        if not self.available():
            return {}
        if not self.guard.authorize("claude", "textes du montage", "subscription_cli"):
            return {}
        events = []
        for a in analyses:
            for e in sorted(a.get("events", []), key=lambda e: -e.get("strength", 0))[:10]:
                events.append(f"{e['kind']} à {e['t']:.0f}s (force {e.get('strength', 0):.2f})")
        prompt = (
            "Tu écris les textes à l'écran d'un montage gaming TikTok en français, style "
            "punchy. Événements détectés dans la vidéo « " + stem + " » :\n"
            + "\n".join(events) +
            "\nRéponds UNIQUEMENT en JSON : {\"hook\": \"accroche de 3 à 6 mots\", "
            "\"captions\": [\"mot d'impact de 1 à 3 mots\", ...6 éléments], "
            "\"title\": \"titre court pour YouTube\"}. Pas de hashtags, pas d'emoji. "
            "N'invente pas de kill ni de victoire : décris l'action.")
        try:
            extra = ["--model", self.conf["model"]] if self.conf.get("model") else []
            data = extract_json(run_cli(self.conf["command"], prompt, int(self.conf["timeout"]),
                                        extra, cwd=self.cfg.work_dir))
        except Exception as exc:
            self.log(f"Textes Claude indisponibles ({type(exc).__name__}) : textes locaux.")
            return {}
        if not isinstance(data, dict):
            return {}
        hook = str(data.get("hook", ""))[:48]
        caps = [str(c)[:22] for c in data.get("captions", []) if isinstance(c, str) and c][:8]
        title = str(data.get("title", ""))[:60]
        self.log("Textes du montage écrits par Claude.")
        return {"short": {"hook": hook, "captions": caps} if hook else {"captions": caps},
                "long": {"title": title} if title else {}}

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
            # dossier de travail neutre : Claude ne charge pas le contexte du projet
            data = extract_json(run_cli(self.conf["command"], prompt,
                                        int(self.conf["timeout"]), extra,
                                        cwd=self.cfg.work_dir))
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
