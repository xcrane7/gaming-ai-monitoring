"""Lecture du texte à l'écran (kill feed, bandeaux VICTORY / ELIMINATED / WASTED...).

OCR local et gratuit avec RapidOCR (Apache-2.0, moteur onnxruntime sous MIT). Dépendance
optionnelle : sans elle, l'analyse fonctionne sans cette couche. Au premier usage, RapidOCR
télécharge ses modèles (environ 15 Mo) une seule fois.

On ne lit que quelques images par moment candidat, et seulement dans des zones de l'écran
(réglables par jeu dans config.json, clé "games"), pour rester rapide.
"""
from __future__ import annotations

import difflib
import logging
import unicodedata

import numpy as np

DEFAULT_REGIONS = {  # x0, y0, x1, y1 en fraction de l'image
    "kill_feed": [0.60, 0.00, 1.00, 0.32],  # en haut à droite dans la plupart des FPS
    "centre": [0.10, 0.25, 0.90, 0.75],  # bandeaux VICTORY / WASTED / MISSION PASSED
}
# du plus précis au plus général : « eliminated by » (on s'est fait tuer) avant « eliminated »
DEFAULT_KEYWORDS = [
    ("fail", ["eliminated by", "killed by", "you died", "wasted", "defeat", "game over",
              "mission failed", "tu es mort", "vous etes mort", "defaite", "echec de la mission"]),
    ("headshot", ["headshot", "tir dans la tete"]),
    ("kill", ["double kill", "triple kill", "quadra kill", "penta kill", "ace", "eliminated",
              "you eliminated", "knocked", "killed", "elimine", "a elimine", "elimination"]),
    ("victoire", ["victory", "victoire", "winner", "royale", "mission passed", "mission complete",
                  "level complete", "niveau termine", "mission accomplie", "#1"]),
]

_ENGINE = None
_TRIED = False


def available() -> bool:
    return engine() is not None


def engine():
    """Moteur OCR (chargé une seule fois), ou None si RapidOCR n'est pas installé."""
    global _ENGINE, _TRIED
    if not _TRIED:
        _TRIED = True
        try:
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
            from rapidocr import RapidOCR  # type: ignore

            _ENGINE = RapidOCR()
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        except Exception:
            _ENGINE = None
    return _ENGINE


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def game_profile(cfg_games: dict, source: str) -> dict:
    """Profil du jeu d'après le nom du fichier (clé "match" dans config.json)."""
    name = normalize(source)
    for game, prof in (cfg_games or {}).items():
        if any(normalize(m) in name for m in prof.get("match", [game])):
            return {"name": game, **prof}
    return {"name": "générique"}


def _fuzzy_in(phrase: str, text: str, threshold: float = 0.85) -> bool:
    """Le texte contient-il la phrase, à quelques lettres près ? L'OCR lit souvent mal les
    polices de jeu (contour, couleurs) : « ELINIITED » pour « ELIMINATED »."""
    words, tokens = phrase.split(), text.split()
    n = len(words)
    # mots courts : correspondance exacte seulement (sinon « limited » passerait pour
    # « eliminated ») ; seuil strict, la vraie correction vient du prétraitement de l'image
    if min(len(w) for w in words) < 6:
        return False
    for i in range(len(tokens) - n + 1):
        cand = " ".join(tokens[i:i + n])
        if difflib.SequenceMatcher(None, cand, phrase).ratio() >= threshold:
            return True
    return False


def classify(text: str, keywords: list | None = None) -> str | None:
    t = f" {normalize(text)} "
    for label, words in (keywords or DEFAULT_KEYWORDS):
        for w in words:
            w = normalize(w)
            if (f" {w} " in t) if len(w) <= 3 else (w in t):  # mots courts : mot entier
                return label
    for label, words in (keywords or DEFAULT_KEYWORDS):  # second passage, tolérant
        for w in words:
            if _fuzzy_in(normalize(w), t.strip()):
                return label
    return None


def read_regions(frame: np.ndarray, regions: dict) -> list[tuple[str, str]]:
    """[(zone, texte)] lus dans les zones d'une image RGB."""
    eng = engine()
    if eng is None:
        return []
    h, w = frame.shape[:2]
    out = []
    for name, (x0, y0, x1, y1) in regions.items():
        crop = frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        if crop.size == 0:
            continue
        # gris + contraste étiré : les textes de jeu (couleurs vives, contour) sont bien
        # mieux lus (« ELINIITED » brut, « ELIMINATED » après prétraitement)
        # (mesuré sur 4 images de test : 4/4 lus correctement, contre 1/4 raté sans)
        gray = crop.astype(np.float32).mean(axis=2)
        lo, hi = float(gray.min()), float(gray.max())
        gray = np.clip((gray - lo) * 255.0 / max(hi - lo, 1.0), 0, 255).astype(np.uint8)
        if gray.shape[0] < 200:  # petit texte (kill feed) : agrandi ×2 avant lecture
            from PIL import Image

            gray = np.asarray(Image.fromarray(gray).resize((gray.shape[1] * 2, gray.shape[0] * 2),
                                                           Image.LANCZOS))
        try:
            res = eng(np.ascontiguousarray(np.stack([gray] * 3, axis=2)))
        except Exception:
            continue
        for text in getattr(res, "txts", None) or ():
            if text and text.strip():
                out.append((name, text.strip()))
    return out


def scan(frames: list[tuple[float, np.ndarray]], profile: dict) -> list[dict]:
    """Textes reconnus et leur étiquette, pour les images d'un moment."""
    regions = profile.get("regions") or DEFAULT_REGIONS
    keywords = profile.get("keywords")
    if keywords and isinstance(keywords, dict):
        keywords = [(k, v) for k, v in keywords.items()] + DEFAULT_KEYWORDS
    hits = []
    for t, frame in frames:
        for region, text in read_regions(frame, regions):
            label = classify(text, keywords)
            if label:
                hits.append({"t": round(t, 2), "region": region, "text": text[:60],
                             "label": label})
    return hits
