# Architecture des moteurs — GAMING_AI_EDITOR

## 1. Audit de la version précédente

| Domaine | État avant | Constat |
|---|---|---|
| Détection des moments forts | Opérationnelle | Score son + mouvement par fenêtres de 0,5 s. Imprécis (±0,5 s), aucun type d'événement, ne distinguait pas un menu du jeu. |
| Plan de montage | Opérationnel | Bonne sélection par durée cible. Fusion de passages pouvant donner des clips de 27 s. |
| Rendu FFmpeg | Opérationnel | Découpe + recadrage + fondus. **Aucune animation** : pas de zoom, pas de texte animé, pas de VFX. |
| Musique | Partielle | Seulement si l'utilisateur fournit un fichier. Pas d'analyse du tempo. |
| Effets sonores | Partielle | Un seul whoosh sur les transitions, et seulement si le fichier existe. |
| Sous-titres | Opérationnels | Fichiers `.srt` fournis. Whisper présent mais jamais validé (modèle non téléchargeable dans l'environnement de test). |
| Couche graphique | **Absente** | Aucun moteur de composition. |
| Étalonnage | **Absent** | |
| QA | Opérationnelle | Contrôles techniques du fichier, mais rien sur la qualité du montage animé. |
| Skills Researcher | Opérationnel | Réglages d'encodage + détecteur de scènes en bac à sable. Statuts minuscules, pas de quarantaine, pas de limite de durée. |
| GPU | Partielle | NVENC détecté et testé réellement, repli CPU. Décodage matériel non mesuré. |
| MoviePy | Non utilisé | Le projet n'en a pas besoin : FFmpeg + NumPy + Pillow font tout, plus vite. |

Rien d'opérationnel n'a été supprimé. Le mode simple reste disponible
(`"style": {"enabled": false}` dans `config.json`), et le rendu « prudent » sans
effets sert de second essai automatique si un rendu animé échoue à la QA.

## 2. Les dix moteurs

```
                 ┌──────────────────────── 2. Intelligent Editing ───────────────────────┐
 vidéo ─► 1. Gameplay Intelligence ─► plan ─► Style DNA · effets par événement · ralentis   │
          (événements à 20 ms, menus,          · coupes sur le tempo · textes · transitions │
           suivi de l'action, couleurs)       └──────┬───────────┬──────────┬───────────────┘
                                                     │           │          │
                         3. Motion Design ◄──────────┘           │          └──► 6. Audio Intelligence
                         4. Gaming VFX                           │               (tempo, musique, SFX)
                         5. Cinematic Color                      ▼
                                │                    7. Motion Graphics & HUD
                                ▼                                │
                         8. Automatic Rendering ◄────────────────┘
                                │
                                ▼
                         9. Quality Assurance ──► 10. Skills Research (toutes les 6 h)
```

| # | Moteur | Fichier | Rôle |
|---|---|---|---|
| 1 | Gameplay Intelligence | `engines/intelligence.py` | Transitoires audio à 20 ms ; classement en tir / explosion / impact / rafale / flash / rush ; détection des menus et écrans de pause ; suivi horizontal de l'action ; profil colorimétrique ; rythme. |
| 2 | Intelligent Editing | `engines/editing.py` | Identité visuelle (palette, police, look, famille de transitions, intensité, cadrage) ; effets choisis selon la nature et la force de chaque événement ; délais minimum entre effets lourds ; ralenti sur le moment fort ; coupes calées sur le tempo ; textes. |
| 3 | Dynamic Motion Design | `engines/motion.py` | Courbes d'easing (Python et expressions FFmpeg) ; dynamic zoom, punch zoom, smooth zoom, beat pulse ; tremblement avec rotation ; pan/tilt ; suivi de l'action ; speed ramp progressif avec fusion d'images ; motion blur. |
| 4 | Gaming VFX | `engines/vfx.py` | Impact flash, RGB split, aberration chromatique, glitch, glow, distorsion d'objectif, vignette réactive, flou cinématique (mise au point). |
| 5 | Cinematic Color | `engines/color.py` | Cinq looks en LUT 3D calculées (`teal_orange`, `neon_night`, `warm_cinematic`, `cold_tactical`, `punchy`), choisis selon les couleurs de la vidéo. |
| 6 | Audio Intelligence | `engines/audio.py` | Détection du tempo et des temps (tempo exact, phase < 25 ms sur les tests) ; musique générée avec montée, passage filtré pendant le ralenti et drop ; 9 effets sonores synthétisés ; piste d'effets placée au 1/48 000 s. |
| 7 | Motion Graphics & HUD | `engines/graphics.py` | Compositeur image par image (Pillow) : accroche, texte cinétique, kill feed, headshot, combo, victoire, titre, bandeau bas, scoreboard, HUD, barre de progression, étincelles, onde de choc, explosion, braises, bouton d'abonnement ; icônes vectorielles. |
| 8 | Automatic Rendering | `editor/render.py` | Composition multicouche FFmpeg : gameplay animé → transitions → couche graphique → sous-titres ; son du jeu (grave pendant les ralentis) + musique (atténuée sous le jeu) + effets → −14 LUFS. NVENC si disponible. |
| 9 | Quality Assurance | `qa/checks.py`, `qa/motion.py` | Contrôles techniques + synchro des impacts (ms), scintillement non prévu, fluidité (comparée à la source), transitions, couche graphique, densité d'effets, débit. Score sur 100. |
| 10 | Skills Research | `research/` | Toutes les 6 h, priorité basse, en pause pendant les rendus, durée limitée : découverte (PyPI, npm, GitHub, filtres GPU de FFmpeg), licences, bac à sable, benchmarks, intégration automatique si meilleur, quarantaine si régression. |

## 3. Comment un effet est choisi

| Événement détecté | Caméra | VFX | Graphisme | Son |
|---|---|---|---|---|
| Explosion | punch zoom + tremblement | flash, glow, distorsion si forte | explosion (lueur, onde de choc, étincelles) | boom |
| Impact / tir | punch zoom (+ tremblement si fort) | flash, RGB split une fois sur deux | étincelles + onde de choc | impact |
| Rafale (≥ 3 coups) | — | glitch | compteur de combo | glitch |
| Flash visuel | punch léger | glow | — | impact |
| Rush (mouvement soutenu) | — | motion blur | — | whoosh |
| Moment le plus fort | speed ramp + smooth zoom | glitch d'entrée + flash | mot « slam » | riser + sub drop, musique filtrée puis drop |
| Kill / headshot / victoire (**Gemini uniquement**) | — | — | kill feed, tampon HEADSHOT, bandeau VICTOIRE | ding |

Structure d'un Short (`short.structure = "story"`) : cold open de 1,5 à 2 s sur le meilleur
moment (coupé avant sa résolution, avec l'accroche), montée chronologique en coupes sèches,
clips sans action retirés, durées variées (jamais deux à ±20 % de suite), climax en entier à la
fin avec ralenti et drop, fin en boucle. Bornes de chaque clip adaptées à l'action
(`_adaptive_bounds`). Outil de relecture : `python -m gaming_ai_editor.tools.critic`
(métriques, images clés, grille /10, avant/après ; skill `edit-critic`).

Emballage de publication : `gaming_ai_editor/packaging.py` (skill `youtube-packaging`) écrit
`OUTPUT/reports/<vidéo>.metadata.json` après la QA : 3 titres, description, chapitres du plan
long, hashtags, miniature PNG. Claude (CLI) si disponible, réponse validée, sinon modèles locaux.

Règles anti-saturation : deux effets lourds (tremblement, glitch, distorsion)
jamais à moins de 1,5 s ; un mot d'impact au plus toutes les 1,6 s en court,
3 s en long ; trois notifications empilées au maximum ; un seul ralenti par Short.

Sans Gemini, le logiciel ne prétend jamais qu'un événement est un kill : la
notification dit « Explosion » ou « Impact ». Ce sont les seules étiquettes que
le son et l'image permettent d'affirmer.

### Contrôle qualité visuel (qa/visual.py)
Mesuré sur les images rendues, pas sur le plan : images brûlées (2 images/s sur toute la
vidéo, 15 images/s sur les 3 premières secondes : une seule image blanche dans l'accroche
est un refus), images floues ajoutées par le montage (comparées aux mêmes passages de la
source : les jeux ont leur propre flou de mouvement), images ternes. Un refus relance le
rendu « prudent ». Une comparaison directe de netteté rendu/source a été essayée puis
abandonnée : le rendu ajoute des détails fins (particules, accentuation, compression) et
paraissait « plus net » que le jeu même flouté.

## 4. Statuts des skills

`CANDIDATE` → `TESTING` → `VALIDATED` / `REJECTED` → `ACTIVE` → (régression) → `QUARANTINE`

- Une version n'est activée que si sa mesure est meilleure que la version active,
  mesurée dans les mêmes conditions. Le registre refuse aussi d'activer une version
  au score inférieur pour la même métrique.
- L'ancienne version active redevient `VALIDATED` et reste disponible pour un retour arrière.
- Si un rendu en production échoue à la QA avec les skills actifs et réussit en mode
  prudent, les skills non d'origine sont mis en `QUARANTINE` et la version
  précédente est réactivée automatiquement.

Types de skills mesurés aujourd'hui :

| Type | Ce qui est mesuré |
|---|---|
| `encode_profile` | vitesse, taille et SSIM sur un extrait de ton dernier gameplay |
| `motion_preset` | intensité des zooms, tremblements et flashs : score QA du montage + lisibilité des impacts |
| `decode_accel` | décodage matériel (CUDA, D3D11VA, QSV…) contre CPU, à résultats identiques |
| `scene_detector` | outil externe (PySceneDetect) en bac à sable, F1 sur vérité terrain |

## 5. Limites connues

- **Créativité mesurée ≠ goût** : le chercheur optimise ce qu'il peut mesurer
  (synchro, scintillement, lisibilité des impacts, vitesse). Il ne juge pas si un
  montage est « beau ».
- **Intégration de code inconnu** : le chercheur découvre et évalue des outils, mais
  n'installe seul que ceux pour lesquels un adaptateur relu existe dans
  `research/runners/`. Exécuter automatiquement du code trouvé sur Internet,
  sans adaptateur, serait un risque pour ton PC.
- **Remotion** est suivi par le chercheur, mais sa licence impose un abonnement
  payant pour les entreprises : il n'est pas intégré.
- **Rendu CPU** : sans GPU, un Short de 45 s à partir d'une source 4K prend
  plusieurs minutes. NVENC n'accélère que l'encodage : les filtres d'animation
  tournent sur le processeur.
