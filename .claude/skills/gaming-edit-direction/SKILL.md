---
name: gaming-edit-direction
description: Règles créatives du montage gaming (rythme, accroche, structure narrative, variété). À lire avant toute modification de la sélection des clips, de l'ordre, des transitions ou des effets (editor/plan.py, engines/editing.py).
---

# Direction créative d'un montage gaming

## Structure d'un Short (30–60 s)
1. **Cold open (0–2,5 s)** : un éclat du meilleur moment, coupé juste avant sa résolution, avec l'accroche texte. Le spectateur doit vouloir voir la suite.
2. **Montée** : les autres moments en ordre chronologique, intensité croissante. Clips de 3 à 8 s ; plus courts quand l'action s'accélère.
3. **Climax** : le meilleur moment en entier, ralenti progressif + drop musical juste après. Placé dans le dernier tiers, jamais au début.
4. **Résolution courte** (1–3 s) puis fin nette. Pas de traîne.
5. **Boucle** : la dernière image doit enchaîner naturellement sur la première (même scène ou coupe sèche en pleine action) pour que la relecture TikTok paraisse continue.

## Format YouTube (5–12 min)
Chronologique, respiration entre les séquences, intensité réduite (effets à 50 %), chapitres.

## Anti-robot : règles
- Jamais deux clips de même durée à la suite (écart ≥ 20 %).
- Un clip commence pendant le calme qui précède l'action (0,8 à 3 s avant), pas à un décalage fixe.
- Un clip finit quand l'action retombe, pas à un décalage fixe ; on coupe sur un mouvement ou un temps fort musical.
- Jamais la même transition deux fois de suite ; coupe sèche sur les temps forts, transition visible seulement entre séquences.
- Un effet lourd (tremblement, glitch, distorsion) au plus toutes les 1,5 s.
- Pas de « kill » / « headshot » / « victoire » sans étiquette IA (Gemini ou Claude vision).

## Où c'est codé
- Sélection et bornes des clips : `editor/plan.py` (`build_plan`, `_candidate`, `_adaptive_bounds`).
- Ordre narratif : `editor/plan.py` (`_story_order`).
- Effets, ralenti, textes, musique : `engines/editing.py` (`Director`).
- Toute option nouvelle : `config.py` (DEFAULTS) + `config.example.json`, désactivable.

Après modification : rendre un Short réel et appliquer le skill `edit-critic`.
