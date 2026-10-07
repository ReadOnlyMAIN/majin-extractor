# Binaires de maps — étude de `map101`

Ce document décrit les ressources actuellement extraites dans
`game_files/decompressed/KB/map/map101/` et sert de point de départ pour
l'analyse des autres dossiers `KB/map/mapXXX`. Les noms n'ont pas d'extension,
mais les quatre premiers octets identifient le format réel.

Les conclusions sont classées ainsi :

- **confirmé** : structure vérifiée par les tailles, les index et les données ;
- **fortement probable** : fonction déduite du nom, de la structure et de
  comparaisons avec d'autres maps ;
- **inconnu** : champ observé mais dont la sémantique exacte n'est pas prouvée.

## Résumé du dossier

`map101` contient 21 fichiers répartis en sept familles :

| Fichier(s) | Magic | Fonction | Confiance |
| --- | --- | --- | --- |
| `map101` | `00 64 64 6d` (`\0ddm`) | géométrie visuelle principale et matériaux | confirmé |
| `map101_L0` | `\0ddm` | géométrie visuelle simplifiée / LOD distant | confirmé |
| 14 textures nommées | `00 78 65 74` (`\0xet`) | textures BC/DXT avec mipmaps | confirmé |
| `map101_hit` | `00 63 67 62` (`\0cgb`) | collision de gameplay | confirmé par le maillage, fortement probable pour l'usage exact |
| `map101_cam` | `\0cgb` | collision ou volume bloquant de caméra | confirmé par le maillage, fortement probable pour l'usage exact |
| `map101_ins` | `00 68 73 63` (`\0hsc`) | table de placement de 332 instances de végétation | confirmé |
| `map101_R0` | `00 6d 76 6e` (`\0mvn`) | réseau de navigation/connexions, région 0 | fortement probable |
| `map101.resource_f4fa1318` | `00 65 70 6d` (`\0epm`) | paramètres de scène/environnement organisés en 16 sections | structure confirmée, sémantique détaillée inconnue |

Le dossier ne contient pas seulement les éléments nécessaires au rendu. Il
regroupe également la collision, la navigation, le placement procédural ou
semi-procédural d'objets et des paramètres de scène.

## Enveloppe binaire commune

Tous les formats ci-dessus utilisent une enveloppe HexaEngine de `0x80`
octets, en big-endian :

```text
0x00  char magic[4]       // zéro initial + trois lettres minuscules
0x04  uint32 version
0x08  uint32 type/layout  // dépend du format
0x0C  uint32 flags/count  // dépend du format
0x10  ...                 // métadonnées communes encore inconnues
0x28  uint64 build_id?    // valeur récurrente, sens non confirmé
0x80  début du payload propre au format
```

Dans CGB, HSC et EPM, plusieurs offsets sont relatifs à `0x80`. Cette règle ne
doit cependant pas être appliquée aveuglément à tous les champs de tous les
formats : DDM possède ses propres tables et alignements.

## DDM : rendu principal et LOD

### `map101`

Le DDM principal fait 3 513 000 octets. Le décodeur actuel valide :

- 79 567 sommets répartis sur trois sections géométriques ;
- 38 descripteurs de sous-maillage ;
- 62 128 triangles exportés après reconstruction de la topologie ;
- 13 matériaux ;
- positions, normales, UV et couleurs de sommets ;
- plusieurs familles de rendu, dont terrain multi-texture, opaque, alpha
  scissor et alpha blend.

Le fichier contient la géométrie statique visible du niveau. Les détails de sa
structure sont consignés dans `REVERSE_DDM.md`.

### `map101_L0`

`map101_L0` est lui aussi un vrai DDM, de 1 145 566 octets. Son contenu est
nettement simplifié :

- 26 229 sommets dans une seule section ;
- quatre sous-maillages ;
- 19 445 triangles reconstruits ;
- seulement trois matériaux ;
- uniquement les textures basse résolution `si_map104_iwa3low_c`,
  `hgkb_101taile001low_c` et `hgkb_101hasira001low_c`.

La réduction géométrique, les matériaux réduits et le suffixe `low` des trois
textures confirment que `_L0` est une représentation de niveau de détail. Le
sens exact du chiffre `0` reste à comparer aux maps possédant plusieurs
variantes `L`.

## XET : textures locales

Les 14 XET ont leurs dimensions en big-endian à `0x80`, puis leur plus grand
mip à `0x88`. Les tailles correspondent aux chaînes de mipmaps BC1/DXT1 ou
BC3/DXT5 :

| Texture | Dimensions | Compression | Rôle probable |
| --- | ---: | --- | --- |
| `hgkb_101hasira001_c` | 1024×1024 | DXT1 | couleur de pilier |
| `hgkb_101hasira001_n` | 1024×1024 | DXT1 | normale de pilier |
| `hgkb_101hasira001low_c` | 256×256 | DXT1 | couleur LOD du pilier |
| `hgkb_101taile001_c` | 1024×512 | DXT1 | couleur de tuile |
| `hgkb_101taile001_n` | 1024×512 | DXT1 | normale de tuile |
| `hgkb_101taile001low_c` | 256×256 | DXT1 | couleur LOD de la tuile |
| `hgkbst1kowarewall003c` | 512×512 | DXT1 | couleur du mur cassé |
| `hgkbst1kowarewall003_n` | 512×512 | DXT1 | normale du mur cassé |
| `si_map101_gim_mon1_c` | 512×512 | DXT1 | couleur du portail/monument |
| `si_map101_gim_mon1_n` | 512×512 | DXT1 | normale correspondante |
| `si_map101_syokubutu2_c` | 512×512 | DXT5 | végétation avec alpha |
| `si_map104_isidadami_c` | 512×512 | DXT1 | couleur de pavés partagée |
| `si_map104_isidadami_n` | 512×512 | DXT1 | normale correspondante |
| `si_map104_iwa3low_c` | 256×256 | DXT1 | roche/terrain de LOD |

Le suffixe `_c` désigne généralement la couleur, `_n` une normale et `low_c`
une couleur de LOD. D'autres textures référencées par le DDM ne sont pas
dupliquées ici : elles sont résolues dans les banques partagées, notamment
`KB/texture/common/area1/`.

## CGB : maillages de collision

Les deux CGB sont des maillages triangulés complets, distincts du rendu. Leur
payload commence par :

```text
0x80  uint32 vertex_count
0x84  uint32 vertex_offset_relative_to_0x80   // 0x30 ici
0x88  uint32 index_count
0x8C  uint32 index_offset_relative_to_0x80
...
```

Chaque sommet occupe 16 octets (`float32 position[3]`, puis zéro) et chaque
index est un `uint32` big-endian. Tous les index observés sont valides et le
nombre d'index est divisible par trois.

| Fichier | Sommets | Index | Triangles | Plage des index | BBox XYZ |
| --- | ---: | ---: | ---: | --- | --- |
| `map101_cam` | 2 831 | 12 588 | 4 196 | `0..2830` | `(-100.004,-15.351,98.968)` à `(-8.807,14.127,177.361)` |
| `map101_hit` | 4 155 | 19 260 | 6 420 | `0..4154` | `(-100.000,-15.351,98.968)` à `(-9.375,14.127,177.476)` |

Les deux boîtes englobantes couvrent presque exactement la même zone, mais le
maillage `_hit` est plus détaillé. Dans `_hit`, deux champs supplémentaires du
header valent 6 420, exactement le nombre de triangles. Les régions suivant
les index contiennent des données par triangle puis une structure spatiale
compacte, très probablement l'accélérateur utilisé pour les requêtes de
collision. Leur décodage détaillé reste à faire.

L'association `_hit` = collision physique/gameplay et `_cam` = collision de
caméra est cohérente avec les noms, les maillages et la différence de densité.
Le format géométrique est confirmé ; les règles exactes de filtrage des deux
familles ne le sont pas encore.

## HSC `_ins` : placements d'instances

`map101_ins` n'est pas un fichier audio malgré une ancienne classification
générique de la signature HSC. C'est une table typée :

```text
0x80  uint32 column_count = 17
0x84  uint32 row_count    = 333
0x88  uint32 cell_offsets[17 * 333]
```

Les offsets de cellule sont relatifs à `0x80`. La première ligne contient les
noms de colonnes, les 332 suivantes les instances :

```text
#TransX, TransY, TransZ,
RotateX, RotateY, RotateZ,
ScaleX, ScaleY, ScaleZ,
InsMagnitude, InsHeight, InsSpeed,
isPerVertex, ModelName, GroupID,
isCullByDistance, cullByDistance
```

La matrice de pointeurs explique pourquoi les chaînes et les valeurs de taille
variable ne doivent pas être décodées avec un stride fixe.

Répartition des 332 instances :

| Modèle | Nombre | Ressource résolue |
| --- | ---: | --- |
| `ins107` | 40 | `KB/instance/ins107/ins107` |
| `ins108` | 62 | `KB/instance/ins108/ins108` |
| `ins109` | 66 | `KB/instance/ins109/ins109` |
| `ins110` | 76 | `KB/instance/ins110/ins110` |
| `ins111` | 88 | `KB/instance/ins111/ins111` |

Ces cinq ressources sont des DDM très petits qui référencent les textures
`si_map101_syokubutu*`; il s'agit donc de cartes ou touffes de végétation
instanciées. Les 332 lignes ont `isPerVertex=1` et `isCullByDistance=1`.
266 placements utilisent une distance de culling de 7 000 unités source ; les
66 placements de `ins109` utilisent 10 000 unités. Les `GroupID` varient et
doivent probablement permettre l'activation ou le culling groupé.

Pour une reconstruction Godot fidèle, `_ins` devra devenir un ou plusieurs
`MultiMeshInstance3D`, avec les transforms converties dans le même repère et à
la même échelle (`0.01`) que le DDM de la map.

## MVN `_R0` : navigation et connexions

`map101_R0` est un petit fichier MVN de 6 780 octets. Ce n'est pas un DDM et il
ne contient pas la géométrie visuelle de la map. Les éléments observés sont :

- une table de 64 vecteurs/points ;
- une table de 65 éléments ;
- 65 enregistrements de 44 octets contenant des coordonnées, un scalaire, un
  vecteur normalisé, un identifiant/flag et six liens `uint16` ;
- `0xFFFF` comme sentinelle pour les connexions absentes ;
- une table secondaire de 58 associations ;
- les chaînes `lambert1` et `TSUNAGI` en fin de fichier.

`TSUNAGI` signifie « connexion/jonction » en japonais. Sur d'autres maps, les
MVN `_R0` contiennent aussi des noms comme `scr_103_gate`, `scr_105_LIFT_1` ou
`scr_107_IWA`, c'est-à-dire des obstacles ou passages dynamiques. La présence
de normales, d'adjacences et de sentinelles, combinée à ces noms, rend très
probable l'interprétation suivante : MVN décrit des régions de navigation ou
de traversée et leurs connexions, tandis que `R0`, `R1`, etc. séparent des
régions/couches de la map.

Ce format ne doit pas être exporté comme décor visible. Une future conversion
pourrait produire un `NavigationMesh`, mais seulement après validation de
l'ordre des sommets, du sens des liens et des obstacles scriptés.

## EPM `.resource_f4fa1318` : paramètres de scène

Ce fichier fait 976 octets. Le header annonce 16 types de sections et leur
répertoire commence à `0x90`. Chaque entrée est une paire `(count, offset)` et
les offsets sont relatifs à `0x80`. Les comptes observés pour Map101 sont :

```text
[2, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0]
```

Les bornes déduites des offsets concordent exactement avec la taille du
fichier. Les enregistrements contiennent notamment :

- des couleurs RGBA flottantes, par exemple `(0.8, 1.0, 0.71, 1.0)` et
  `(0.5, 0.7, 1.0, 1.0)` ;
- des positions dans le même ordre de grandeur que les coordonnées de map ;
- des rotations/quaternions et plusieurs distances ou intensités ;
- des couleurs compactes RGBA8 comme `DC DC DC FF` et `40 40 78 01`.

Le suffixe constant `.resource_f4fa1318`, la structure par catégories et ces
valeurs indiquent un paquet de paramètres de scène : éclairage, ambiance,
distances, couleurs ou volumes. On ne peut pas encore attribuer un nom sûr à
chacune des 16 sections. Elles ne doivent donc pas être connectées directement
à des propriétés Godot avant comparaison croisée avec plusieurs maps et, si
possible, avec les shaders ou le code du moteur.

## Couche gameplay : `KB/data/map/mapXXXX`

La géométrie et les instances visuelles de `KB/map/map101` ne constituent pas
à elles seules la map jouable. Une seconde arborescence contient les objets
avec logique, leurs transforms et les déclencheurs associés :

```text
KB/data/map/map0101/
  effect
  event
  gimmick
  placer
  resource
  RetryPoint
```

Les fichiers `effect`, `event`, `gimmick`, `placer` et `RetryPoint` portent le
magic `\0lpt`. Les enregistrements observés contiennent au minimum :

- une position XYZ écrite sous forme de chaîne décimale ;
- un quaternion XYZW, également écrit sous forme de chaîne ;
- un identifiant de ressource ou de type ;
- des identifiants d'instance, flags et paramètres propres à l'objet.

Les positions emploient les mêmes unités source que les autres données de map
et doivent donc être multipliées par `0.01` pour le projet Godot. Le découpage
complet des champs LPT reste à formaliser, mais les transforms et plusieurs
références sont déjà directement vérifiables.

Rôle observé ou probable de chaque table :

| Fichier | Rôle | Confiance |
| --- | --- | --- |
| `placer` | placement des objets de gameplay et de leurs paramètres | confirmé pour les transforms et références principales |
| `gimmick` | mécanismes interactifs propres à la map | fortement probable |
| `event` | déclencheurs et volumes/points d'événements | fortement probable |
| `effect` | placements et paramètres d'effets | fortement probable |
| `RetryPoint` | positions et orientations de réapparition | fortement probable |
| `resource` | manifeste des ressources `gim`, `enm` et `instance` requises | confirmé |

### Arbre de soin/sauvegarde et totems de `map101`

Le modèle `KB/gimmick/gim102/gim102` est l'arbre brillant. Son DDM référence
notamment `ct_savetree_eda_c` et `ct_savetree_happa_c`. Son script appelle le
système de jeu, et `map0101/placer` contient le placement suivant :

```text
ressource       gim102
position source (-1662, -537, 16051)
position Godot  (-16.62, -5.37, 160.51)
quaternion      (0, 0, 0, 1)
```

Le modèle `gim650` correspond aux totems de checkpoint : ses scripts appellent
explicitement `setRetryPoint`. Deux placements `gim650` sont présents dans
`map0101/placer` :

```text
position source (-6890.775879, -748.649597, 16925.109375)
position Godot  (-68.907759, -7.486496, 169.251094)
quaternion      (0, 0.901221, 0, 0.433360)

position source (-2192.416748, -429.767944, 11663.518555)
position Godot  (-21.924167, -4.297679, 116.635186)
quaternion      (0, 0.249179, 0, 0.968457)
```

Les quatre transforms de `map0101/RetryPoint` forment deux groupes proches de
ces totems. Elles semblent représenter les positions exactes où replacer le
joueur selon le checkpoint ou le sens d'entrée dans la zone ; cette relation
spatiale est confirmée, mais la règle de sélection reste à décoder.

Le manifeste HSC `map0101/resource` référence les gimmicks `102`, `610`,
`650`, `659`, `673`, `118`, `119` et `656`, ainsi que les instances visuelles
`107`, `108`, `109`, `110` et `111`.

### Ennemis et combats de `map102`

Le manifeste `KB/data/map/map0102/resource` définit trois ensembles ennemis :

```text
enm0 : 300
enm1 : 300, 370
enm2 : 300, 370
```

Les identifiants correspondent aux ressources `KB/chara/chr300` et
`KB/chara/chr370`, nommées respectivement `ENEMY_300_ZAKO_A` et
`ENEMY_370_NIGHT_A` dans la base de données des personnages.

Les fichiers suivants complètent cette déclaration :

| Fichier | Magic | Rôle probable |
| --- | --- | --- |
| `KB/map/map102/map102_battle` | `\0dab` | configuration de combat de base |
| `map102_battle_1` | `\0dab` | placements/configuration de la variante `enm1` |
| `map102_battle_2` | `\0dab` | placements/configuration de la variante `enm2` |
| `map102_path` | `\0pvn` | chemins ou points de déplacement liés aux combats |
| `map102_R0..R3` | `\0mvn` | régions et connexions de navigation |

Les relations entre les variantes DAB et les lignes `enm0..enm2` sont très
probables grâce aux noms et aux références croisées, mais le format interne
DAB n'est pas encore suffisamment décodé pour exporter les spawns ennemis.

## Relations entre les ressources

```text
map101 (DDM principal)
  ├─ textures locales XET
  └─ textures partagées KB/texture/common/area1

map101_L0 (DDM de LOD)
  └─ trois XET low_c locaux

map101_ins (HSC)
  └─ 332 placements de KB/instance/ins107..ins111 (DDM de végétation)

map101_hit / map101_cam (CGB)
  └─ maillages de collision indépendants du rendu

map101_R0 (MVN)
  └─ régions et connexions de navigation probables

map101.resource_f4fa1318 (EPM)
  └─ paramètres de scène classés en 16 sections

KB/data/map/map0101 (LPT + HSC)
  ├─ placements des objets de gameplay et gimmicks
  ├─ événements, effets et points de réapparition
  └─ manifeste vers KB/gimmick, KB/chara et KB/instance

map102_battle* / map102_path (DAB + PVN)
  └─ combats, spawns ennemis et chemins associés probables
```

## Priorités de recherche

1. Écrire un lecteur CGB minimal et visualiser `_hit` et `_cam` superposés au
   DDM pour confirmer leurs usages et le changement de repère.
2. Convertir HSC `_ins` en données structurées, puis instancier `ins107..111`
   avec un `MultiMesh` plutôt que de fusionner 332 copies de géométrie.
3. Visualiser les points, normales et liens MVN afin de confirmer la topologie
   de navigation avant toute génération de `NavigationMesh`.
4. Comparer les 16 sections EPM sur plusieurs maps, en faisant varier une
   catégorie à la fois, pour nommer les paramètres sans spéculation.
5. Vérifier les relations entre `L0`, les distances de culling HSC et les
   éventuelles transitions de LOD observées dans le jeu.
6. Formaliser le lecteur LPT et exporter `placer`, `gimmick`, `event`,
   `effect` et `RetryPoint` vers des ressources ou scènes Godot.
7. Décoder DAB et PVN pour reconstruire les groupes d'ennemis, leurs points de
   spawn et leurs chemins de déplacement.
