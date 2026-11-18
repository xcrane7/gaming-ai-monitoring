---
name: youtube-packaging
description: Titre, description, chapitres et miniature pour la version YouTube longue. À utiliser pour modifier gaming_ai_editor/packaging.py, ai/providers.py (ClaudeDirector) ou la génération de rapports.
---

# Emballage YouTube

Code : `gaming_ai_editor/packaging.py` (appelé par `pipeline.py::_produce`, après la QA).
Option : `youtube.enabled` (défaut true), `youtube.game_name` (sinon profil de jeu `games`,
sinon rien — jamais le nom de fichier dans un titre).

## Sortie : `OUTPUT/reports/<vidéo>.metadata.json`
```json
{"titles": ["3 variantes"], "description": "2 lignes d'accroche",
 "description_full": "accroche + chapitres + hashtags, prête à coller",
 "chapters": [{"t": "00:00", "title": "Intro"}, ...], "hashtags": ["#..."],
 "thumbnail": {"text": "2 à 4 mots", "idea": "...", "frame_time": 12.4, "source_time": 53.2,
               "path": "OUTPUT/youtube/<vidéo>_thumbnail.png (sans version longue : OUTPUT/reports)"},
 "short_hook": "3 à 7 mots", "game": "...", "source": "claude" | "local"}
```
Visible dans le dashboard (onglet Montage, « Publication YouTube » : miniature, 3 titres,
description complète sélectionnable).

## Règles
- **Entrées de l'IA** : étiquettes et « pourquoi » des meilleurs moments (Gemini/Claude/OCR),
  durée, nom du jeu, langue (`subtitles.language`). Jamais la vidéo. CLI Claude de l'abonnement,
  passé par `CostGuard` (journalisé), aucune API payante.
- **Titres** : 3 styles différents, 40 à 65 caractères, promesse concrète + curiosité, pas de
  clickbait mensonger, pas tout en majuscules, coupés sur un mot entier (`fit`).
- **Validation** (`validate`) : clés, longueurs, nombre de titres de chapitre, hashtags (3 à 5,
  `#` sans espace). Réponse refusée → **une** nouvelle tentative avec la liste des erreurs, puis
  repli sur les modèles locaux (`local_package`).
- **Chapitres** (`chapter_slots`) : issus du plan long réel, 00:00 en premier, au moins 10 s
  entre deux, aucun dans les 10 dernières secondes, au moins 3 sinon pas de chapitres (règle
  YouTube). Titres parlants (« L'élimination », « Série de 3 actions »), jamais « Moment 3 ».
- **Miniature** (`make_thumbnail`) : PNG 1280×720, image **propre de la source** au pic du
  segment le mieux noté (le rendu porte déjà accroche et sous-titres), saturée, 2 à 4 mots en
  gros dans la couleur d'accent du montage. Texte local tiré de l'étiquette du **premier**
  moment seulement (texte et image doivent parler du même moment) ; sans preuve IA/OCR, pas
  de « ÉLIMINÉ » ni « VICTOIRE ».
- Un échec d'emballage ne fait jamais échouer le rendu (repli local, avertissement au journal).

Tests : `tests/test_packaging.py` (validation, nouvelle tentative, chapitres, miniature,
dashboard) et `tests/test_services.py::test_youtube_package_and_tempo_matched_music` (pipeline).
