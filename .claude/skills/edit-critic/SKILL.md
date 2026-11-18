---
name: edit-critic
description: Relecture critique d'un rendu (images clés + checklist anti-robot) et comparaison avant/après. À utiliser avant d'affirmer qu'un changement améliore le montage.
---

# Relire un montage comme un monteur

Règle : **pendant la critique, ne pas toucher au code**. On mesure, on regarde, on note, on
propose ; on n'implémente qu'à la demande.

## 1. Extraire de quoi juger
```bash
python -m gaming_ai_editor.tools.critic <rendu.mp4> [--before <ancien.mp4>] [--before-plan <plan.json>] [--out <dossier>]
```
`--out` par défaut : `data/critique/<date>_<vidéo>/`. Le plan est retrouvé dans le rapport du
rendu (`OUTPUT/reports/*_job*.json`) ; sinon le passer avec `--before-plan` pour l'ancien.

Produit (`critique.json` + `critique.md`) :
- **métriques** : durée, énergie audio (dB, toutes les 0,5 s), pic audio, montée (dernier tiers
  − premier tiers), temps calme (son < −35 dB et image quasi fixe), nombre et durées des clips,
  coupes par seconde au début et à la fin, accroche dans les 2 premières secondes ;
- **images clés** (`images_cles.png`) : 0,1 s, 0,8 s, 1,5 s, pic (milieu du ralenti, sinon pic
  audio), milieu, fin ; planche par seconde, une image par clip et par jonction ;
- **grille /10** sur 9 lignes : accroche, variété des durées, courbe d'intensité, texte et
  légendes, impacts sonores, musique, fin voulue, transitions, temps morts — chaque note avec
  le pourquoi, ce qu'il faut regarder, la correction proposée et le fichier concerné ;
- **verdict** : les 3 points les plus faibles (notes < 8) seulement.

Regarder réellement les images (outil Read) avant tout jugement.

## 2. Checklist anti-robot (chaque point : oui / non + preuve)
1. Les 2 premières secondes donnent envie de rester (action ou accroche lisible) ?
2. Le climax est dans le dernier tiers, avec ralenti + drop ?
3. Les durées de clips varient (pas deux durées à ±20 % à la suite) ?
4. Chaque clip commence avant l'action et finit quand elle retombe (pas de coupe en plein vide) ?
5. Les transitions varient et tombent sur la musique ?
6. Le texte est lisible sur mobile et ne couvre pas l'action ?
7. Aucun menu, écran de pause ou chargement dans le montage ?
8. La fin est nette (boucle) ou appelle à l'action, sans traîne ?
9. Aucune affirmation inventée (kill sans étiquette IA ou OCR) ?

La fonction `checklist(plan, durée)` (6 contrôles automatiques) reste utilisée par les tests.

## 3. Avant / après
Même source, même configuration hormis le changement testé. Avec `--before`, l'outil ajoute
`avant_images_cles.png`, `avant_apres_images_cles.png` et les métriques / notes des deux
versions côte à côte. Ne dire « c'est mieux » que si la grille progresse **et** que les images
le montrent ; citer les images. Une ligne qui baisse se signale, même si la note globale monte.
