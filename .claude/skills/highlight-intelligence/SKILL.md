---
name: highlight-intelligence
description: Améliorer la détection des moments forts pour qu'elle comprenne le gameplay (événements audio, texte à l'écran, séries, vision Gemini/Claude, score de « qualité narrative »). À utiliser pour toute modif de analysis/, engines/intelligence.py ou ai/providers.py, ou quand le montage choisit de mauvais moments.
---

# Détection de moments forts « intelligente »

Objectif : un score qui ressemble à ce qu'un monteur choisirait. Un pic sonore n'est pas un moment
intéressant (une cinématique bruyante ne vaut pas un triple kill).

## État actuel (implémenté et testé : `tests/test_highlights.py`)

1. **Signaux de base** (`analysis/highlights.py`) : RMS audio (0,45) + flux (0,2) + mouvement (0,35),
   lissés, pics au-dessus du 80e centile. Menus / pauses exclus (`engines/intelligence.py`).
2. **Événements audio** (`engines/intelligence.py`) :
   - `reaction` : voix du joueur — énergie forte dans 300–3400 Hz ET spectre harmonique
     (planéité < 0,3), 0,3 à 3 s. Une explosion remplit la bande mais avec un spectre plat : rejetée.
     Approximation sans piste micro séparée.
   - `surprise` : silence (12 dB sous le niveau habituel) pendant 1,5 s puis attaque ≥ 15 dB.
   - aussi : tir, explosion, impact, rafale, flash, rush.
3. **Séries** (`detect_streaks`) : ≥ 3 actions à moins de 8 s d'écart (deux détections au même
   instant comptent pour une) = un seul moment, élargi pour contenir toute la série.
4. **Texte à l'écran** (`analysis/ocr.py`, RapidOCR, facultatif) : lu APRÈS l'action (pic + 0,7 s et
   fin + 2 s), dans les zones `kill_feed` et `centre` (réglables par jeu : `config.json` → `games.<nom>`
   avec `match`, `regions`, `keywords`). Image passée en gris, contraste étiré, agrandie ×2 si petite
   (mesuré : 4/4 bien lus contre « ELINIITED » sans prétraitement). Mots-clés → `kill`, `headshot`,
   `victoire`, `fail` (« eliminated by » = fail, avant « eliminated » = kill). Tolérance aux erreurs
   d'OCR très stricte (≥ 0,85, mots ≥ 6 lettres) : « limited » ne doit jamais devenir un kill.
5. **Re-classement** (`analysis/rerank.py`) :
   `score = base × bonus_événements × bonus_série × bonus_texte × doublon × répétition`
   réaction ×1,3 ; surprise ×1,4 ; série ×(1 + 0,25 par action au-delà de 2, max ×2) ; texte kill /
   headshot / victoire ×1,5, fail ×1,2 ; doublon visuel (dHash du pic, distance ≤ 8) ×0,6 ;
   k-ième répétition d'une étiquette ×0,9^(k-1). Infos dans `meta` : `base_score`, `bonus`, `why`,
   `streak`, `ocr_hits`, `dup_of`.
6. **Vision IA** (`ai/providers.py`, `qualify_moments`) sur les `ai.vision_top` (12) meilleurs candidats,
   `ai.frames_per_moment` (3) images chacun (début, pic, fin). JSON :
   `{"index", "label": kill|headshot|clutch|victoire|fail|drôle|cinematic|action|rien, "interest": 0-10, "why"}`.
   `score = base × (0,5 + interest/10)`. Ordre : Gemini (clé gratuite) puis Claude (CLI abonnement,
   outil Read seul). Garde-fou des coûts et journalisation inchangés.

L'IA reste un plus, jamais une dépendance : les couches 1 à 5 tournent sans elle.

## Pistes restantes
- Profils de jeux prêts à l'emploi (Fortnite, Valorant, CS2, Warzone…) : zones et mots-clés réels,
  à valider sur de vraies captures de l'utilisateur.
- Piste micro séparée (OBS multi-pistes) pour des réactions fiables.
- Rires (motif rythmique de la voix) : non détectés.

## Contraintes
- L'analyse locale doit rester bien plus rapide que le temps réel : les couches coûteuses (images,
  OCR, IA) ne portent que sur les meilleurs candidats (`analysis.rerank_top`, `analysis.ocr_top`).
- Tout nouveau signal : test sur gameplay synthétique à vérité terrain (`synth.make_gameplay`
  accepte `voices`, `quiet`, `texts`).
- Étiquettes affirmatives (kill, headshot, victoire) seulement si elles viennent de l'IA ou du texte
  lu à l'écran (`source` = gemini, claude ou ocr), jamais des seuls signaux.
- Sortie : toujours `Highlight(start, end, peak, score, label, source, meta)`.
- Documenter dans le README ce qui est vérifié et ce qui ne l'est pas.
