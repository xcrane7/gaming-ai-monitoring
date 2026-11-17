# GAMING_AI_EDITOR

Montage vidéo gaming automatisé, **100 % local et gratuit**. Tu déposes tes captures de
gameplay dans `INPUT/`, le système produit tout seul :

- un **Short** vertical 9:16 (TikTok / Reels / YouTube Shorts), 30 à 60 secondes ;
- une **vidéo YouTube** horizontale 16:9, 5 à 12 minutes.

Moteur : FFmpeg + Python + OpenCV + SQLite. Aucun logiciel de montage payant, aucune API
payante. Les vidéos originales ne sont jamais modifiées, déplacées ni supprimées.

## Démarrage rapide (Windows 11)

1. Installe [Python 3.10+](https://www.python.org/downloads/) en cochant *Add python.exe to PATH*.
2. Double-clique `scripts\install.bat` : il crée l'environnement local, installe NumPy et
   OpenCV, propose d'installer FFmpeg (via winget) puis inspecte ta machine (GPU, NVENC…).
3. Double-clique `start.bat` : le dashboard s'ouvre sur <http://127.0.0.1:8765>.
4. Dépose une vidéo dans `INPUT\`. Les rendus arrivent dans `OUTPUT\shorts\` et
   `OUTPUT\youtube\`, avec un rapport dans `OUTPUT\reports\`.

Optionnel :

| Script | Effet |
|---|---|
| `scripts\enable_autostart.bat` | Lance l'application à chaque ouverture de session Windows |
| `scripts\install_subtitles.bat` | Sous-titres automatiques locaux (faster-whisper) |
| `scripts\benchmark.bat` | Compare les encodeurs de ta machine (CPU / NVENC) |
| `python -m gaming_ai_editor sfx` | Génère des effets sonores libres de droits |

Musique : dépose tes fichiers dans `assets\music\` (dossier vide = pas de musique).

## Ce que fait le système pour chaque vidéo

1. **Détection** — un fichier n'est pris que lorsque sa taille est stable (un enregistrement
   OBS en cours n'est pas traité à moitié).
2. **Compréhension** — explosions, tirs, impacts, rafales, flashs et rushs situés à 20 ms
   près ; menus et écrans de pause écartés ; suivi de la position de l'action ; couleurs.
3. **Sélection** — les meilleurs passages pour la durée du format. Short : le moment le
   plus fort en accroche. YouTube : ordre chronologique.
4. **Réalisation** — une identité visuelle propre à chaque vidéo, puis pour chaque
   événement l'effet adapté : punch zoom, tremblement, flash, RGB split, glitch, glow,
   distorsion, explosion dessinée, étincelles, texte cinétique, notification façon kill
   feed, compteur de combo ; speed ramp sur le moment le plus fort.
5. **Son** — musique générée (ou la tienne, avec son tempo détecté), coupes sur les temps,
   montée puis drop après le ralenti, son du jeu ralenti et grave pendant le speed ramp,
   effets sonores (impacts, booms, whooshs, riser) calés sur les animations.
6. **Rendu multicouche** — gameplay animé et étalonné, transitions, couche graphique,
   sous-titres, mixage normalisé à −14 LUFS.
7. **Contrôle qualité** — vérifications techniques + synchro des impacts, scintillement,
   fluidité, présence des animations, densité d'effets. Un rendu refusé est refait en mode
   prudent ; s'il échoue encore, il n'arrive jamais dans `OUTPUT/`.

L'architecture détaillée (dix moteurs, choix des effets, statuts des skills, limites) est
dans [`docs/ENGINES.md`](docs/ENGINES.md).

Si la source est plus courte que 5 minutes, la version YouTube contient la vidéo entière et
la QA le signale par un avertissement. Pour faire une vraie vidéo longue à partir de
plusieurs clips courts : `python -m gaming_ai_editor compile 1 2 3 --name soiree`.

## Les moteurs

| # | Moteur | Code |
|---|---|---|
| 1 | Gameplay Intelligence | `engines/intelligence.py`, `analysis/` |
| 2 | Intelligent Editing | `engines/editing.py`, `editor/plan.py` |
| 3 | Dynamic Motion Design | `engines/motion.py` |
| 4 | Gaming VFX | `engines/vfx.py` |
| 5 | Cinematic Color | `engines/color.py` |
| 6 | Audio Intelligence | `engines/audio.py` |
| 7 | Motion Graphics & HUD | `engines/graphics.py` |
| 8 | Automatic Rendering | `editor/render.py` |
| 9 | Quality Assurance | `qa/` |
| 10 | Skills Research | `research/` |

L'orchestrateur (`pipeline.py`, `watcher.py`) enchaîne les étapes, gère la file (un seul
rendu lourd à la fois, attente si la mémoire manque) et reprend les tâches après un arrêt.

## IA et coûts

Le système fonctionne entièrement **sans IA**. L'IA n'apporte qu'un plus :

- **Gemini** qualifie les moments forts (kill, victoire, fail, drôle) à partir d'images clés ;
- **Claude** écrit l'accroche, les mots d'impact, le titre, la description et les hashtags.

Sans Gemini, les notifications disent « Explosion » ou « Impact », jamais « Kill » : ce
sont les seules étiquettes que le son et l'image permettent d'affirmer.

Par défaut, seuls les programmes en ligne de commande de tes abonnements (`gemini`, `claude`)
sont utilisés, s'ils sont installés et connectés : cela ne déclenche aucun achat.
**Toute API payante est bloquée** tant que tu n'as pas écrit toi-même
`"allow_paid_api": true` dans `config.json` et fourni une clé. Chaque appel (autorisé ou
refusé) est journalisé et visible dans l'onglet *Système* du dashboard.

## Skills Researcher

Toutes les 6 heures, en priorité basse, et en pause dès qu'un rendu démarre :

1. **Découverte** — métadonnées PyPI d'une liste de veille + recherche GitHub (API publiques
   gratuites, sans clé).
2. **Licences** — AGPL, GPL et licences inconnues sont refusées.
3. **Expériences mesurées** — réglages d'encodage (vitesse, taille, qualité SSIM sur un extrait
   de ton dernier gameplay) ; outils externes installés dans un **bac à sable** (un venv isolé
   par outil, version épinglée, roues binaires uniquement, clés et jetons non transmis).
4. **Intégration** — uniquement si le gain est mesuré, la qualité ne baisse pas, et qu'un rendu
   de contrôle passe la QA. Sinon le candidat est rejeté, avec la raison chiffrée.
5. **Rollback** — la version précédente est conservée. Retour arrière automatique si la
   vérification après activation échoue, ou manuel depuis le dashboard
   (`python -m gaming_ai_editor research rollback --kind encode_profile`).

L'échéance est enregistrée : si le PC était éteint, le cycle repart au démarrage suivant
(il faut que l'application soit lancée — voir `enable_autostart.bat`).

Limite voulue : le chercheur n'installe tout seul que les outils pour lesquels un adaptateur
relu existe dans ce dépôt (`research/researcher.py`, `ADAPTERS`). Les autres découvertes sont
listées « à examiner manuellement » : installer automatiquement du code inconnu trouvé sur
Internet serait un risque de sécurité.

## Commandes

```
python -m gaming_ai_editor inspect            Inspecte la machine
python -m gaming_ai_editor run                Tout : surveillance INPUT + dashboard + chercheur
python -m gaming_ai_editor process [fichier]  Traite un fichier (ou tout INPUT) tout de suite
python -m gaming_ai_editor compile 1 2 3      Compilation de plusieurs vidéos analysées
python -m gaming_ai_editor status             Vidéos, tâches, rendus
python -m gaming_ai_editor benchmark          Compare les encodeurs
python -m gaming_ai_editor research run       Lance un cycle de recherche maintenant
```

## Configuration

Copie `config.example.json` en `config.json` et ne garde que ce que tu changes. Pour une
identité de chaîne : `"style": {"channel_name": "TonPseudo", "accent_color": "#FF2E88"}`. Toutes les
options et leurs valeurs par défaut sont dans `gaming_ai_editor/config.py` (durées cibles,
cadrage vertical `crop` / `blur` / `hybrid`, encodeur, musique, sous-titres, chercheur…).

## Tests

```
pip install pytest
python -m pytest tests                      57 tests, environ 13 minutes
GAE_TEST_NETWORK=1 python -m pytest tests   ajoute le cycle de recherche en ligne
```

Les tests fabriquent des gameplays synthétiques dont on connaît exactement les moments
d'action et les changements de plan, ce qui permet de vérifier objectivement la détection.

## État des vérifications

Vérifié par des tests exécutés (Linux, Python 3.13, FFmpeg 6.1, sans GPU) — 57 tests :

- montage complet d'un vrai gameplay FPS 4K : Short 1080×1920 de 43,5 s, 20 contrôles QA
  réussis, impacts sonores à 1 ms de l'instant prévu, originaux intacts ;
- détection des événements (tirs, explosions, rafales) et des menus sur vérité terrain ;
- speed ramp : durée rendue = durée calculée ; courbes d'easing ; expressions de caméra sûres ;
- tempo d'une musique retrouvé exactement, temps à moins de 40 ms, drop sur une mesure ;
- effets sonores placés à 20 ms près ; 5 looks d'étalonnage distincts ;
- les 16 éléments graphiques se dessinent et s'encodent avec transparence ;
- effets adaptés aux événements, jamais deux effets lourds à moins de 1,5 s, coupes sur le
  tempo, deux vidéos différentes = deux identités différentes ;
- sans Gemini, jamais de « kill » ni de « headshot » inventé ;
- registre : statuts, refus d'activer une version moins bonne, quarantaine et retour arrière ;
- chercheur : réglages du motion design et décodage GPU mesurés, cycle limité dans le temps,
  cycle en ligne complet (découverte, bac à sable, licences) ;
- toutes les vérifications de la version précédente (formats, QA, surveillance, dashboard,
  garde-fou des coûts, repli CPU).

**Pas encore vérifié — à contrôler sur ton PC :**

- **NVENC** sur la RTX 5070 Ti (aucun GPU ici ; seul le repli CPU a été testé) ;
- les scripts `.bat` et le démarrage automatique Windows ;
- le **CLI Gemini** réel (testé avec un programme factice) : sans lui, pas de kill feed
  « Élimination », de tampon HEADSHOT ni de bandeau VICTOIRE ;
- les sous-titres **Whisper** (téléchargement du modèle bloqué ici) ;
- les polices Windows (Impact, Bahnschrift) : ici le rendu utilise Inter Display Black.
