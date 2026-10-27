# Utilisation originale de chr100 et chr101

Analyse des tables locales et du code PPU du build examiné. Les observations
visuelles initiales (« propre » et « avec tar ») décrivent leur contenu, mais ne
suffisent pas à expliquer leur sélection par le jeu.

## Conclusion étayée

La famille chr100/110/115/… est celle référencée par les objets de costume du
personnage jouable. Chaque modèle contient sa base et ses propres surfaces de
tar. La famille chr101/111/116/… est utilisée pour les acteurs de cinématiques,
avec remplacement selon le costume courant. Il ne s'agit donc pas simplement
de deux couches à assembler, ni d'un couple dont la sélection selon les PV
serait établie. D'autres usages de la famille cinématique restent possibles.

| Objet de costume | Modèle jouable | Modèle sélectionné pour les cinématiques |
| --- | --- | --- |
| 0 | chr100 | chr101 |
| 1 | chr110 | chr111 |
| 2 | chr115 | chr116 |
| 3 | chr120 | chr121 |
| 4 | chr125 | chr126 |
| 5 | chr130 | chr131 |
| 6 | chr135 | chr136 |
| 7 | chr140 | chr141 |
| 8 | chr150 | chr151 |
| 9 | chr155 | chr156 |
| 10 | chr145 | chr146 |

Les trois dernières lignes ne sont pas une extrapolation arithmétique : l'ordre
est décodé séparément dans les données d'objets et dans le switch PPU.
La classification d'usage n'établit pas à elle seule que tous leurs matériaux
sont déjà correctement interprétés par l'exporteur.

## Données de gameplay

`KB/data/data_base/PathCharaModel` est un conteneur MHCP à 216 allocations,
organisé en trois colonnes de 72 chemins. La première commence par chr100,
chr110, chr115, chr120, chr125, chr130, chr135, chr140, chr145, chr150, chr155.
Les colonnes suivantes référencent chr100 pour ces onze variantes ; leurs
fonctions consommatrices chargent des ressources partagées. Le rôle exact de
chaque ressource partagée n'est pas renommé arbitrairement ici.

Le lookup `0x53aa58` lit le chemin à l'indice du modèle. Le lookup
`0x53a604` lit un enregistrement de 52 octets de `LinkItemData`.
Les objets 0..10 ont la catégorie 0 à +0x14 et l'indice de modèle à +0x04.
Leurs indices sont 0,1,2,3,4,5,6,7,9,10,8 : cela explique l'ordre final du tableau.
Les chemins ainsi obtenus appartiennent tous à la famille jouable, sans chr101.
Le chemin d'initialisation `0x1790f8` lit l'objet équipé, puis sa liaison de
modèle (`0x179138`, `0x179140`) et son chemin (`0x179158`).

## Données et sélection de cinématiques

Le corpus extrait contient **56 fichiers** sous `KB/demo/` qui référencent
explicitement `KB/chara/chr101/chr101`. Aucun de ces chemins de personnage
chr1xx ne désigne chr100 dans ce corpus. Ce comptage concerne les références
littérales dans les fichiers, pas une capture de toutes les scènes exécutées.

Le reconnaisseur `0x145e24` compare le chemin à une table de onze modèles
commençant à `0xe01644` : chr101,111,116,121,126,131,136,141,146,151,156.
Il est appelé dans deux chemins de substitution :

- À `0x44ae6c`, le chargement reconnaît cette famille et appelle le sélecteur
  `0x1586c` à `0x44ae98`, puis charge la ressource retournée.
- À `0x41ecec`, un autre chargement reconnaît la famille, appelle `0x37858`
  à `0x41ed14`, puis résout le modèle correspondant.

Le switch `0x1586c` lit l'indice de costume dans la structure de contexte à
+0xc0 ; il ne lit pas les PV. Les onze destinations de sa table à `0x158a4`
produisent les chemins cinématiques du tableau ci-dessus. Le fallback est
chr101. Le sélecteur `0x37858` reprend les mêmes chemins et en extrait le numéro
à trois chiffres. Il existe également une comparaison explicite avec chr101
à `0x2fe254` et `0x303f1c` dans le traitement des acteurs de cette zone du code.
Le nom de type original `TaskCharacterDemo` est enregistré à `0x30ffa8`.

Aucun basculement chr101→chr100 commandé par les PV n'a été trouvé dans ces
chemins. La progression connue des PV reste une entrée du shader tarHP.
Cela ne démontre pas l'absence de tout autre branchement dans l'exécutable.

## Conséquence pour le chevauchement Godot

L'ancien assemblage utilisait la base de chr101 et le tar de chr100. Or les
modèles ont des géométries et rigs distincts (146 os pour chr100, 183 pour
chr101). Par exemple, les surfaces ordinaires du torse contiennent 708+401
vertices de matériau body dans chr100, contre 33+729+419 pour body1 dans chr101.
Ce ne sont pas deux copies identiques différant seulement par un shader.

Dans l'essai Godot sur `c100_0010_hold_climb_00`, remplacer la base par les
surfaces ordinaires de chr100 supprime les traversées de peau observées aux
bras et au cou, en gardant le tar de chr100. Le test visuel porte sur cette pose,
pas sur toutes les animations. Le biais de profondeur de 1 mm ajouté auparavant
ne résout pas cette incompatibilité de géométrie : il n'est pas une correction
originale décodée. Les états RSX exacts de profondeur restent non établis.

La reconstruction cohérente pour le personnage jouable doit conserver la base
et le tar du **même modèle jouable**, puis corriger séparément les éventuelles
erreurs de matériaux. Transférer aveuglément les matériaux de chr101 sur
chr100 n'est pas non plus validé : leurs paramètres et certaines textures
(diffuse, utilitaire, environnement) diffèrent.

Le contrôleur Godot dispose maintenant de `cinematic_render`, activé par défaut.
Il remplace le corps complet par le modèle de la famille choisie, conserve le
maître d'animation chr100 et présente corps/tête/bracelets dans le Lab. Le biais
précédent vaut désormais zéro par défaut. Les accessoires chr72x/74x sont exportés
avec leur rig statique ; les bracelets sont séparés en deux pièces miroir pour
suivre les deux avant-bras. Le placement est une reconstruction de preview,
indépendante du chemin d'attache CPU complet encore à documenter.

## Les emplacements d'équipement

Les noms internes retrouvés sont `_currentItemBody`, `_currentItemHead`,
`_currentItemBracelet`, et les tableaux `_itemBody`, `_itemHead`, `_itemBracelet`.
Les trois premiers sont enregistrés aux adresses 0xb7ba8, 0xb7c20, 0xb7c98.
Les objets 0..10 de catégorie 0 choisissent un modèle de corps entier.
Les deux groupes suivants, de catégories 1 et 2, utilisent l'indice de modèle
72 (sentinelle sans remplacement de corps) et des indices séparés dans
`PathCharaEquip` : 38..47 vers chr720..729, et 48..57 vers chr740..749 ; les
premiers objets de chaque groupe emploient la sentinelle d'équipement 58.

Cela fournit une piste fortement étayée pour tête et bracelets indépendants
du corps. Le chemin complet d'attache des accessoires reste à décoder. Le
précédent découpage des chr1xx en tête/torse/jambes, obtenu par influences d'os,
est une fonctionnalité inventée pour le Lab ; il ne doit plus être présenté
comme la reconstitution des emplacements originaux. Les jambes ne sont pas
établies comme un emplacement séparé dans ces données.

## Reproduction

```bash
python tools/research/player_model_usage.py \
  --kb-root game_files/decompressed/KB \
  --elf /tmp/majin-eboot.elf \
  --output output/research/player_model_usage.json
python -m unittest tests.test_player_model_usage -v
```

L'inspecteur vérifie le format des allocations et plusieurs instructions aux
adresses attendues. Il suit les destinations du switch et les offsets TOC
signés ; les noms ne sont pas simplement calculés avec « modèle + 1 ».
Le rapport contient le SHA-256 de l'ELF local et les fichiers de cinématiques
comptés. Aucun exécutable ni contenu d'asset n'est enregistré dans ce rapport.
