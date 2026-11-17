---
name: shorts-hook-captions
description: Accroche texte, sous-titres animés mot à mot (style karaoké) et boucle de fin pour les Shorts. À utiliser pour modifier les textes à l'écran (engines/graphics.py, engines/editing.py, editor/subtitles.py).
---

# Accroche, légendes, boucle

- **Accroche** (élément `hook`) : 3 à 6 mots, en haut, mot-clé surligné, visible 2,5 s pendant le cold open. Écrite par Claude (`ClaudeDirector.creative_texts`) sinon tirée d'une liste locale.
- **Sous-titres mot à mot** (élément `captions`) : 1 à 3 mots à la fois, le mot prononcé grossit et prend la couleur d'accent ; police grasse, contour noir, placés au tiers inférieur. Source : `.srt` à côté de la vidéo ou Whisper (horodatage par mot si disponible, sinon répartition proportionnelle à la longueur des mots). Ils remplacent les sous-titres ASS classiques quand le style est actif (`subtitles.style = "words"`).
- **Mots d'impact** (élément `word`) : un mot par événement fort, jamais plus d'un toutes les 1,6 s.
- **Boucle** : fin coupée net en pleine action, sans fondu au noir, pour que la relecture enchaîne (`short.loop_ending`). Le bouton d'abonnement est désactivé dans ce cas.

Lisibilité mobile : texte ≥ 4 % de la hauteur, jamais dans les 12 % du bas (interface TikTok) ni sur le HUD du jeu si possible.
