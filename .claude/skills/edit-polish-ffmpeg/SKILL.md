---
name: edit-polish-ffmpeg
description: Effets de montage FFmpeg pour un rendu dynamique - punch-in, ralenti sur le pic, accélération des temps morts, impacts sonores, shake, étalonnage, musique calée sur le tempo, coupes en J/L, transitions guidées. À utiliser pour toute modif de editor/render.py, engines/motion.py, engines/vfx.py, engines/color.py ou engines/audio.py.
---

# Polish FFmpeg pour montage gaming

`editor/render.py` fait (1) un clip intermédiaire par segment (`_render_clip`), (2) une couche
graphique, (3) un assemblage (`_assemble`) : transitions xfade (durée par jonction), musique, effets
sonores, sous-titres, loudnorm −14 LUFS. Les effets sont décidés par le réalisateur
(`engines/editing.py`) et stockés dans le plan (`Segment.ramp`, `camera`, `vfx`, `plan.transitions`,
`plan.sfx`, `plan.music`). Toujours garder le repli CPU et la QA.
Tests : `tests/test_polish.py`, `tests/test_engines.py`.

## Effets par segment (pilotés par le plan, désactivables)
- **Punch-in** : zoom 1 → ~1,1 amorti autour de chaque impact (`engines/motion.py`, expressions sur
  `t`). Netteté : avec `render.supersample` (1,15), la mise au format produit une image 1,15× plus
  grande dans laquelle la caméra recadre, au lieu d'agrandir l'image finale.
- **Ralenti sur le pic** (`SpeedRamp`, 0,35× sur ~0,8 s avec entrée/sortie progressives) : `setpts`
  par morceaux (points détaillés seulement pendant les transitions de vitesse : l'expression reste
  courte quelle que soit la durée) + `framerate` (fusion d'images). Son : `style.slowmo_audio`
  `keep_pitch` (rubberband si disponible, sinon `atempo` enchaînés) ou `deep` (grave, « bullet time »).
- **Accélération des temps morts** (`style.speed_up_dead_time`, ×1,7) : le plus long passage d'au
  moins 2,5 s sans événement (à plus de 0,8 s de toute action) dans un clip de montée ; son à
  hauteur conservée. Jamais sur le cold open ni le climax.
- **Shake** : translation + légère rotation amorties (~0,25 s), avec flou de mouvement (`tmix`).
- **Flash** sur impact fort : luminosité qui décroît en quelques images.

## Effets globaux
- **Impacts sonores** : placés au 1/48 000 s sur chaque événement ; chaque occurrence varie (±5 % de
  hauteur, ±1,5 dB). Tes propres sons `assets/sfx/<type>.*` (impact, boom, whoosh, riser, ding…)
  remplacent les sons synthétisés. Whoosh seulement sur les transitions visibles.
- **Musique** : fournie → choisie selon le tempo adapté à l'énergie de la vidéo, tempo et temps
  détectés (`detect_beats`, autocorrélation + ajustement de grille, sans librosa), coupes calées sur
  les temps. Montée +3 dB sur les deux mesures avant le climax, chute nette 0,3 s avant le pic, retour
  au drop (`Renderer._music_envelope`). Générée → composée avec montée, passage étouffé et drop.
- **Étalonnage** : une LUT par vidéo (identique sur tous les clips), choisie selon les couleurs du jeu.
- **Coupes en J** (format long, `long.jl_cuts` = 0,2 s) : le son du clip suivant commence avant son
  image ; image et son sont concaténés séparément, la synchro à l'intérieur des clips est conservée.
- **Fin du Short** : pas de fondu, durée calée sur un temps de la musique.

## Transitions (`Director.choose_transitions`)
Coupe sèche par défaut pendant la montée ; « whip » (glissé 0,12 s + flou de mouvement) entre deux
moments d'énergie proche ; flash blanc à l'entrée du climax ; jamais deux fois de suite la même
transition visible.

## Garde-fous (obligatoires)
- Durée finale = somme des segments ajustée par les vitesses (`Segment.out_duration`) : la QA la vérifie.
- Dérive A/V après ralenti et accélération testée (`test_no_audio_video_drift_after_speed_changes` :
  un impact après la rampe reste synchrone à une image près).
- Graphe de filtres limité (`MAX_XFADE_CLIPS`, expressions de vitesse compactes).
- Temps de rendu : comparer avant/après sur le gameplay de référence (`render_seconds` du rapport) ;
  ne pas dégrader de plus de 30 %.
