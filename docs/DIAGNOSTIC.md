# Diagnostic et points à traiter

Verdict du diagnostic externe : bonne base technique, pas de reconstruction.
Il faut travailler la qualité artistique, l'intelligence du montage et la vérification
visuelle. On traite les points un par un, sans toucher au code tant qu'un point n'est pas
validé par l'utilisateur.

## 0. Référence fournie par l'utilisateur (à confirmer)
Short GTA 6 de 42 s, 9:16, compte TikTok « @zouna.gta ». Ce n'est **pas** un montage de
moments forts mais une vidéo **explicative** :
- écran partagé : en haut un panneau animé (mots-clés en deux couleurs « Plafond / secret »,
  compteurs « Épargne 5 000 $ → 174 000 $ », « Perdu 168 $ → 0 $ », cartes d'image : écran
  de téléphone, illustration), en bas du gameplay GTA 6 qui illustre le sujet (banque,
  conduite, garage, bateau), un plan toutes les ~4 s, fine ligne rose/cyan entre les deux ;
- une couleur par idée (vert = épargne, cyan = plafond, rouge = perte, rose = secret),
  police ronde très grasse, fond flouté ; pas de tremblement, flash ni particules ;
- portée par la narration (son moyen −16,8 dB) : les mots à l'écran suivent le texte dit ;
- fin : question aux spectateurs (« Quel palier ? ») pour les commentaires, « abonne-toi ».
Conséquence : le logiciel actuel (montage d'action, effets « gaming ») ne produit pas ce
genre de vidéo. À confirmer avec l'utilisateur avant tout développement.

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

## Second diagnostic (autre IA), vérifié dans le code
1. Skills Melvynx/video-agent-skills : licence MIT. Flux de travail complet pour créateurs
   (Node 20, Chromium, whisper.cpp ~0,6 Go) ; /steal mesure le style d'une vidéo avec ffmpeg.
   Des services payants y sont optionnels (Treg, ElevenLabs, Codex) : à laisser coupés.
   Remotion n'y figure pas ; sa licence n'est pas libre (gratuite pour particuliers et petites
   structures, à vérifier) : contraire à la règle des licences d'AGENTS.md.
2. « Mots d'impact » de ClaudeDirector.creative_texts : confirmé, écrits sans la transcription.
3. Sous-titres : déjà en mode auto, word_timestamps déjà actif, scripts/install_subtitles.bat
   existe. Il reste à l'exécuter sur le PC de l'utilisateur.
4. Moteur d'animation : l'ASS ne sert qu'au style « classic » ; le style par défaut dessine
   avec Pillow. Punch-in, tremblement et flash existent déjà (FFmpeg) ; l'arrêt sur image
   avec cercle/flèche n'existe pas. Un rendu HTML/CSS dans Chromium (licences BSD/Apache)
   peut remplacer Pillow pour la piste transparente déjà incrustée par FFmpeg.
5. Vérification visuelle sur un rendu réel : déjà une règle de CLAUDE.md, à appliquer à
   chaque étape. Les tests restent obligatoires (règle du projet).
Conflit : les effets du point 4 (tremblement, flash, zoom) vont à l'opposé de la vidéo de
référence de l'utilisateur (explicative, sans aucun de ces effets).

## Décisions prises
**Point 1 (en cours)**
- Jeux : FPS et GTA 6. Deux définitions du « bon moment » : FPS = éliminations,
  multi-kills, clutch, kill feed ; GTA 6 = poursuites, crashs, cascades, explosions,
  moments drôles (pas de kill feed : l'OCR des FPS ne sert presque pas).
- IA sur le PC : Claude Code (déjà branché : mode auto, il regarde les images des moments
  dès que la commande `claude` existe), OpenCode, abonnement Gemini. L'abonnement Gemini
  grand public ne donne pas d'accès programmable (le CLI refuse les comptes personnels) :
  une clé gratuite AI Studio reste possible en secours. OpenCode : pas nécessaire.
- Prochaine étape : un vrai gameplay FPS et un vrai gameplay GTA 6 de l'utilisateur pour
  régler la détection sur ses jeux.
