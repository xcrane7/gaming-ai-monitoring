---
name: edit-polish-ffmpeg
description: Finition visuelle et sonore avec FFmpeg - punch-in, ralentis, impacts, tremblement, étalonnage, musique synchronisée. À utiliser pour modifier engines/motion.py, engines/vfx.py, engines/color.py, engines/audio.py ou editor/render.py.
---

# Finition FFmpeg

- Toute animation passe par une courbe continue (`engines/motion.py`) : jamais de saut de valeur. Les exponentielles sont protégées par `max(t - t0, 0)`.
- Les effets image sont actifs seulement dans leurs fenêtres (`enable='between(t,a,b)'`) pour ne rien coûter ailleurs.
- Speed ramp : `SpeedRamp` calcule à la fois `setpts` (vidéo), la durée ajoutée et le facteur audio (`asetrate`, son grave). Toujours garder vidéo et audio de même durée.
- Étalonnage : LUT 3D générées (`engines/color.py`), jamais de fichier tiers.
- Musique : générée (`generate_music`, drop aligné sur une mesure) ou fournie (`detect_beats`) ; coupes sur les temps (`Director.plan_music`).
- Effets sonores : `render_sfx_track`, au 1/48 000 s, mêmes instants que les animations.
- Rendu : NVENC si disponible, repli CPU obligatoire (NVENC réel non vérifié).

Vérifier : `tests/test_engines.py` (durée du speed ramp, synchro des impacts < 50 ms, LUT valides), puis `edit-critic`.
