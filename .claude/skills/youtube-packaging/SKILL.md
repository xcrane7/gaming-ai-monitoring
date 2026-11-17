---
name: youtube-packaging
description: Titre, description, chapitres et miniature pour la version YouTube longue. À utiliser pour modifier ai/providers.py (ClaudeDirector.metadata) ou la génération de rapports.
---

# Emballage YouTube

- **Titre** (≤ 70 caractères) et **description** : `ClaudeDirector.metadata`, à partir des moments retenus. Ne rien affirmer que les étiquettes ne disent pas.
- **Chapitres** : un par clip du plan long (« 0:00 Intro », puis « m:ss <étiquette> »), le premier à 0:00, au moins 10 s entre deux chapitres (règle YouTube). À écrire dans le rapport (`OUTPUT/reports/*.json`, clé `chapters`) et dans la description.
- **Miniature** : image du moment le plus fort (pic d'intérêt IA, sinon score local), 1280×720, légèrement saturée, gros texte de 2 à 4 mots ; enregistrée à côté de la vidéo (`*_thumbnail.jpg`).

État : titre, description, chapitres et miniature implémentés (`pipeline.py`, `_youtube_package`), testés par `tests/test_services.py::test_youtube_package_and_tempo_matched_music`.
