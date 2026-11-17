---
name: highlight-intelligence
description: Détecter de vrais moments forts dans un gameplay (événements audio typés, menus exclus, vision Gemini/Claude). À utiliser pour toute modification de analysis/ ou engines/intelligence.py, ou quand le montage choisit de mauvais moments.
---

# Détecter de vrais moments forts

Un pic sonore n'est pas un moment intéressant. Les couches, de la moins chère à la plus fiable :

1. **Signaux locaux** (`analysis/signals.py`) : énergie audio (20 ms), part de graves, mouvement, luminosité, part de l'image qui bouge.
2. **Événements typés** (`engines/intelligence.py`) : tir (attaque sèche), explosion (graves + flash), rafale (≥ 3 coups < 0,6 s), flash, rush. Les **menus / pauses** (moins de 5 % de l'image bouge pendant 1,5 s) sont exclus.
3. **Vision IA** (`ai/providers.py`, `qualify_moments`) : une image par moment fort, étiquetée kill / headshot / victoire / fail / drôle / action / rien, avec un intérêt 0–10 qui repondère le score. Ordre : Gemini (clé gratuite) puis Claude (CLI de l'abonnement, outil Read seul).

## Pistes non encore implémentées
- Lecture du kill feed (zone de l'écran propre à chaque jeu, changement de pixels en haut à droite) : signal fort et gratuit, à ajouter par jeu.
- Plusieurs images par moment (avant / pendant / après) pour que l'IA juge l'action et pas une image figée.

## Vérifier
Tests sur vérité terrain : `tests/test_engines.py::test_intelligence_finds_events_and_menus`. Toute nouvelle détection : un gameplay synthétique (`synth.py`) avec l'événement placé à un instant connu.
