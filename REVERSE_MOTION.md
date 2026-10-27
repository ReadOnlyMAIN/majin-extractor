# Motion HexaEngine → glTF / Godot 4

État vérifié au **27 octobre 2026**. Convention : *Evidence over guessing*.
Les expériences antérieures au programme TRS compact restent dans l'historique
Git et les sondes de recherche ; elles ne spécifient plus le mapping actif.

## Avancement

| Étape | État |
| --- | --- |
| Structures ressource / squelette / tables de blocs | Vérifiées sur 193 packages |
| Stockage des courbes et temps | 4 436 blocs décodés, 20 placeholders |
| Mapping générique des transformations | Programme TRS compact vérifié sur tous les blocs décodables |
| Rotations | Euler locaux absolus, radians, Rz × Ry × Rx ; 43 ancres DDM discriminantes |
| Index des noms | Cinq tables vérifiées sur 157 séquences ; 3 201 durées concordantes, 5 états composites non reconstruits |
| IK | Graphes déclarés vérifiés sur 193 rigs ; solveur géométrique avec résidus et bornages déclarés |
| Export GLB / VS Code | `--animations all`, sélection par nom/motif/slot, `--ik bake|off` |
| Godot | Import et évaluation réels, validation automatisée reproductible |
| Fidélité complète à tous les états et modèles | Encore ouverte : contrôleurs secondaires, qstm, variantes géométriques |

Les preuves détaillées et les corrections des anciennes hypothèses sont dans
[research/TRANSFORM_LAYOUT_FINDINGS.md](research/TRANSFORM_LAYOUT_FINDINGS.md).
Le point de reprise est [CURRENT_WORK.md](CURRENT_WORK.md).

## Fichiers actifs

| Module | Responsabilité |
| --- | --- |
| `tools/conversion/motion/resource.py` | En-tête commun et découverte des ressources par chemin |
| `sequence.py` | Records nommés, cinq tables d'index et qstm brut |
| `package.py` | Slots, squelette, courbes, index de slots, footer brut |
| `curves.py` | Stockage scalaire et bitstream, sans affectation des os |
| `layout.py` | Programme générique T/R/S à largeur variable |
| `transforms.py` | Interpolation scalaire et calcul quaternion |
| `ik.py` | Graphes de chaînes déclarées, FK et solveur à deux segments |
| `pose.py` | Évaluation du programme et calcul des pistes glTF |
| `export.py` | Sélection, noms par index, alias et diagnostics |
| `tools/validation/check_godot_animation.py` | Import Godot dans un projet temporaire, poses et cibles |

Les fonctions de base globale et de carry encore présentes dans `curves.py`
sont des expériences historiques conservées pour reproduire leur réfutation.
L'export actif n'en dépend plus. Les prototypes de `research/legacy/` ne sont
pas des points d'entrée supportés.

## Ressources et squelette

Par convention de chemins :

```text
KB/chara/<nom>/<nom> ou KB/gimmick/<nom>/<nom>
KB/motionSequence/<nom>/<nom>
KB/motionPackage/<nom>/BigEndian/<nom>
```

En-tête commun : magic BE à +0, version 2 à +4, FILETIME à +0x28, corps à +0x80.
Sequence magic = `psmr`. Le package commence par un nombre de slots puis
`slot_count+1` offsets u32, relatifs à +0x80 ; le dernier termine le fichier.
Les offsets dupliqués décrivent des slots vides, conservés dans l'index logique.

Squelette : `[u8 N][u8 chain_count]`, N−1 couples parent/flag, N ids, alignement
sur quatre octets, puis N enregistrements `[T:3*f32][R:4*f32]`. Les ids et les
translations suivent l'ordre de transformation DDM. Les quaternions de référence
sont globaux ; les rotations locales DDM restent l'oracle pour le repos lors de
l'export, notamment pour les os auxiliaires dont les références diffèrent.

## Courbes et programme de transformations

Chaque bloc commence par deux u16 BE : nombre de descripteurs, nombre
échantillons. Huit codes 3-bit occupent un mot 24-bit BE, lus du triplet de
poids faible vers le poids fort. Le padding de descripteurs est nul.

- Modes scalaires 0/2/3/4 : zéro, +π/2, π, −π/2, sans payload.
- Mode 5 : constante f32.
- Mode 6 : clés f32, interpolation linéaire.
- Mode 7 : paires valeur/tangente f32 ; Hermite, tangente par seconde.
- Le code **1** est un **groupe de transformation implicite**, pas un scalaire.

Les comptes et temps des clés intérieures précèdent les valeurs ; u8 jusqu'à
256 échantillons, u16 ensuite. Temps strictement croissants entre zéro et la
dernière frame ; les extrémités sont implicites dans la table des temps.
Consommation exacte du payload, valeurs finies et bornes contrôlées.

```text
2 contrôles scalaires initiaux
racine : groupe T, groupe R
pour chaque autre os dans l'ordre du squelette : groupe T, groupe R, groupe S

groupe = un code 1                         → référence (S = unité)
       = trois codes scalaires différents de 1 → trois composantes explicites
```

Un groupe qui devient implicite retire deux descripteurs : les variations
253/251/249/247 ne sont pas des insertions anatomiques ni des bases par section.
Les rotations explicites remplacent la rotation locale, sans multiplication
par le bind. Matrice sur vecteurs colonnes : `Rz(z) Ry(y) Rx(x)`, angles radians.
Le contrôle de heading initial n'est pas multiplié à la rotation racine.

Horloge mesurée : **30 Hz**, notamment sur les dérivées mode 7 des grands rigs.
L'export évalue chaque frame source avec son interpolation, puis écrit des clés
glTF LINEAR (slerp pour les quaternions). Cela conserve les poses à ces frames ;
la courbe continue entre frames est une approximation déclarée. Les groupes
implicites sont écrits pour restaurer le repos lors des changements de clip.

## Noms, slots et qstm

Cinq compteurs u32 à sequence +0x80 ; cinq tables u32 à +0x94. Chaque entrée
non `0xFFFFFFFF` désigne un record nommé ; leur parcours donne 0..N−1 exactement.
L'indice de table désigne le **slot physique du package, squelette compris**.
Les tables supplémentaires peuvent fournir des alias du même segment.

Un nom est affecté lorsque l'index désigne un segment et que la durée qstm+0x80
égale `sample_count−1`. Ce second contrôle ne remplace pas l'index. Les segments
sans nom sont conservés sous `segment_NNN`, où NNN est l'indice de courbe logique
zéro-based (slot du package moins un). Plusieurs noms peuvent partager les mêmes
pistes. Les états vides, composites, de durée incompatible ou hors de ce package
restent dans les diagnostics, sans courbe inventée.

Les corps qstm sont conservés jusqu'au début du record suivant. L'ancien
parseur incluait à tort son mot de type, d'où certaines fausses interprétations
du footer qstm. Le bytecode d'événements et de composition reste à décoder.
L'index de courbes du package contient offsets et mots bruts par slot ; ses
anciennes interprétations en timeline/grille de huit frames sont invalidées.

## IK et contraintes

Chaîne déclarée : parent flag 3, enfant flag 4, effecteur avec `flags & 8`.
La détection retrouve exactement le compteur dans les 193 packages ; aucun
nom d'os, personnage ou offset de canal n'est nécessaire.

Les T des effecteurs sont des cibles en espace modèle. Le solveur conserve les
translations locales de longueur, évalue le FK, puis ajuste les rotations du
premier et du deuxième segment pour atteindre la cible. Il conserve le plan de
flexion et le twist du FK ; en alignement parfait il utilise un axe du repère FK.
Les R locales des effecteurs restent celles du programme. Les cibles hors de
portée sont bornées et leurs erreurs enregistrées. Les cas d'échelle ancêtre
non uniforme ou négative sont refusés par le solveur plutôt qu'approximés.

`--ik bake` produit des pistes glTF ordinaires : aucun addon ni solveur propre à
une version de Godot n'est requis. `--ik off` permet la comparaison FK.

**Limite de preuve** : atteindre une cible ne démontre pas l'identité des
règles pole/twist/stretch avec HexaEngine. Les records de contrôleurs du footer
restent bruts et sont signalés ; ils peuvent influencer les os auxiliaires de
peau. La fidélité intégrale des déformations n'est pas encore certifiée.

## Export et vérification

```bash
python3 tools/conversion/ddm_to_3d.py CHEMIN_DDM output/decoded \
  --animations all --ik bake --scale 0.01 --final
python3 tools/conversion/export_folder_to_godot.py CHEMIN_DOSSIER_KB
python3 tools/validation/check_godot_animation.py CHEMIN_GLB --godot CHEMIN_GODOT
```

Dans VS Code : **DDM: export model with animations** ou **Godot: export selected
folder**. Les noms/motifs et le choix IK sont éditables. Le rapport
`<nom>.glb.import` désactive les optimisations qui déplaçaient les effecteurs
pendant l'import Godot ; le conserver avec le GLB. Les autres réglages existants
sont préservés. `<nom>.motion.json` est conservé même avec `--final`, afin d'exposer les sources,
les slots/noms, les résidus IK, les états manquants et les contrôleurs non résolus.

Passage complet : 4 436 blocs, 770 770 résolutions IK, 7 113 bornages déclarés ;
erreur maximale aux cibles atteignables 1,02e-12 unité source. Tests : 154 exécutés, aucun ignoré. Imports éditeur Godot 4.7.2 : 738 animations sur quatre rigs.

Sondes : `transform_layout_probe.py` et `pose_program_probe.py` (option
`--all-blocks`). Tests : `python3 -m unittest discover -s tests`.
Le vérificateur Godot importe le GLB dans un projet temporaire avec l'importeur
normal, puis contrôle chaque animation au début, au milieu et à la fin : poses
finies, noms présents et cibles IK cohérentes avec les bornages enregistrés.

## Suite prioritaire

1. Identifier la sémantique des contrôleurs secondaires et comparer leurs effets
   sur les déformations ; garder les records bruts jusqu'à preuve.
2. Comparer poles, twist, orientations et stretch à une référence du jeu.
3. Reconstruire les états sans payload et composites via le bytecode qstm.
4. Étendre le décodeur géométrique aux variantes encore refusées (dont gim103
   rigide) pour atteindre l'objectif d'export de n'importe quel modèle.
5. Étendre les goldens Godot et la validation visuelle sur les familles distinctes.
