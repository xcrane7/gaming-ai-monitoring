# Instructions pour les agents de code — GAMING_AI_EDITOR

À lire avant toute modification, quel que soit l'agent (Antigravity, Gemini, Claude Code…).

## Le projet

Application locale (Windows 11, RTX 5070 Ti, 16 Go) qui transforme des captures de
gameplay en Shorts 9:16 (30–60 s) et en vidéos YouTube 16:9 (5–12 min), avec un vrai
montage animé. Python + FFmpeg + NumPy + OpenCV + Pillow + SQLite. Interface en français.

Lis d'abord `README.md` puis `docs/ENGINES.md` (architecture des dix moteurs).

| Dossier | Contenu |
|---|---|
| `gaming_ai_editor/engines/` | moteurs : intelligence, editing, motion, vfx, color, audio, graphics |
| `gaming_ai_editor/editor/` | plan de montage et rendu FFmpeg multicouche |
| `gaming_ai_editor/analysis/` | extraction des signaux audio/vidéo |
| `gaming_ai_editor/qa/` | contrôle qualité technique et du montage animé |
| `gaming_ai_editor/research/` | Skills Researcher (registre, bac à sable, expériences) |
| `gaming_ai_editor/ai/providers.py` | appels à Gemini et Claude, garde-fou des coûts |
| `tests/` | tests pytest (gameplays synthétiques à vérité terrain connue) |

## Règles à respecter

1. **Ne jamais modifier, déplacer ni supprimer** un fichier de `INPUT/` : les originaux
   restent intacts (la QA le vérifie par somme de contrôle).
2. **Aucune dépense** : pas d'API payante, pas de service cloud. `ai.allow_paid_api`
   reste `false` sauf si l'utilisateur le change lui-même.
3. **Ne rien supprimer qui fonctionne.** Le mode simple (`"style": {"enabled": false}`) et
   le second essai « prudent » doivent continuer à marcher.
4. **Ne jamais déclarer une fonctionnalité terminée sans l'avoir testée** :
   `python -m pytest tests` doit passer (environ 13 minutes ; un fichier précis :
   `python -m pytest tests/test_engines.py`).
5. Ajouter un test pour chaque nouvelle fonctionnalité, sur des vidéos générées par
   `gaming_ai_editor/synth.py` plutôt que sur des fichiers externes.
6. Pas de nouvelle dépendance sans vérifier sa licence (MIT, BSD, Apache, LGPL, ISC,
   MPL : oui ; GPL, AGPL, licence inconnue ou commerciale : non) et sans l'ajouter à
   `requirements.txt`.
7. Code et messages en français, comme le reste du projet.
8. Ne jamais écrire de clé API dans le code, les journaux ou git (`gemini_key.txt` est
   ignoré par git).
9. Sans étiquette de Gemini, ne jamais afficher « kill », « headshot » ou « victoire » :
   le son et l'image seuls ne permettent pas de l'affirmer.

## Plusieurs agents sur le même dépôt

Claude Code et Antigravity travaillent sur ce projet. Pour éviter les conflits :
- un seul agent modifie le code à la fois ;
- travaille sur ta propre branche git (`git switch -c agent/<sujet>`) ;
- fais `git pull` avant de commencer et des commits petits et clairs.

## Commandes utiles

```
python -m gaming_ai_editor inspect            état de la machine (FFmpeg, NVENC, modules, CLI)
python -m gaming_ai_editor process <fichier>  monter une vidéo tout de suite
python -m gaming_ai_editor run                surveillance d'INPUT + dashboard + chercheur
python -m gaming_ai_editor research run       un cycle du Skills Researcher
python -m pytest tests                        tous les tests
```
