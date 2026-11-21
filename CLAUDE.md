# GAMING_AI_EDITOR — contexte pour Claude Code

Montage vidéo gaming automatisé, 100 % local (FFmpeg + Python + OpenCV + Pillow + SQLite).
INPUT/ → analyse → plan de montage (EDL) → réalisation → rendu multicouche → QA → OUTPUT/shorts et OUTPUT/youtube.
Machine : Windows 11, RTX 5070 Ti (NVENC). Langue du projet : français.
Règles communes à tous les agents : `AGENTS.md`. Architecture : `docs/ENGINES.md`.

## Objectif n°1 : que le montage ne fasse PAS « robot »

État du diagnostic (vérifié dans le code et sur un rendu réel, outil `tools/critic.py`) :

| Problème | État |
|---|---|
| Moments forts choisis par son + mouvement seulement | **Amélioré** : événements typés à 20 ms (dont réactions du joueur et silence puis pic), séries, texte à l'écran (OCR : kill feed, VICTORY, WASTED…), doublons et répétitions pénalisés, menus exclus, vision IA sur 3 images par moment. Détails : skill `highlight-intelligence`. |
| Découpe identique (`pre_roll` / `post_roll` fixes) | **Corrigé** : `_adaptive_bounds` (début dans le calme qui précède, fin quand l'action retombe, calée sur les changements de plan). `bounds: "fixed"` garde l'ancien comportement. |
| Transitions en rotation fixe | **Corrigé** (montage stylé) : coupes sèches pendant la montée, transitions visibles entre séquences, jamais deux fois la même. `XFADE_STYLES` ne sert plus qu'au mode simple. |
| Aucun texte, sous-titres taille 16 | **Corrigé** : accroche animée, mots d'impact, sous-titres mot à mot (élément `captions`). |
| Aucun zoom / ralenti / impact | **Corrigé** : punch zoom, tremblement, speed ramp, flash, particules, explosions dessinées. |
| Musique au hasard, sans tempo | **Corrigé** : musique fournie choisie par tempo (selon l'énergie de la vidéo), coupes sur les temps ; sinon musique générée avec montée et drop. |
| Pas de structure narrative | **Corrigé** : cold open → montée → climax (ralenti + drop) dans le dernier tiers → fin en boucle. Clips sans action retirés, durées variées. |

Mesure avant/après sur le gameplay FPS de référence (rendus réels, images regardées) :
checklist `edit-critic` passée de 4/6 à 6/6 ; climax de 21 % à 84 % de la vidéo ; premier clip
de 18 s remplacé par un cold open de 1,8 s ; clip sans action retiré ; 1 image surexposée au lieu
de 3. Ne pas régresser.

Refonte « pro » (branche feature/pro-motion-overhaul, mesurée sur le même gameplay) :
cadrage vertical plein écran (le mode auto donnait une zone 4:5 à 70 % de la hauteur pour
les FPS), HUD caméscope coupé par défaut, mots d'impact de l'IA coupés (option
`style.ai_captions`), looks qui ne désaturent plus, cible animée `focus_ring` sur le climax
et les kills confirmés, plus d'image blanche pendant l'accroche. QA visuelle
(`qa/visual.py`) : images brûlées (une seule dans les 3 premières secondes = refus), flou
AJOUTÉ par le montage (le flou de mouvement du jeu, 30 % sur ce FPS, est mesuré sur la
source et retiré), images ternes.

Pistes restantes (non faites) : profils de jeux réels (zones et mots-clés), piste micro séparée,
rires ; vérifier sur de vrais gameplays de l'utilisateur (un seul gameplay réel testé, sans voix
ni texte de jeu : l'OCR et les réactions ne sont validés que sur vidéos de test).

## Règles de travail
- Ne jamais modifier/déplacer/supprimer les originaux de INPUT/ (la QA le vérifie par checksum).
- Aucune API payante : `ai.allow_paid_api` reste à False. Claude via son CLI (abonnement).
  Gemini : son CLI refuse les comptes personnels depuis juin 2026 ; le projet accepte à la place
  une **clé gratuite** AI Studio (projet sans facturation, 20 requêtes/jour max). À retirer si
  l'utilisateur ne la veut pas (`ai.gemini.mode = "off"`).
- Toute nouvelle option va dans `config.py` (DEFAULTS) ET `config.example.json`, désactivable.
- Tout changement de rendu passe par la QA (`qa/checks.py`, `qa/motion.py`) et les tests (`python -m pytest tests`).
- Les tests utilisent des gameplays synthétiques à vérité terrain : en ajouter pour chaque fonction.
- NVENC réel non vérifié : garder le repli CPU.
- Avant de dire « c'est mieux » : rendre un Short réel, lancer
  `python -m gaming_ai_editor.tools.critic <rendu> --before <ancien> --out <dossier>`,
  **regarder les images** et comparer la checklist. Pas de jugement sans regarder.

## Skills du projet (dans .claude/skills/)
- `gaming-edit-direction` : règles créatives (rythme, accroche, structure) — à lire avant toute modif du montage.
- `highlight-intelligence` : détecter de VRAIS moments forts (événements audio, menus, vision Gemini/Claude, kill feed).
- `shorts-hook-captions` : accroche texte, sous-titres animés mot à mot, boucle de fin.
- `edit-polish-ffmpeg` : punch-in, ralentis, impacts, caméra shake, étalonnage, musique synchronisée.
- `edit-critic` : relecture critique d'un rendu (images clés + checklist anti-robot) et A/B.
- `youtube-packaging` : titre, description, chapitres, miniature pour la version YouTube.
