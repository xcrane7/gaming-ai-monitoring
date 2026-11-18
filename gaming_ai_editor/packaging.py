"""Emballage YouTube / Shorts (skill youtube-packaging).

Pour chaque vidéo traitée, un fichier OUTPUT/reports/<vidéo>.metadata.json :

    {"titles": [3 variantes], "description": "...", "chapters": [{"t": "00:00", "title": ...}],
     "hashtags": ["#..."], "thumbnail": {"frame_time", "text", "idea", "path"},
     "short_hook": "...", "game": "...", "source": "claude" | "local"}

- Le texte vient du CLI Claude (abonnement, journalisé par le garde-fou des coûts), à partir
  des étiquettes et du « pourquoi » des moments — jamais de vidéo envoyée. La réponse est
  validée (clés, longueurs) ; une seconde tentative est faite avec la liste des erreurs, puis
  repli sur des modèles locaux.
- Les chapitres viennent du plan réel : 00:00 en premier, au moins 10 s entre deux.
- La miniature (PNG 1280×720) est tirée du pic du meilleur moment, avec 2 à 4 mots.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .ai.providers import ClaudeDirector, extract_json, run_cli
from .analysis import ocr
from .editor.plan import EditPlan

CHAPTER_TITLES = {
    "kill": "L'élimination", "headshot": "Le headshot", "clutch": "Le clutch",
    "victoire": "La victoire", "fail": "Le raté", "drôle": "Le moment drôle",
    "surprise": "La surprise", "réaction": "La réaction", "action intense": "Gros échange",
    "pic sonore": "Ça explose", "mouvement": "Ça file", "action": "En pleine action",
}
GENERIC = {"", "menu", "source entière", "rien", "repère régulier", "cinematic"}


def fmt_time(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def game_name(cfg, stem: str) -> str:
    if cfg["youtube"].get("game_name"):
        return str(cfg["youtube"]["game_name"])
    prof = ocr.game_profile(cfg.get("games") or {}, stem)
    return "" if prof["name"] == "générique" else prof["name"].replace("_", " ").title()


def best_moment_label(analyses: list[dict]) -> str:
    """Étiquette sémantique du meilleur moment (kill, clutch…), sinon ''. Seulement le
    premier moment : c'est lui que montre la miniature (texte et image doivent coller).
    Sans preuve de l'IA ou de l'OCR, pas de kill/victoire : les étiquettes en tiennent compte."""
    best = max((h for a in analyses for h in a.get("highlights", [])[:1]),
               key=lambda h: h.get("score", 0), default={})
    return best.get("label", "") if best.get("label") in FOCUS else ""


def chapter_slots(plan: EditPlan) -> list[dict]:
    """Débuts de chapitre issus du plan : 0:00 d'abord, au moins 10 s entre deux,
    et pas de chapitre dans les 10 dernières secondes."""
    slots, last = [], -100.0
    for i, (seg, off) in enumerate(zip(plan.segments, plan.offsets())):
        t = 0.0 if i == 0 else off
        if i and (t - last < 10 or plan.duration - t < 10):
            continue
        why = ""
        for ev in seg.events:
            if ev.get("label"):
                why = ev["label"]
        slots.append({"seconds": round(t, 2), "label": seg.label or "", "why": why})
        last = t
    return slots


def _chapter_title(slot: dict, used: set) -> str:
    label = slot["label"]
    if label.startswith("série"):
        base = f"Série de {label.split('x')[-1]} actions" if "x" in label else "La série"
    else:
        base = CHAPTER_TITLES.get(slot["why"] or label, CHAPTER_TITLES.get(label, "En pleine action"))
    title, n = base, 2
    while title in used:  # pas deux chapitres au même titre
        title = f"{base} ({n})"
        n += 1
    used.add(title)
    return title


def fit(text: str, limit: int = 65) -> str:
    """Coupe sur un mot entier (jamais « n'att… »)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,:;–-")
    return cut or text[:limit]


FOCUS = {"kill": "cette élimination", "headshot": "ce headshot", "clutch": "ce clutch",
         "victoire": "cette victoire", "fail": "ce raté", "drôle": "ce moment",
         "surprise": "ce retournement", "réaction": "ce moment"}


THUMB_TEXT = {"kill": "ÉLIMINÉ", "headshot": "HEADSHOT", "clutch": "CLUTCH",
              "victoire": "VICTOIRE", "fail": "RATÉ…", "drôle": "J'AI CRAQUÉ",
              "surprise": "JAMAIS VU ÇA", "réaction": "MA RÉACTION"}


def local_package(stem: str, game: str, slots: list[dict], best_label: str, hook: str) -> dict:
    """Modèles locaux, sans IA. Sans nom de jeu connu, pas de nom de fichier dans les titres."""
    focus = FOCUS.get(best_label, "")
    if game:
        titles = [f"{game} : {focus or 'cette manche'} que personne n'attendait",
                  f"Je n'aurais jamais dû tenter ça sur {game}",
                  f"{game} – les meilleurs moments de la session"]
    else:
        titles = [f"Personne n'avait vu venir {focus or 'la fin de cette manche'}",
                  "La partie a mal commencé… puis tout a basculé",
                  "Les meilleurs moments de ma session (montage)"]
    used: set = set()
    chapters = [{"t": fmt_time(s["seconds"]),
                 "title": "Intro" if i == 0 else _chapter_title(s, used)}
                for i, s in enumerate(slots)] if len(slots) >= 3 else []
    tag = "".join(ch for ch in game.title() if ch.isalnum())
    hashtags = ([f"#{tag}"] if tag else []) + ["#gaming", "#highlights"]
    if len(hashtags) < 3:
        hashtags.append("#gameplay")
    about = f"Une session de {game}" if game else "Une session"
    return {
        "titles": [fit(t) for t in titles],
        "description": f"{about} montée automatiquement autour de ses moments forts.\n"
                       f"Regarde jusqu'au bout.",
        "chapters": chapters,
        "hashtags": hashtags,
        "thumbnail": {"text": THUMB_TEXT.get(best_label, "REGARDE ÇA"),
                      "idea": "Image du pic du meilleur moment, texte en gros en bas"},
        "short_hook": hook or "Attends la fin",
        "source": "local",
    }


def validate(data, n_chapters: int) -> tuple[dict, list[str]]:
    """Vérifie et nettoie la réponse de l'IA. Retourne (données, erreurs)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return {}, ["la réponse n'est pas un objet JSON"]
    out: dict = {}
    titles = data.get("titles")
    if not (isinstance(titles, list) and len(titles) >= 3
            and all(isinstance(t, str) and t.strip() for t in titles[:3])):
        errors.append("titles : 3 titres attendus")
    else:
        clean = []
        for t in titles[:3]:
            t = " ".join(t.split())
            letters = [c for c in t if c.isalpha()]
            if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
                errors.append(f"titre tout en majuscules : « {t} »")
            if not 20 <= len(t) <= 70:
                errors.append(f"titre de {len(t)} caractères (40 à 65 attendus) : « {t} »")
            clean.append(fit(t, 70))
        out["titles"] = clean
    desc = data.get("description")
    if not (isinstance(desc, str) and desc.strip()):
        errors.append("description vide")
    else:
        out["description"] = "\n".join(desc.strip().splitlines()[:2])[:300]
    ct = data.get("chapter_titles", [])
    if n_chapters:
        if not (isinstance(ct, list) and len(ct) == n_chapters
                and all(isinstance(c, str) and 0 < len(c.strip()) <= 40 for c in ct)):
            errors.append(f"chapter_titles : {n_chapters} titres de 40 caractères max attendus")
        elif any(c.lower().startswith("moment") for c in ct):
            errors.append("chapter_titles : titres parlants, pas « moment 3 »")
        else:
            out["chapter_titles"] = [c.strip() for c in ct]
    tags = data.get("hashtags")
    if not (isinstance(tags, list) and 3 <= len(tags) <= 5
            and all(isinstance(h, str) and h.startswith("#") and " " not in h for h in tags)):
        errors.append("hashtags : 3 à 5, commençant par #, sans espace")
    else:
        out["hashtags"] = tags
    thumb = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    text = str(thumb.get("text", "")).strip()
    if not 1 <= len(text.split()) <= 4:
        errors.append("thumbnail.text : 2 à 4 mots")
    else:
        out["thumbnail"] = {"text": text[:40], "idea": str(thumb.get("idea", ""))[:200]}
    hook = str(data.get("short_hook", "")).strip()
    if not 2 <= len(hook.split()) <= 7:
        errors.append("short_hook : 3 à 7 mots")
    else:
        out["short_hook"] = hook
    return out, errors


def ask_claude(director: ClaudeDirector, stem: str, game: str, slots: list[dict],
               moments: list[str], duration: float, language: str, log) -> dict | None:
    if not director.available():
        return None
    if not director.guard.authorize("claude", "emballage YouTube", "subscription_cli"):
        return None
    chap = "\n".join(f"- {fmt_time(s['seconds'])} : {s['why'] or s['label'] or 'action'}"
                     for s in slots[1:])
    base = (
        f"Tu prépares la publication YouTube d'un montage gaming ({language}). Jeu : "
        f"{game or 'inconnu (déduis-le du nom de fichier si possible)'} ; fichier « {stem} » ; "
        f"durée {duration:.0f} s.\nMeilleurs moments (étiquette : ce qui se passe) :\n"
        + "\n".join(moments) + (f"\nChapitres (après l'intro à 00:00) :\n{chap}" if chap else "")
        + "\nRègles : 3 titres de 40 à 65 caractères dans des styles différents (promesse "
        "concrète + curiosité, nom du jeu au début ou à la fin, pas de clickbait mensonger, "
        "pas tout en majuscules) ; description = 2 lignes d'accroche ; un titre court et "
        "parlant par chapitre listé (pas « moment 3 ») ; 3 à 5 hashtags ; miniature : 2 à 4 "
        "mots ; short_hook : 3 à 7 mots. N'affirme rien que les étiquettes ne disent pas."
        "\nRéponds UNIQUEMENT en JSON : {\"titles\": [\"...\", \"...\", \"...\"], "
        "\"description\": \"...\", \"chapter_titles\": [\"...\"], \"hashtags\": [\"#...\"], "
        "\"thumbnail\": {\"text\": \"...\", \"idea\": \"...\"}, \"short_hook\": \"...\"}")
    n = max(0, len(slots) - 1) if len(slots) >= 3 else 0
    prompt, errors = base, []
    for attempt in (1, 2):
        try:
            extra = ["--model", director.conf["model"]] if director.conf.get("model") else []
            raw = run_cli(director.conf["command"], prompt, int(director.conf["timeout"]), extra,
                          cwd=director.cfg.work_dir)
            data, errors = validate(extract_json(raw), n)
        except Exception as exc:
            data, errors = {}, [f"{type(exc).__name__}: {str(exc)[:120]}"]
        if not errors:
            data["source"] = "claude"
            return data
        log(f"Emballage YouTube, essai {attempt} refusé : {'; '.join(errors)[:200]}")
        prompt = base + "\nTa réponse précédente a été refusée : " + "; ".join(errors) + \
            ". Corrige ces points."
    return None


def make_thumbnail(video: str | Path, at: float, text: str, style: dict, out: Path) -> Path:
    """PNG 1280×720 au pic du meilleur moment, image ravivée, 2 à 4 mots en gros."""
    from PIL import Image

    from .engines import graphics
    from .ffutil import run_ffmpeg

    raw = out.with_suffix(".frame.png")
    run_ffmpeg(["-ss", f"{max(0.0, at):.2f}", "-i", str(video), "-frames:v", "1",
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
                "eq=saturation=1.25:contrast=1.08,unsharp=5:5:0.6", str(raw)])
    img = Image.open(raw).convert("RGBA")
    ctx = graphics.Context(1280, 720, 30, 1, palette=style.get("palette") or
                           graphics.Context(1, 1, 1, 1).palette,
                           font=style.get("font") or "Impact")
    words = " ".join(text.upper().split()[:4])
    sprite = graphics.text_sprite(words, ctx.font, 124, ctx.color("accent"), 12, (0, 0, 0, 255),
                                  glow=(0, 0, 0, 210), glow_radius=12, shadow=8)
    graphics.paste(img, sprite, 640, 590, min(1.0, 1180 / sprite.width), -3)
    img.convert("RGB").save(out)
    raw.unlink(missing_ok=True)
    return out


def build(cfg, guard, log, stem: str, outputs: list[dict], analyses: list[dict],
          reports_dir: Path) -> dict:
    """Produit, valide et écrit l'emballage d'une vidéo. Retourne le dictionnaire écrit."""
    long_out = next((o for o in outputs if o["format"] == "long"), None)
    short_out = next((o for o in outputs if o["format"] == "short"), None)
    ref = long_out or short_out
    plan = EditPlan.from_dict(ref["plan"]) if ref else None
    slots = chapter_slots(EditPlan.from_dict(long_out["plan"])) if long_out else []
    if len(slots) < 3:
        slots = slots if long_out else []
    game = game_name(cfg, stem)
    moments, best_label = [], ""
    for a in analyses:
        for h in a.get("highlights", [])[:5]:
            if h.get("label") in GENERIC:
                continue
            why = (h.get("meta") or {}).get("ai_why") or (h.get("meta") or {}).get("why", "")
            moments.append(f"- {h.get('label', '')} : {why}" if why else f"- {h.get('label', '')}")
    best_label = best_moment_label(analyses)
    hook = (short_out or {}).get("plan", {}).get("hook", "") if short_out else ""
    director = ClaudeDirector(cfg, guard, log)
    lang = cfg["subtitles"].get("language") or "français"
    pkg = ask_claude(director, stem, game, slots, moments, plan.duration if plan else 0.0,
                     lang, log)
    if pkg is None:
        pkg = local_package(stem, game, slots, best_label, hook)
    else:
        titles = pkg.get("chapter_titles") or []
        pkg["chapters"] = ([{"t": "00:00", "title": "Intro"}]
                           + [{"t": fmt_time(s["seconds"]), "title": t}
                              for s, t in zip(slots[1:], titles)]) if len(slots) >= 3 else []
        pkg.pop("chapter_titles", None)
    pkg["game"] = game
    chapters_text = "\n".join(f"{c['t']} {c['title']}" for c in pkg["chapters"])
    pkg["description_full"] = "\n\n".join(
        x for x in (pkg["description"], chapters_text, " ".join(pkg["hashtags"])) if x)
    # miniature : pic du meilleur moment de la vidéo de référence
    pkg["thumbnail"]["frame_time"] = None
    pkg["thumbnail"]["path"] = None
    if ref and plan and plan.segments:
        best = max(range(len(plan.segments)), key=lambda k: plan.segments[k].score)
        seg = plan.segments[best]
        at = plan.offsets()[best] + seg.map_time(seg.peak)
        pkg["thumbnail"]["frame_time"] = round(at, 2)
        pkg["thumbnail"]["source_time"] = round(seg.peak, 2)
        try:
            # image propre de la source (le rendu porte déjà accroche et sous-titres)
            src = seg.source if Path(seg.source).is_file() else ref["path"]
            t = seg.peak if src == seg.source else at
            path = Path(ref["path"]).with_name(Path(ref["path"]).stem + "_thumbnail.png")
            make_thumbnail(src, t, pkg["thumbnail"]["text"], plan.style or {}, path)
            pkg["thumbnail"]["path"] = str(path)
        except Exception as exc:
            log(f"Miniature impossible : {exc}")
    pkg["chapter_seconds"] = [s["seconds"] for s in slots] if pkg["chapters"] else []
    pkg["at"] = time.time()
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{stem}.metadata.json"
    path.write_text(json.dumps(pkg, indent=2, ensure_ascii=False), encoding="utf-8")
    pkg["metadata_path"] = str(path)
    return pkg
