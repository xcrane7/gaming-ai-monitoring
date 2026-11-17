---
name: edit-critic
description: Relecture critique d'un rendu (images clés + checklist anti-robot) et comparaison avant/après. À utiliser avant d'affirmer qu'un changement améliore le montage.
---

# Relire un montage comme un monteur

## 1. Extraire de quoi juger
```bash
python -m gaming_ai_editor.tools.critic <rendu.mp4> [--before <ancien.mp4>] --out <dossier>
```
Produit : planche d'images (une par seconde), une image au milieu de chaque clip et à chaque jonction, la courbe d'intensité, et un rapport JSON (durées des clips, transitions, instants des effets, position du climax).
Regarde réellement les images (outil Read) avant tout jugement.

## 2. Checklist anti-robot (chaque point : oui / non + preuve)
1. Les 2 premières secondes donnent envie de rester (action ou accroche lisible) ?
2. Le climax est dans le dernier tiers, avec ralenti + drop ?
3. Les durées de clips varient (pas deux durées à ±20 % à la suite) ?
4. Chaque clip commence avant l'action et finit quand elle retombe (pas de coupe en plein vide) ?
5. Les transitions varient et tombent sur la musique ?
6. Le texte est lisible sur mobile et ne couvre pas l'action ?
7. Aucun menu, écran de pause ou chargement dans le montage ?
8. La fin est nette (boucle) ou appelle à l'action, sans traîne ?
9. Aucune affirmation inventée (kill sans étiquette IA) ?

## 3. Avant / après
Même source, même configuration hormis le changement testé. Ne dire « c'est mieux » que si la checklist progresse ; citer les images qui le prouvent.
