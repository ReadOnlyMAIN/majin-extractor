# Reproduction du rendu de tar dans Godot

**Correction d’assemblage :** voir [PLAYER_MODEL_USAGE.md](PLAYER_MODEL_USAGE.md).
Les équations du shader restent validées. Le contrôleur a remplacé le mélange
base chr101/tar chr100 par les géométries complètes de chaque famille, choisies
par `cinematic_render`. Le biais de profondeur est maintenant nul par défaut ;
le test de ce biais plus bas documente le correctif intermédiaire.

État vérifié le 27 octobre 2026, Godot 4.8-dev7.

## Équations et ressources originales

`player_darkness.gdshader` reproduit les huit passes de KbTarModel : masque
COLOR.r/tarHP, test alpha, projection UV polynomiale, normales combinées,
éclairage hémisphérique, maximum des termes Phong des lumières, Fresnel linéaire,
environnement cubique ou sphérique et brouillard linéaire/exp/exp2. Les quatre
ressources originales correspondent à zéro à trois lumières ponctuelles.
Les équations et leurs variantes sont détaillées dans
[TAR_SHADER_FORMULA.md](TAR_SHADER_FORMULA.md).

L'inspection du code CPU de l'exécutable local établit les bindings suivants :

| Ressource | Usage | Adresse PPU du binding |
| --- | --- | --- |
| KB/texture/waterwave_n2 | textureSamplerTar | 0x43b730 |
| KB/texture/AmbientSphere3 | textureSamplerAmbient, cas matériau 2/3 | 0x43b948 |
| KB/texture/noise | textureSamplerAmbient conditionnel, cas 4/5 | 0x43b840 |

Les champs du contexte sont respectivement +0xd34, +0xd40, +0xd4c. Le handle
textureSamplerTar est +0xb94 et textureSamplerAmbient +0xb9c. Les noms ne sont
donc pas choisis d'après la ressemblance des images. Le contrôleur utilise
waterwave_n2 et AmbientSphere3 ; le choix conditionnel exact de chaque matériau
reste à confirmer. `noise` est exporté et disponible comme override.

Le constructeur contient une vitesse .006 et un angle 90 degrés pour le canal
utilisé par le chemin tar (0x4ead0, 0x4eaf8). Un second canal vaut .02/270 degrés.
Le défaut de prévisualisation est désormais .1/90, après observation visuelle de
l’utilisateur indiquant une vitesse d’au moins .1. La constante .006 du
constructeur ne suffit donc pas à établir la vitesse finale. Les overrides de
carte et toute la transformation CPU des phases restent à confirmer.
Une branche de transition tarHP utilise une largeur .1 et une pente 10
(0x9437c, 0x94390), avec lissage temporel de la cible. Ce n'est pas une preuve de
la fonction PV→tarHP. Une branche de matériau tar initialise matParam0 à
(24,1,1,.4), mais d'autres sélecteurs changent notamment l'exposant.

`tools/research/tar_cpu_inspect.py` vérifie les instructions aux adresses
attendues avant d'émettre ces éléments. L'ELF examiné a le SHA-256
`4545d083e43159c7d08e83a60e5b342993de79547da5a602aabb18ba809c1f80`.
Aucun exécutable ou matériel de déchiffrement n'est inclus dans le dépôt.

## Intégration

L'exporteur installe les trois images originales dans `tar_globals/` et produit
un manifeste des ressources et SHA-256. Une ressource absente provoque un
avertissement d'export, jamais un remplacement par une texture supposée proche.

Les textures vectorielles sont importées sans compression destructive et avec
mipmaps. Les samplers du shader conservent RGB ; ils n'utilisent pas la
compression automatique des normales, qui peut perdre le bleu. Les normales
locales exportées ont déjà le vert inversé pour Godot : le shader annule cette
conversion avant d'appliquer les équations originales. Les textures globales
restent dans leur convention d'origine. Les bits 0x80/0x100 de la clé matériau
fixent les signes tangent/binormale, conformément au code CPU 0x93038–0x93078.
Les matériaux tar connus de chr1 inversent la binormale.

Le contrôleur duplique les matériaux par joueur, lie les textures et actualise
la position de racine et le défilement à chaque frame. La progression de
contamination reste indépendante : COLOR.r, puis tarHP.x/y/z. Le curseur de
vie est une correspondance de prévisualisation, avec override tarHP explicite.

Un décalage optionnel de profondeur peut séparer les surfaces propres et tar presque
confondues. Seule la profondeur projetée est modifiée : la silhouette, les UV et
les positions utilisées pour l'éclairage restent inchangés. Ce paramètre Godot
(`darkness_depth_bias`) est désactivable ; il ne prétend pas être un état RSX
récupéré. Le masque de couverture conserve son alpha original : seule la bande
de transition doit laisser apparaître l'armure sous-jacente.

Deux modes sont disponibles :

- `darkness_scene_lighting=true` : lumières et ombres Godot, termes Phong dans
  `light()`. L'atténuation ponctuelle native diffère de la portée linéaire RSX.
- `false` : calcul intégral à partir des uniforms explicites de la frame,
  y compris portée linéaire, ombre écran, brouillard et environnement source.
  C'est le mode utilisé pour comparer les équations numériquement.

Le Lab alimente les paramètres depuis son environnement et ses lumières.
Son panneau chr1 permet de régler armure, vie, couleur, bord, normales, reflets
et mouvement. Réinitialiser l'apparence conserve l'armure et la vie.

## Vérifications

- 178 tests Python réussis ; les équations sont confrontées à un interpréteur
  indépendant des instructions RSX sur 768 cas fragment et 128 cas vertex.
- 384 cas GPU Godot : huit passes × quatre nombres de lumières × deux
  environnements × trois brouillards × deux alpha de base. Erreur maximale par
  canal du framebuffer : .002345279, sous 1/255 ; tolérance fixée à 2/255.
- Ombre native : surface éclairée 1, surface masquée 0, témoin hors ombre 1.
- Régression de profondeur : sans correction, une surface propre située .5 mm
  devant le tar le masque. Avec correction de 1 mm, le tar opaque la recouvre ;
  un obstacle distinct situé 5 cm devant reste occultant. Le bord à alpha .5
  conserve son mélange moitié tar/moitié surface propre. Les 384 comparaisons
  GPU ont été rejouées après cette modification, sans changement des résultats.
- 114 surfaces tar des huit armures : bon shader, signe de binormale et
  restauration du canal vert présents.
- Les trois textures globales de 512×512 ont été comparées pixel par pixel après
  import : aucune différence RGBA au niveau principal. Les données brutes ont
  des tailles différentes à cause des mipmaps, sans différence des pixels.
- Pose figée, une seconde de défilement : 103 025 canaux de la région rendue ont
  changé, contre 26 avec défilement arrêté ; tarHP est resté inchangé. Le
  mouvement modifie donc le rendu indépendamment de la contamination.

Reproduire les contrôles d'équations avec `python -m unittest discover -s tests -q`.
Le script GPU s'exécute dans un projet contenant le shader avec
`await load("res://validation/tar_shader_gpu.gd").run(root, variant)` pour chaque
variant 0..7. Enregistrer les échantillons et les comparer avec
`python tools/validation/check_tar_gpu.py <samples.json>`.

## Limites de fidélité au jeu

Les équations sont reproduites et les ressources principales identifiées.
Une image fixe du jeu montre bien un tar sombre, brillant et parcouru de
reflets étirés, mais elle ne permet pas de retrouver le temps de phase, les
uniforms de sa frame, la courbe PV, tous les sélecteurs matériau, les états de
sampler et le post-traitement original. Le Lab utilise ses propres lumières et
certains paramètres de prévisualisation explicites. Il ne constitue donc pas
une validation pixel à pixel du jeu, ni une preuve de la vitesse exacte observée
dans une séquence animée originale.
