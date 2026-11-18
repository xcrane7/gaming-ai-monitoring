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
2. **Compréhension** — explosions, tirs, impacts, rafales, flashs, rushs, réactions du joueur
   (cri) et « silence puis pic » situés à 20 ms près ; séries d'actions regroupées ; texte à
   l'écran lu (kill feed, VICTORY, WASTED…) ; doublons visuels pénalisés ; menus et écrans de
   pause écartés ; puis l'IA regarde 3 images (début, pic, fin) des meilleurs moments.
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

- **Gemini ou Claude** regarde une image de chaque moment fort et l'étiquette (kill,
  headshot, victoire, fail, drôle…) ;
- **Claude** écrit l'accroche, les mots d'impact, le titre, la description et les hashtags.

Sans Gemini, les notifications disent « Explosion » ou « Impact », jamais « Kill » : ce
sont les seules étiquettes que le son et l'image permettent d'affirmer.

Par défaut, seuls les programmes en ligne de commande de tes abonnements (`gemini`, `claude`)
sont utilisés, s'ils sont installés et connectés : cela ne déclenche aucun achat.
**Toute API payante est bloquée** tant que tu n'as pas écrit toi-même
`"allow_paid_api": true` dans `config.json` et fourni une clé. Chaque appel (autorisé ou
refusé) est journalisé et visible dans l'onglet *Système* du dashboard.

### Sans clé Gemini : Claude fait l'analyse des images

Si Claude Code est installé et connecté à ton abonnement Claude, l'application lui fait
regarder les images des moments forts quand Gemini n'est pas disponible. Aucune clé, aucun
frais en plus de ton abonnement ; chaque vidéo consomme un peu de ton quota d'utilisation
Claude (un appel pour l'analyse, un pour les textes, un pour le titre).

1. Installe Node.js, puis dans PowerShell : `npm install -g @anthropic-ai/claude-code`
2. Lance `claude` une fois et connecte-toi avec ton compte Claude. Tape `/exit`.
3. Relance `start.bat`. Onglet *Système* : « Claude : CLI d'abonnement détecté (analyse
   des images + textes) ».

Pendant cet appel, Claude n'a le droit qu'à un seul outil : lire les images. Commandes,
modification de fichiers et accès web lui sont interdits. Ordre d'essai réglable :
`"ai": {"vision_order": ["gemini", "claude"]}`.

### Brancher Gemini (clé gratuite Google AI Studio, facultatif)

Depuis juin 2026, Google refuse la connexion avec un compte personnel dans Gemini CLI
(message « This client is no longer supported for Gemini Code Assist for individuals »).
L'application appelle donc Gemini directement avec une **clé API gratuite** :

1. Va sur <https://aistudio.google.com>, connecte-toi, clique **Get API key** puis
   **Create API key**. Copie la clé.
2. **N'active pas la facturation** (« Set up billing ») sur ce projet : sans facturation,
   Google refuse les requêtes au-delà du quota gratuit au lieu de les faire payer.
3. Dans le dossier du projet, crée un fichier texte `gemini_key.txt` et colle la clé
   dedans, seule sur la ligne. Ce fichier n'est jamais envoyé sur GitHub.
4. Relance `start.bat`. Onglet *Système* du dashboard : « Gemini : API gratuite (clé détectée) ».

Une seule requête par vidéo (les images des moments forts, en une fois), 20 par jour au
maximum (`ai.gemini.max_requests_per_day`). Si le quota gratuit est épuisé, le montage se
fait sans Gemini jusqu'au lendemain. Sur l'offre gratuite, Google peut utiliser les images
envoyées pour améliorer ses produits : ce ne sont que des images de ton gameplay.

Gemini étiquette chaque moment (kill, headshot, victoire, fail, drôle…). Ces étiquettes
déclenchent la notification « Élimination », le tampon HEADSHOT et le bandeau VICTOIRE.

### Faire travailler un agent (Antigravity, Gemini, Claude Code) sur le code

Ouvre le dossier du projet dans **Antigravity** (l'outil de Google qui remplace Gemini
CLI) et commence par : « Lis AGENTS.md, README.md et docs/ENGINES.md ». `AGENTS.md`
contient le contexte et les règles du projet. Ne fais pas travailler deux agents en
même temps sur les mêmes fichiers.

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
python -m pytest tests                      95 tests, environ 19 minutes
GAE_TEST_NETWORK=1 python -m pytest tests   ajoute le cycle de recherche en ligne
```

Les tests fabriquent des gameplays synthétiques dont on connaît exactement les moments
d'action et les changements de plan, ce qui permet de vérifier objectivement la détection.

## État des vérifications

Vérifié par des tests exécutés (Linux, Python 3.13, FFmpeg 6.1, sans GPU) — 95 tests :

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

- lecture du texte à l'écran (OCR, RapidOCR) : « ELIMINATED » et « VICTORY » retrouvés sur des
  vidéos de test, aucun faux kill sur des mots proches (« limited », « estimated ») ; réactions
  vocales et « silence puis pic » détectés sur vidéos de test, aucun faux positif sur le
  gameplay FPS réel ;

**Pas encore vérifié — à contrôler sur ton PC :**

- l'OCR et les réactions sur **tes** jeux : les zones du kill feed varient d'un jeu à l'autre
  (réglables dans `config.json`, clé `games`) et sans piste micro séparée, une musique de jeu
  très vocale peut passer pour une réaction ;

- **NVENC** sur la RTX 5070 Ti (aucun GPU ici ; seul le repli CPU a été testé) ;
- les scripts `.bat` et le démarrage automatique Windows ;
- un **vrai appel à Gemini** avec une clé AI Studio (le format de la requête et la
  gestion des erreurs sont testés avec un faux serveur, sans vraie clé). L'analyse des
  images par **Claude**, elle, a été testée pour de vrai sur le gameplay FPS. Sans aucune IA, pas de kill feed
  « Élimination », de tampon HEADSHOT ni de bandeau VICTOIRE ;
- les sous-titres **Whisper** (téléchargement du modèle bloqué ici) ;
- les polices Windows (Impact, Bahnschrift) : ici le rendu utilise Inter Display Black.
