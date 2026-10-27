# Reprise — animations génériques et IK (2026-10-27)

Objectif : exporter les modèles et leurs animations pour Godot 4 depuis VS Code,
sans règles par personnage. Convention : **Evidence over guessing**.

## Découverte qui débloque le mapping

Le flux ne contient pas sept scalaires fixes par os. Après deux contrôles
initiaux, il décrit T/R pour la racine, puis T/R/S pour chaque autre os,
**dans l'ordre du squelette**. Un groupe commence par `1` quand il est implicite
(référence), sinon il contient trois descripteurs scalaires. Les variations de
largeur expliquent les anciens décalages et les supposées permutations FK/IK.

- **193 packages**, **4 436 blocs** : grammaire consommée exactement.
- **20 placeholders** sans animation, aucune autre erreur de stockage.
- **41 015 composantes de translation non nulles** retrouvent leur référence
  au bon indice d'os (occurrences, pas valeurs indépendantes).
- Rotations explicites : Euler **en radians**, matrice `Rz × Ry × Rx`,
  rotations locales absolues. **43 ancres indépendantes non commutatives**
  reproduisent le quaternion local DDM à <1e-4 rad ; chacune rejette les onze
  autres combinaisons ordre/unité à >0,01 rad.
- Descripteur scalaire `0` = zéro ; `2/3/4` = +π/2, +π, −π/2.
  `1` n'est ni un scalaire nul, ni une tenue de la valeur précédente.
- Le squelette commence par deux octets, puis **N−1** couples parent/flags.
  L'ancien parseur perdait le parent/flag du premier enfant.
- Chaînes IK : parent avec flag `3`, enfant `4`, effecteur `flag & 8`.
  Le nombre de chaînes retrouve l'en-tête dans les **193 packages**.

Preuves et limites : [TRANSFORM_LAYOUT_FINDINGS.md](research/TRANSFORM_LAYOUT_FINDINGS.md).

## Noms et états

`motionSequence` contient cinq compteurs à +0x80 et cinq tables u32 à +0x94.
Leur taille borne exactement les records dans **157 fichiers**. Les entrées non
vides référencent chaque record une fois, dans l'ordre. L'indice est le **slot du
package, squelette inclus**, donc une entrée de plus que l'indice des courbes.
Les cinq tables permettent aussi plusieurs noms pour le même segment.

Sur les packages appariés, **3 201 associations** retrouvent la durée du segment ;
**5** ne sont pas des états à segment unique (gim238, chr321, chr500). Les slots
vides et les états hors du package restent signalés ; aucune pose n'est fabriquée
pour compléter ces états. Le découpage qstm a été corrigé : le dernier mot du
record suivant n'appartient pas à sa queue.

## Implémentation actuelle

- `motion/layout.py` : programme de groupes TRS, consommation stricte.
- `motion/transforms.py` : constantes, interpolation linéaire/Hermite et quaternions.
- `motion/ik.py` : chaînes déclarées et solveur géométrique à deux segments.
- `motion/pose.py` : transformations locales absolues, restauration des groupes
  implicites, échantillonnage à 30 Hz, IK calculée dans les pistes glTF.
- `motion/export.py` : noms par index + durée ; segments auxiliaires sous
  `segment_NNN` ; alias préservés ; diagnostic explicite des états restants.
- `ddm/cli.py`, export dossier et VS Code : `--animations all|none|motifs`,
  `--ik bake|off`, échelle par défaut 0,01. Aucune règle chr30x dans le décodeur.
- `<modèle>.motion.json` reste produit avec `--final` : source, mapping,
  noms, erreurs IK, cibles inatteignables, contrôleurs bruts et états manquants.

L'IK conserve le plan de flexion et le twist du FK. Les positions des effecteurs
sont lues en espace modèle ; leurs translations locales gardent les longueurs
squelettiques. Les rotations locales des effecteurs restent celles du programme.
Les cibles inatteignables sont bornées et leur résidu est enregistré.

## Vérifications rejouables

```bash
python3 -m unittest discover -s tests
python3 research/transform_layout_probe.py --json output/research/transform-layout.json
python3 research/pose_program_probe.py --json output/research/pose-program.json
python3 tools/conversion/ddm_to_3d.py CHEMIN_DDM output/decoded \
  --animations all --ik bake --scale 0.01 --final
python3 tools/validation/check_godot_animation.py CHEMIN_GLB --godot CHEMIN_GODOT
```

La sonde de poses exerce le premier bloc de chaque package : **172 blocs**,
**13 948 échantillons IK**, 8 bornages déclarés, erreur maximale sur les extrémités
atteignables **5,72e-13 unité source**. `--all-blocks` étend la sonde au corpus
entier. Les contrôles Godot utilisent un projet temporaire et l'importeur réel,
puis vérifient poses finies et cibles IK au début/milieu/fin de chaque animation.

Exports locaux : chr301 **140 segments**, dont 110 nommés et 10 états sans
segment autonome ; chr540 **83 segments** ; chr100 **477 animations avec alias**
pour 367 blocs physiques ; chr370 **38 segments**.

## Validation complète et projet Godot utilisateur

Le passage **intégral** de `pose_program_probe.py --all-blocks` est terminé :
193 packages, **4 436 blocs calculés**, **770 770 échantillons IK**, 7 113 cibles
hors de portée explicitement bornées, 20 placeholders. Aucune autre erreur.
Erreur maximale à la cible atteignable : **1,02e-12 unité source**.
Les tests Python comptent **154 tests exécutés, aucun ignoré**.

Import réel Godot **4.7.2**, après réglages de fidélité :

| Rig | Os | Animations | Poses contrôlées | Erreur max IK sans bornage |
| --- | ---: | ---: | ---: | ---: |
| chr100 | 146 | 477 | 1 428 | 1,93 µm |
| chr301 | 81 | 140 | 418 | 0,99 µm |
| chr370 | 58 | 38 | 114 | 0,96 µm |
| chr540 | 207 | 83 | 249 | 7,35 µm |

L'optimiseur d'import Godot déplaçait des effecteurs jusqu'à 1,5 cm sur les
échantillons testés. `motion/godot.py` produit/actualise `<modèle>.glb.import`
pour désactiver simplification, compression et suppression des pistes de repos.
Il conserve UID, script de matériaux et autres paramètres, y compris les valeurs
Variant natives des sous-ressources. **Copier ce fichier avec le GLB**.

Projet indiqué par l'utilisateur : `/mnt/ssd990pro/Projects/Godot/majin-godot`.
La configuration VS Code propose ce projet comme destination ; le convertisseur
reste générique avec `--godot-project CHEMIN` (ou `--output-root CHEMIN`).
Les imports préexistants de chr200/chr301 contiennent des changements utilisateur.
Les nouveaux essais sont donc dans :

`original_files/decoded/animation_validation_20261027/preview_chr301.tscn`

Cette scène a été exécutée dans le projet utilisateur avec **Godot 4.8-dev7**.
L'export exact déposé a aussi passé l'import éditeur **4.8-dev7** : 140 animations,
418 poses, erreur maximale des cibles sans bornage 0,99 µm.
Elle permet sélection d'animation, pause, scrubbing, orbite et zoom. Elle charge le
GLB via GLTFDocument pour fonctionner avant l'import éditeur. Les images
repos/combat/rotation ont été inspectées, et une vidéo de déplacement est dans
`output/research/chr301-walk.mp4` ; les données GLB avec leurs réglages
ont aussi passé séparément l'importeur normal. Les fichiers utilisateur existants
n'ont pas été remplacés.

## Ce qui reste réellement ouvert

1. **Contrôleurs secondaires du rig**, records du footer : conservés et signalés,
   pas encore interprétés. Ils pilotent notamment des os auxiliaires de membres.
   La fidélité de toutes les déformations ne peut pas encore être certifiée.
2. **Politique IK exacte du moteur** : les contraintes d'extrémité sont vérifiées,
   mais les règles de pole/twist, stretch et limites ne sont pas encore comparées
   à une capture ou à l'exécutable du jeu. Le solveur est une reconstruction
   géométrique explicitement nommée `two_bone_fk_plane`.
3. **États composites/poses sans payload** : décoder les instructions qstm pour
   reconstruire tous les états nommés, leurs transitions et leurs événements.
4. **Géométries encore non prises en charge**, notamment le DDM rigide de gim103 :
   ses courbes sont décodées, mais sa géométrie échoue encore dans le convertisseur.
   Le succès sur tous les modèles n'est donc pas acquis.
5. Comparaison visuelle de plusieurs familles et mouvements, puis extension des
   tests d'import Godot aux cas de scale et aux modèles sans ressources motion.

Les anciennes hypothèses de base globale, de quaternions scalaires, de degrés,
d'ancres par offsets et de carry implicite sont **supplantées** par cette lecture.
Les sondes antérieures restent disponibles pour retracer les erreurs, pas comme
spécification de l'export actuel.
