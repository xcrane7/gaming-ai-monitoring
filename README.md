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
2. **Analyse** — énergie sonore (tirs, explosions, cris), mouvement à l'image, changements de
   plan. Les signaux sont fusionnés en une courbe d'intensité dont les pics sont les moments forts.
3. **Sélection** — les meilleurs passages sont choisis pour atteindre la durée cible du format.
   Short : le moment le plus fort en accroche. YouTube : ordre chronologique.
4. **Montage** — recadrage vertical (zone nette centrée sur fond flouté), fondus, effets
   sonores sur les transitions, musique baissée automatiquement sous le son du jeu,
   sous-titres incrustés s'il y en a, volume normalisé à −14 LUFS.
5. **Contrôle qualité** — 13 vérifications par rendu (résolution, durée, synchro audio/vidéo,
   images noires, erreurs de décodage, volume, originaux intacts…). Un rendu refusé n'arrive
   jamais dans `OUTPUT/` : il est retenté en mode prudent, puis mis de côté dans `data/rejected/`.

Si la source est plus courte que 5 minutes, la version YouTube contient la vidéo entière et
la QA le signale par un avertissement. Pour faire une vraie vidéo longue à partir de
plusieurs clips courts : `python -m gaming_ai_editor compile 1 2 3 --name soiree`.

## Les agents

| Agent | Rôle | Code |
|---|---|---|
| Orchestrateur | Enchaîne les étapes, file d'attente, mémoire, reprise après arrêt | `pipeline.py`, `watcher.py` |
| Analyste | Signaux audio/vidéo → moments forts ; Gemini en option pour les qualifier | `analysis/`, `ai/providers.py` |
| Video Editor | Plan de montage puis rendu FFmpeg (NVENC, repli CPU automatique) | `editor/` |
| Skills Researcher | Amélioration continue toutes les 6 h | `research/` |
| QA & Benchmark | Contrôle des rendus, mesures de performance | `qa/` |

## IA et coûts

Le système fonctionne entièrement **sans IA**. L'IA n'apporte qu'un plus :

- **Gemini** qualifie les moments forts (kill, victoire, fail, drôle) à partir d'images clés ;
- **Claude** propose titre, description et hashtags.

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

Copie `config.example.json` en `config.json` et ne garde que ce que tu changes. Toutes les
options et leurs valeurs par défaut sont dans `gaming_ai_editor/config.py` (durées cibles,
cadrage vertical `crop` / `blur` / `hybrid`, encodeur, musique, sous-titres, chercheur…).

## Tests

```
pip install pytest
python -m pytest tests                      36 tests, environ 4 minutes
GAE_TEST_NETWORK=1 python -m pytest tests   ajoute le cycle de recherche en ligne
```

Les tests fabriquent des gameplays synthétiques dont on connaît exactement les moments
d'action et les changements de plan, ce qui permet de vérifier objectivement la détection.

## État des vérifications

Vérifié par des tests exécutés (Linux, Python 3.13, FFmpeg 6.1, sans GPU) :

- détection des moments forts et des changements de plan sur vérité terrain ;
- Short 30–60 s et vidéo longue 5–12 min réelles, les trois cadrages verticaux ;
- musique, effets sonores, sous-titres `.srt` recalés sur le montage et incrustés ;
- sources sans son, carrées, noms de fichiers avec espaces ;
- QA : refus des rendus défectueux (résolution, écran noir, fichier tronqué) ;
- originaux intacts (somme de contrôle avant/après) ;
- repli CPU quand NVENC échoue ;
- surveillance d'INPUT, reprise des tâches interrompues, compilation multi-vidéos ;
- Skills Researcher : cycle complet en ligne, bac à sable, promotion, rejet, rollback, pause
  pendant un rendu, planification persistante ;
- garde-fou des coûts : API payante refusée sans autorisation ;
- appel réel au CLI Claude (titre et description) ;
- dashboard : API, lecture vidéo, protection contre les requêtes d'un autre site.

**Pas encore vérifié — à contrôler sur ton PC :**

- l'encodage **NVENC** réel sur la RTX 5070 Ti (aucun GPU dans l'environnement de
  développement ; seul le repli CPU a été testé). `scripts\install.bat` te dira s'il fonctionne ;
- les scripts `.bat` et le démarrage automatique Windows (écrits mais jamais exécutés sur Windows) ;
- le **CLI Gemini** réel (testé uniquement avec un programme factice qui imite ses réponses) ;
- les sous-titres automatiques **Whisper** (le téléchargement du modèle était bloqué ici) ;
- la pertinence de la détection sur de vrais gameplays : elle repose sur le son et le
  mouvement, elle ne « comprend » pas un kill ou une victoire sans Gemini.
