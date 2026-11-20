---
name: shorts-hook-captions
description: Texte d'accroche, sous-titres animés mot à mot (style TikTok/Shorts) et fin en boucle pour les Shorts verticaux. À utiliser pour toute modif de editor/subtitles.py, engines/graphics.py (éléments hook, word, captions, notify, combo) ou de l'habillage texte dans engines/editing.py.
---

# Accroche, légendes et boucle

Un Short sans texte ni légende dynamique paraît automatique et se fait zapper.
État : implémenté et testé (`tests/test_captions.py`, `tests/test_engines.py`).

Choix technique : en montage stylé, tous les textes sont dessinés par le moteur graphique
(`engines/graphics.py`, couche transparente) et non en ASS, parce que les animations voulues
(rebond, mot courant qui grossit, entrée mot par mot) n'y sont pas faisables proprement. Le
fichier ASS reste utilisé en mode `subtitles.style = "classic"` ou sans style.

## 1. Texte d'accroche (0–2,5 s, pendant le cold open)
- 3 à 7 mots, une promesse ou une question (« 1 contre 4… », « Il aurait dû rester caché »).
- Ordre : `short.hook_text` imposé (texte fixe) ou `"off"` ; sinon Claude
  (`ClaudeDirector.creative_texts`, nourri des étiquettes et du `why` des meilleurs moments) ;
  sinon modèles locaux par étiquette du meilleur moment (`HOOK_TEMPLATES` : kill, headshot,
  clutch, fail, drôle, victoire, série, surprise, réaction).
- Élément `hook` : 92 px en 1080×1920, à 16 % de la hauteur, mots qui entrent un à un (rebond),
  mot-clé sur bandeau de couleur, sortie en fondu.

## 2. Sous-titres animés mot à mot (élément `captions`)
- Source : `.srt` à côté de la vidéo ou Whisper avec `word_timestamps=True` (sinon durée répartie
  selon la longueur des mots). Jamais un groupe à cheval sur deux répliques.
- 4 mots et 22 caractères au plus par ligne, 66 px en vertical, contour 4–6 px, ombre légère,
  à 68 % de la hauteur. Mot courant : couleur d'accent + petit grossissement. Chiffres, cris
  (« NON!! », mots en majuscules) et mots du jeu : couleur secondaire.
- Masqués pendant les 0,3 s qui suivent un impact visuel (explosion, étincelles, headshot,
  victoire) pour laisser voir l'action (`mute`).
- Pas de voix : pas de sous-titres, les légendes d'événement restent.

## 3. Légendes d'événement
- Mot d'impact (`word`, à 56 % de la hauteur, distinct des sous-titres), notification
  (`notify`), compteur (`combo`), `headshot`, `victory`.
- Une légende `word` par clip au maximum ; jamais deux textes d'événement à l'écran en même
  temps (`Director.one_event_text_at_a_time`, priorité victoire > headshot > notification >
  combo > mot).

## 4. Zone sûre (vertical)
`graphics.SAFE_TOP` (8 %), `SAFE_BOTTOM` (82 %), colonne de boutons à droite (`SAFE_BUTTONS` :
x > 86 % entre 40 et 85 % de la hauteur). Textes centrés à 47 % de la largeur, largeur max 74 % ;
notifications et combo à gauche. Test : `test_no_text_in_the_tiktok_interface_zones`.

## 5. Boucle
- `short.loop_ending = true` : pas de fondu, durée totale calée sur un temps de la musique
  (`Director.end_on_beat`), l'accroche revient pendant la dernière seconde (élément `hook`,
  `reprise: true`) pour que la relecture enchaîne sur la même phrase.
- Le bouton d'abonnement n'est affiché que sans boucle.

## Config
`short.hook_text` (`auto|off|"texte fixe"`), `subtitles.style` (`words|tiktok|classic`),
`short.loop_ending`. Toute option nouvelle : `config.py` ET `config.example.json`.
