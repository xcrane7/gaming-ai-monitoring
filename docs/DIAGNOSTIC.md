# Diagnostic et points à traiter

Verdict du diagnostic externe : bonne base technique, pas de reconstruction.
Il faut travailler la qualité artistique, l'intelligence du montage et la vérification
visuelle. On traite les points un par un, sans toucher au code tant qu'un point n'est pas
validé par l'utilisateur.

## 1. Intelligence visuelle : comprendre le gameplay pour choisir les moments
**Vérifié dans le code :** sans IA, le choix repose sur le son (tirs, explosions,
réactions), le mouvement de l'image, les changements de plan, les menus et l'OCR (texte à
l'écran). La compréhension réelle de l'image (« que se passe-t-il ? ») dépend d'une IA
externe : Gemini (clé gratuite, 20 requêtes/jour) ou le CLI Claude, sur 3 images par
moment. Aucun modèle de vision local.
**Pas validé :** l'OCR et les réactions sur les vrais jeux de l'utilisateur (un seul
gameplay réel testé).
**À décider :** jeux joués ; IA disponible sur le PC (CLI Claude ? clé Gemini ?) ; modèle
de vision local acceptable (téléchargement de plusieurs centaines de Mo, carte graphique).

## 2. Motion design professionnel : animations, transitions, compositions, effets
**Vérifié :** 16 éléments graphiques dessinés image par image avec Pillow (accroche,
sous-titres mot à mot, notifications, combo, particules…), transitions FFmpeg (fondus,
whip, flash), caméra (punch-in, tremblement), ralentis, étalonnage. Les éléments ont un
rendu « gabarit » : mêmes formes, mêmes positions, pas d'animation liée aux objets de
l'image.
**À décider :** références visuelles précises (vidéos à montrer) ; quels effets manquent.

## 3. Direction créative : montages variés, identité, narration
**Vérifié :** une « identité » par vidéo (palette, look, style de transitions) tirée au
sort de façon déterministe, mais **une seule structure narrative** pour tous les Shorts
(cold open → montée → climax → boucle) et une seule pour le format long (chronologique).
Deux Shorts ont donc toujours la même forme.
**À décider :** quelles structures (exemples de montages aimés) ; identité de chaîne fixe
ou variable.

## 4. Contrôle qualité visuel : refuser les montages visuellement faibles
**Vérifié :** la QA bloquante est technique (résolution, durée, synchro, images noires,
silence, volume). Le score de mouvement n'est qu'un avertissement. L'outil de critique
(`tools/critic.py`, note /10) existe mais **n'est pas branché sur le pipeline** : aucun
montage n'est refusé pour faiblesse visuelle.
**À décider :** seuil de refus ; que faire d'un montage refusé (nouvel essai avec un autre
style ? le garder de côté ?).

## 5. Chercheur automatique
**Vérifié :** un seul adaptateur d'installation externe (PySceneDetect). Les autres
découvertes sont seulement listées ; les réglages internes du motion design sont mesurés
et activés automatiquement.
**À décider :** quelles technologies brancher en priorité, une fois les points 1 à 4 fixés.

## Décisions prises
(à remplir au fil de la discussion)
