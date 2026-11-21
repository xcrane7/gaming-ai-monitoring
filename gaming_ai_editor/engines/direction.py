"""Directeur artistique : classe chaque événement, juge son importance et choisit une
réalisation adaptée. Jamais d'information que les données ne permettent pas d'affirmer.

Classes (avec la preuve exigée) :
    kill, headshot, clutch, victoire, defaite, fail, drole  étiquette IA (Gemini/Claude) ou OCR
    multikill        kills confirmés enchaînés (≥ 2 en 4 s, ou série de coups sur un kill)
    explosion        événement audio « explosion » (basses fortes, signal mesuré)
    spectaculaire    impact / tir / rafale forts sans étiquette : on montre, on n'affirme rien
    reaction         voix du joueur détectée (bande vocale)
    tension          silence puis pic (la tension se relâche sur le pic)
    deplacement      mouvement rapide (rush) ou impact faible
    temps_mort       rien de mesurable

Chaque décision : {class, evidence, importance 0..1, tier, fx, why}. Les recettes donnent la
liste d'effets (jetons compris par editing.py : caméra, VFX, graphismes, sons).
"""
from __future__ import annotations

# importance de base (avant force du signal et rôle narratif)
BASE = {"victoire": 1.0, "multikill": 0.95, "headshot": 0.9, "clutch": 0.9, "kill": 0.8,
        "explosion": 0.7, "defaite": 0.65, "fail": 0.6, "drole": 0.6, "spectaculaire": 0.55,
        "tension": 0.5, "reaction": 0.45, "deplacement": 0.2, "temps_mort": 0.0}

# recettes : effets par intensité (« fort » ajoute aux effets « moyen », etc.)
RECIPES = {
    "kill": {"léger": ["punch", "hitmarker_gfx", "sfx_impact"],
             "moyen": ["shake_micro", "notify", "sfx_ding"],
             "fort": ["flash"]},
    "headshot": {"léger": ["punch_snap", "flash", "headshot_gfx", "sfx_boom"],
                 "moyen": ["notify", "sfx_ding"], "fort": ["rgb_split"]},
    "multikill": {"léger": ["punch", "combo", "hitmarker_gfx", "sfx_ding"],
                  "moyen": ["notify", "sfx_impact"], "fort": ["shake", "glow"]},
    "clutch": {"léger": ["punch_soft", "notify", "sfx_riser"],
               "moyen": ["prefer_ramp", "sfx_ding"], "fort": ["glow"]},
    "victoire": {"léger": ["victory_gfx", "glow", "sfx_riser"],
                 "moyen": ["letterbox"], "fort": ["flash"]},
    "defaite": {"léger": ["glitch", "sfx_glitch"], "moyen": ["punch_soft"], "fort": []},
    "fail": {"léger": ["glitch", "sfx_glitch"], "moyen": ["punch_soft"], "fort": ["shake_micro"]},
    "drole": {"léger": ["punch_comic"], "moyen": ["sfx_pop"], "fort": []},
    "explosion": {"léger": ["punch", "explosion_gfx", "sfx_boom"],
                  "moyen": ["flash", "shake"], "fort": ["glow", "distortion"]},
    "spectaculaire": {"léger": ["punch", "sfx_impact"], "moyen": ["sparks_gfx", "flash"],
                      "fort": ["rgb_split", "shake_micro"]},
    "tension": {"léger": ["punch"], "moyen": ["flash", "sfx_impact"], "fort": ["shake"]},
    "reaction": {"léger": ["punch_soft"], "moyen": [], "fort": []},  # la voix suffit
    "deplacement": {"léger": [], "moyen": ["motion_blur", "sfx_whoosh"], "fort": []},
    "temps_mort": {"léger": [], "moyen": [], "fort": []},
}
TIERS = ("aucun", "léger", "moyen", "fort")
HEAVY = {"shake", "shake_micro", "glitch", "distortion"}

# textes courts, naturels, sans rien affirmer au-delà de la classe
TEXTS = {
    "kill": ["ÉLIMINÉ", "AU SUIVANT", "UN DE MOINS"],
    "headshot": ["EN PLEINE TÊTE", "HEADSHOT"],
    "multikill": ["ENCHAÎNEMENT", "ET DE DEUX", "IMPOSSIBLE À ARRÊTER"],
    "clutch": ["SANG-FROID", "IL S'EN SORT", "CLUTCH"],
    "victoire": ["VICTOIRE", "GG"],
    "defaite": ["C'EST PERDU", "DÉFAITE"],
    "fail": ["AÏE", "OUPS", "RATÉ"],
    "drole": ["QUOI ?!", "N'IMPORTE QUOI", "PARDON ?"],
    "explosion": ["BOUM", "ÇA SAUTE", "BADABOUM"],
    "spectaculaire": ["ÇA TAPE", "PAF", "DIRECT"],
    "tension": ["D'UN COUP", "LÀ !"],
    "reaction": [],  # la réaction du joueur parle d'elle-même
    "deplacement": [],
    "temps_mort": [],
}


def classify(ev: dict, kills_near: int = 0) -> tuple[str, str]:
    """(classe, preuve). `kills_near` = autres kills confirmés à moins de 4 s."""
    label, kind = ev.get("label"), ev.get("kind", "")
    strength = float(ev.get("strength", 0.5))
    proof = ev.get("label_source") or "ia/ocr"
    if label == "kill" and (kills_near >= 1 or (kind == "burst" and int(ev.get("count", 0)) >= 2)):
        return "multikill", f"{proof} : kill confirmé + {max(kills_near + 1, int(ev.get('count', 0)))} coups enchaînés"
    if label in ("kill", "headshot", "clutch", "victoire", "fail"):
        return label, f"{proof} : étiquette « {label} »"
    if label in ("drôle", "drole"):
        return "drole", f"{proof} : étiquette « drôle »"
    if label in ("défaite", "defaite"):
        return "defaite", f"{proof} : étiquette « défaite »"
    if kind == "explosion":
        return "explosion", f"son : explosion (force {strength:.2f})"
    if kind == "reaction":
        return "reaction", "voix du joueur détectée"
    if kind == "surprise":
        return "tension", "silence puis pic sonore"
    if kind in ("impact", "shot", "burst", "flash") and strength >= 0.55:
        return "spectaculaire", f"son : {kind} fort ({strength:.2f}), sans étiquette"
    if kind in ("impact", "shot", "burst", "flash", "rush"):
        return "deplacement", f"signal faible ({kind} {strength:.2f})"
    return "temps_mort", "rien de mesurable"


def importance(cls: str, strength: float, role: str, peak_near: bool) -> float:
    """0..1 : classe × force du signal × rôle dans le récit."""
    imp = BASE[cls] * (0.6 + 0.4 * max(0.0, min(1.0, strength)))
    if role == "climax":
        imp *= 1.25
    elif role == "teaser":
        imp *= 1.1
    if peak_near:  # c'est LE moment du clip
        imp *= 1.1
    return round(min(1.0, imp), 3)


def tier(imp: float) -> str:
    return "fort" if imp >= 0.75 else "moyen" if imp >= 0.45 else "léger" if imp >= 0.2 else "aucun"


def recipe(cls: str, level: str) -> list[str]:
    if level == "aucun":
        return []
    out: list[str] = []
    for lv in ("léger", "moyen", "fort")[: TIERS.index(level)]:
        out += RECIPES[cls][lv]
    return out


def decide(ev: dict, role: str = "", peak_near: bool = False, kills_near: int = 0,
           heavy_ok: bool = True, scale: float = 1.0) -> dict:
    """Décision complète pour un événement. `scale` = intensité du style (0..1+)."""
    cls, evidence = classify(ev, kills_near)
    strength = float(ev.get("strength", 0.5))
    imp = importance(cls, strength, role, peak_near)
    level = tier(imp * (0.85 + 0.3 * min(1.0, scale)))
    fx = recipe(cls, level)
    if ev.get("kind") == "burst" and int(ev.get("count", 0)) >= 2 and level != "aucun" \
            and cls in ("spectaculaire", "deplacement"):
        fx += ["combo"]  # série de coups COMPTÉS (affichée « SÉRIE », jamais « KILL »)
    if not heavy_ok:  # deux effets lourds à moins de 1,5 s : on garde le plus léger
        fx = [f for f in fx if f not in HEAVY]
    why = f"{cls} ({evidence}) · importance {imp:.2f} · {level}" + (f" · {role}" if role else "")
    return {"class": cls, "evidence": evidence, "importance": imp, "tier": level,
            "fx": fx, "why": why}


# accroches : ce qu'un texte ne peut pas affirmer sans preuve, et les tics de texte d'IA
CLAIMS = {"kill": ("élimin", "kill", "tué", "abattu"), "headshot": ("headshot", "en pleine tête"),
          "victoire": ("victoire", "gagn", "top 1", "top1"), "clutch": ("clutch", "1v", "1 contre")}
CLICHES = ("incroyable", "épique", "dingue", "tu ne vas pas croire", "vous n'allez pas croire",
           "voici", "absolument", "ultime", "légendaire", "insane", "regardez ce", "#")
FIXES = {"ne l'a pas vue venir": "ne l'a pas vu venir", "l'a pas vue": "l'a pas vu"}


def check_hook(text: str, proven: set[str]) -> tuple[str, str]:
    """(accroche nettoyée ou '', raison du refus). Refuse une affirmation non prouvée
    (« kill » sans kill confirmé), les tics d'IA, les longueurs hors 2 à 7 mots."""
    t = " ".join(str(text or "").split())
    low = t.lower()
    for bad, good in FIXES.items():  # accord du participe (« il ne l'a pas vu venir »)
        if bad in low:
            i = low.index(bad)
            t = t[:i] + good + t[i + len(bad):]
            low = t.lower()
    if not 2 <= len(t.split()) <= 7 or len(t) > 48:
        return "", "longueur"
    for word in CLICHES:
        if word in low:
            return "", f"tic de texte d'IA : « {word} »"
    for cls, words in CLAIMS.items():
        if any(w in low for w in words) and cls not in proven and not (
                cls == "kill" and "multikill" in proven):
            return "", f"affirme « {cls} » sans preuve"
    return t, ""
