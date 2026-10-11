# REVERSE_MOTION — Décodage HexaEngine motion → Godot 4

> Statut du plan et avancement. Convention projet : *Evidence over guessing* —
> seuls les faits vérifiés sur le corpus sont décrits comme confirmés.
> Le document suit l'**ordre d'exécution du plan** : les résultats de
> recherche sont rangés dans la phase qui les a produits.

## Progress

| Phase | Tâche | Statut |
|---|---|---|
| 0 | Nettoyage / archivage de l'ancien prototype | ✅ |
| 1 | Parsers structurels (`resource.py`, `sequence.py`, `package.py`) validés corpus | ✅ |
| 2.1 | En-tête des blocs de courbes (`u16 descriptor_count, u16 sample_count` + fil 3-bit) | ✅ |
| 2.2 | Table de canaux bit-packed → mapping blocs→(bone, composant) | 🔶 en cours |
| 2.3 | Décodage des keyframes (oracle bind pose FAIT ; clés quantifiées ouvertes) | 🔶 en cours |
| 2.4 | Liaison records ↔ slots ↔ blocs + FPS | 🔶 en cours |
| 2.5 | IK : journalisation puis exclusion des canaux non-FK | ⬜ |
| 3 | Reconstruction des clips glTF (`decode_clip`) | ⬜ |
| 4 | Intégration pipeline / Godot (pont `export.py` partiellement FAIT) | 🔶 en cours |
| 5 | Tests goldens + GLB bout-en-bout (partiellement FAIT : 128 tests OK) | 🔶 en cours |
| 6 | Documentation finale (README/ROADMAP) + nettoyage legacy | ⬜ |

Légende : ✅ fait · 🔶 en cours · ⬜ à faire.

## Objectif

Décoder les données d'animation HexaEngine (`motionSequence psmr` +
`motionPackage`) des assets skinnés DDM et exporter skin + animations vers
Godot 4 via le pipeline GLB existant.

## Contexte du fichier (convention KB)

- `chara/<n>/<n>` ↔ `motionSequence/<n>/<n>` ↔
  `motionPackage/<n>/BigEndian/<n>` : liés **par convention de nom**
  (aucune référence croisée dans les binaires).
- Extension naturelle : `demo/<d>/motionPackage` (cutscenes), gems (gimXXX).

## Architecture & fichiers impliqués

### Implémentation active — `tools/conversion/motion/`

| Fichier | Rôle | Phases |
|---|---|---|
| `resource.py` | En-tête ressource commun (magic/version/FILETIME), `UnsupportedMotion`, `discover_motion_paths` | 1 |
| `sequence.py` | Parser `psmr` (records nommés, `parse_qstm`, `duration_frames_of`) | 1, 2.4 |
| `package.py` | Parser `motionPackage` (table de blocs, `parse_skeleton`, index de slots `CurveIndexBlock`, `CurveBlock` / footer) | 1, 2.1 |
| `curves.py` | Décodage des blocs de courbes (descripteurs 3-bit, `ScalarTrack`, `decode_curve_block`, oracles bind pose) — prototype de recherche | 2.x |
| `export.py` | Pont pipeline : `discover_character_motion`, `decode_character_animations` (retourne `[]` tant que les courbes ne sont pas décodées) | 4 |
| `cli.py`, `__main__.py`, `__init__.py` | CLI d'inspection `python -m tools.conversion.motion` | 1, 5 |

### Intégration pipeline (touchée)

- `tools/conversion/ddm/skinned.py` — rewire de `motion_decode` (archivé)
  vers `tools.conversion.motion.export`.
- `tools/conversion/export_folder_to_godot.py` + `.vscode/launch.json` —
  à mettre à jour en Phase 4 (tâche « DDM: export with animations »).
- `tests/test_motion_sequence_package.py`, `tests/test_ddm_to_3d.py`.

### Sondes corpus (recherche, rejouables) — `research/`

- `curve_bitstream_probe.py` + `CURVE_BITSTREAM_FINDINGS.md` (Phase 2.1/2.2),
- `curve_header_probe.py` + `CURVE_HEADER_FINDINGS.md` (Phase 2.1, son modèle
  octets A/B est corrigé ; conservé pour trace),
- `ddm_structural_scan.py`, `ddm_variant_probe.py`.

### Fichiers legacy — à supprimer à la TOUTE DERNIÈRE étape de clean (Phase 6)

`research/legacy/` contient l'implémentation précédente, conservée
uniquement pour traçabilité. À la fin du plan (Phase 6), supprimer :

- `research/legacy/motion_decode_archived.py`,
  `research/legacy/test_motion_decode_archived.py`,
- les anciennes sondes : `motion_probe.py`, `motion_semantics_probe.py`,
  `per_clip_layout_probe.py`, `fk_rotation_probe.py`,
  `ddm_multisection_probe.py`, `skin_decode_probe.py`,
- les documents périmés : `MOTION_ANALYSIS.md`, `EXTRACTION_SUMMARY.md`,
  `README_CONVERSION.md`, `asset_inventory.json`,
- et le dossier `__pycache__/`.

Ne les supprimer qu'après : décodage complet des courbes, extraction des
clés, tests GLB bout-en-bout verts et mise à jour de la documentation
(Phase 6).

## En-tête ressource commun (Phase 1 — VÉRIFIÉ)

0x80 octets : magic u32 BE (+0x00), **version=2** (+0x04, contrôle strict),
champs type (+0x08/+0x0c), **FILETIME u64 Windows** (+0x28), corps à +0x80.
u32/f32 **big-endian** ; sous-tables parfois u16/u32 LE ou octets séparés
(comme déjà vu dans le DDM).

Code : `tools/conversion/motion/resource.py` (`read_header`,
`UnsupportedMotion`, `discover_motion_paths`).

## Phase 0 — Nettoyage (FAIT)

1. Fichier accidentel à la racine supprimé (artefact d'échappement shell).
2. Ancien prototype archivé via `git mv` : `motion_decode.py`,
   `test_motion_decode.py` et les anciennes sondes research →
   `research/legacy/`.

## Phase 1 — Parsers structurels (FAIT, validés sur tout le corpus)

Paquet : `tools/conversion/motion/` (`resource.py`, `sequence.py`,
`package.py`).

### sequence.py (psmr)

- +0x80 : table creuse u32 (sémantique de bookkeeping, non décodée ; sert de
  borne de scan).
- Records contigus, **triés alphabétiquement par nom** :
  `[u32 type_id][u32 GUID 0x735C64B8][u64 FILETIME][u32 namelen+NUL]
  [nom "KB/motionSequence/<chr>/<clip>"][bloc qstm]`.
- Scanner : boucle déterministe `data.find(guid_bytes)` →
  (type_id, offset). Les noms hors convention lèvent `UnsupportedMotion`
  (dérive de format), pas de regex.
- Séquences sans records (chr900, gim001… 41/157) : **légitimes** (assets
  sans animation) → l'API retourne une liste vide, la pipeline exporte le
  skin seul.
- `parse_qstm` : magic + 4 mots + FILETIME ; champ hypothétique
  **durée en frames à qstm+121/131 volontairement NON figé** (`None`),
  cf. Phase 2.4.

### package.py (motionPackage)

- +0x80 : `[u32 block_count][block_count+1 offsets u32 rela­tifs à 0x80]`,
  `offsets[n] == filesize − 0x80` (vérifié partout).
- **Bloc 0 = squelette du motion** — DÉCODÉ ET VALIDÉ :
  `[u8 bone_count][u8 chain_count][0][0]`, puis `(bone_count−2)` paires
  `(parent_index, flags)`, puis `bone_count` ids (== ordre des transforms
  DDM, vérifié sur le corpus), padding align4, puis bind pose
  `[3×f32 trans][4×f32 quat (x,y,z,w)]` par bone — **== bind pose DDM
  exacte** (vérifié octet-près, chr100).
- `chain_count` = candidat compteur de chaînes IK (chr100=4, chr370=2,
  gim103=0); flags de bones 00/02/03/04/0x48 = marqueurs candidats.
- **Index des slots de courbes (avant-dernier bloc)** — décodé
  structurellement : `[u32 slot_count] [slot_count+1 offsets u32]`, puis
  des mots u32 bruts regroupés par ces offsets. Le premier offset vaut
  exactement `4 + 4*(slot_count+1)` et le dernier la taille du bloc : ce sont des
  **offsets internes**, pas des frames. `slot_count == block_count-3` sur
  les 173 packages qui portent cet index. Les offsets de blocs dupliqués
  expliquent pourquoi le nombre de blocs physiques non vides peut être
  inférieur au nombre de slots logiques. Index optionnel sur les petits
  placeholders.
- Dernier bloc : footer ~16 octets (`e7 19 01 … 05 ff 00`).
- Offsets dupliqués dans la table = clips partageant les mêmes données
  (variantes miroir).

### Validation corpus (rejouable)

Les motionSequence (157) + motionPackage (193) se parsent sans exception :
chr100 = 414 records / 460 slots ; gim103 = 4 records / 11 blocs.

## Base de connaissances corpus (résumé de l'analyse binaire)

- Magic u32 BE par format : `\x00ddm` (DDM), `psmr` (motionSequence),
  `\x00\x00\x00\x00` (motionPackage), `\x00crg`, `\x00CSR` — tous
  version u32 = 2 à +0x04.
- `.crg` = configuration physique du personnage (capsules, vitesses f32).
- `.resource_*` (CSR) = ressorts / physique secondaire (paires d'ids de
  bones + longueurs).
- `demo/<d>/motionPackage` existe aussi (cutscenes) — extension
  naturelle du décodeur.
- `type_id` des records : 208 partout pour chr370/gim103 ; varie pour
  chr100 (236×118, 272×52, 208×62…) — probablement un code de catégorie,
  pas un lien.
- Chr100 : 457 slots de courbes pour 460 blocs totaux ; chr300 : 150/153.

## Phase 2 — Recherche format des courbes (EN COURS)

### 2.1 — En-tête des blocs (FAIT, CORRIGÉ — research/curve_bitstream_probe.py)

- L'ancien découpage par octets A/B était faux. L'en-tête est
  `u16 descriptor_count, u16 sample_count` (BE) et le premier compteur borne
  exactement le flux initial de codes 3-bit, groupés huit par mot 24-bit,
  lus du triplet de poids faible vers le poids fort. Orientation discriminée
  par le padding nul, les temps strictement croissants et la consommation
  exacte du payload (4 379 blocs contre 172 en MSB-first). Motif `24 92 49`
  = répétition du code `001`.
- Les compteurs dépassent 255 sur les gros rigs — chr540 : 658..1112
  descripteurs, chr590 : 753..793 (207/220 bones) : l'« octet type »
  n'existait pas (octet haut de `descriptor_count`) ; l'« octet phase »
  était celui de `sample_count`.
- `sample_count - 1` appartient aux durées candidates qstm+128 pour
  chr300 **135/150**, chr540 **54/83**, chr590 **26/32** et gim103 **4/8**.
  Cela soutient une longueur échantillonnée, sans encore fournir une
  bijection record→slot.
- Sur gim103, les compteurs de descripteurs 36/40/44 sont divisibles par 4
  et les paires miroir conservent leurs motifs structurés. Sur les
  humanoïdes, les compteurs sont proches de 4×bone_count ; l'affectation
  exacte aux canaux reste à démontrer.

### 2.2 — Table de canaux bit-packed (EN COURS)

- Filtre descripteur : histogramme corpus écrasé par le code 1
  (ex. chr300 : 1×22887, 0×3322, 7×7107) — distribution asset-spécifique.
  C'est LE bitstream de canaux candidat.
- **FAIT (gim103) — layout 7 scalaires par bone** : chaque bone occupe 7
  indices scalaires consécutifs `(T.x,T.y,T.z,R.x,R.y,R.z,R.w)` ;
  `bone = (scalar + base) // 7`, `comp = (scalar + base) % 7` avec
  **base = 21** et permutation identité (ancres 21↔(6,T.x)/22/T.y/23/T.z,
  28↔(7,T.x)… vérifiées à travers TOUS les blocs ; sonde
  `channel_base_probe.py` → `CHANNEL_BASE_FINDINGS.md`). Le stride de 7
  entre bones consécutifs est attesté par les triplets de translation
  (21..23 vs 28..30).
- **INVALIDÉ** : « les sections (f32, 0) suivent immédiatement le fil de
  descripteurs » (alignement mesuré : 1/150 chr300, 0 ailleurs) → une table
  de frames quantifiées s'intercale ; désormais décodée (cf. 2.3), mais
  l'offset exact de la section de valeurs reste ouvert. Candidats restants :
  canaux indexés dans une carte globale type bloc (0x12,0x0e) de chr100.
- **Mapping humanoïdes — EN COURS** : les mappings naïfs
  `scalar_index//4` (quaternion par bone) et `//7` (T+R par bone) sont
  invalidés (0 hits sur gim103/chr300/chr370 ; chr540 b0 : 23/25 hits en
  //7, asset-spécifique). Balayage exhaustif base × permutation (7!) sur
  les ancres constantes : gim103 seule se sature (2/2, base 21) ; chr300/chr370
  plafonnent à ~12/24-29 ancres → les clips humanoïdes ne démarrent PAS sur
  la bind pose et les constantes seules ne suffisent pas à poser la base
  (oracles supplémentaires requis : mots u32 internes de l'index de slots,
  flags de bones, tangentes mode 7). Les constantes 0.0 sont exclues des
  oracles (trivialement ambigües).


### 2.3 — Keyframes (EXTRACTION FAIT corpus entier ; oracles de pose PARTIELS)

- **FAIT** : `curves.decode_curve_block` décode les 4 370+ blocs du corpus
  sans exception et avec consommation exacte du payload. Stockage scalaire
  lossless : mode 5 = constante (1 f32), mode 6 = clés intérieures datées
  (comptes/temps u8 ≤256 échantillons et u16 au-delà ; frames ∈ ]0,
  sample_count−1[, strictement croissantes), mode 7 = clés + auxiliaires
  (2×f32 par clé, paires (valeur, auxi)). Les modes 0–4 ne stockent rien
  dans le payload du bloc.
- Les valeurs de mode 5/6/7 contiennent des **composantes de bind pose
  exactes** (0.8415768… quat, 229.172 translation gim103), mais les tests
  de quartet |q|≈1 échouent sur des indices scalaires consécutifs → un
  canal = UN scalaire (une seule composante), pas un bone entier.
- Oracles restants : bijection scalar_index ↔ (bone, composant).

- Les valeurs « constantes » des blocs de courbes = **valeurs de pose des
  canaux figés**, vérifiées contre la bind pose du bloc squelette :
  `curves.bind_pose_hits()` → chr100 **87/145**, chr300 8/19, chr540 6/12,
  gim103 2/5 (tol 1e-3). Exemple exact : gim103 bone id 9
  `T=(-1.2651, -5.6713, 229.172)` retrouvé tel quel dans un bloc.
- Forme « courbe 100 % constante » isolée (`sample_count=2`, 20 octets) :
  `curves.is_constant_curve` + `constant_curve_value` — gim103 b3/b4
  = 0.8415768146514893, 1 octet de différence dans le flux
  post-descripteurs → discriminateur idéal pour ce flux (probablement un
  index/id, pas une valeur numérique).
- Oracles à venir : pose à t0 ≈ bind pose, |quat|≈1, blocs à 2 clés =
  canaux constants.

### Mise à jour 2.4b — mots u32 internes de l'index (FAITS, sémantique ouverte)

- **Longueurs variables de groupes** : la plupart des slots = 2 mots
  `(valeur, 0xFFFFFFFF)` ; certains (chr300 slots 23/17/116/131/143,
  chr370 slots 26/27/37) portent 3–5 mots u32 supplémentaires dont
  `0x1010000`, `0x171dffff`, `0x1ffffff` (octets 0xFF de padding →
  candidates de listes d'ids de canaux/états, pas des valeurs numériques).
- **Aucune corrélation** de la valeur de tête avec `descriptor_count` ni
  `sample_count` (chr300 slots 97..115 : valeur 0x800 constante tandis que
  sc varie de 15 à 111) → la valeur n'est PAS un compteur de payload.
- Motifs bit observés : bit7 (0x80… 0x800 → 0x8800, 0x802/0x8802/0x9002 :
  bits 11/13/15 qui saturent indépendamment), 0x21/0x121/0x321
  (0x0100/0x0200 qui s'empilient) → candidate (état, flags) probable.
  Conservés RAW : **l'interprétation « état par slot » reste non démontrée**.
- gim103 : tous les mots internes valent 0 → l'actif ne contraint rien.
- Correction de vue : le slot 0 est parfois SANS bloc physique
  (offsets dupliqués, paires miroir) ; l'association slot→bloc passe par
  la table de offsets du paquet, pas par les groupes de mots.

### 2.4 — Liaison records ↔ slots ↔ blocs + durées (PARTIEL)

- **FAIT — slots ≠ clips** : le compte de slots dépasse le compte de
  records (chr300 : 150 slots / 121 records ; chr370 : 38/3 ; chr540 :
  88/…) → les slots **n'ont pas de bijection 1-1 avec les clips** ; ils
  candidate comme **états/sous-segments** adressés par la machine qstm
  (cohérent avec le couple (valeur, flags=0xFFFFFFFF) par slot et les
  bitfields d'état observés).
- **FAIT — la queue du qstm est dense en états** : au-delà de l'en-tête,
  les mots u32 du qstm varient par record sur des dizaines d'offsets
  (chr300, valeurs aux offsets +160..+252 : séries de petites valeurs
  entremêlées de sentinelles 0xFFFFFFFF et de mots à octets 0xFF →
  liste variable d'entrées d'états/événements). Longueurs de qstm
  variables (chr300 : 208..452 octets, 21 tailles distinctes).
- Champ **qstm+128** (décimal) varie par clip et s'accorde à l'ordre de
  grandeur des clips (chr300 : 1..142 ; chr540 : 66..266 ; chr590 : 11..45 ;
  gim103 : **92, 39, 92, 39**) → meilleur candidat des durées en frames,
  désormais soutenu par `sample_count`, mais pas encore démontré.
- Durée candidate à qstm+121/131 **hex** (`0x79`/`0x83`) : valeurs
  aberrantes → hypothèse à la position hex FAUSSE.
- Constantes partagées entre records du même asset : qstm+38=458, +168=44,
  +172=48 (chr540) ; dernier mot du qstm ≈ len(qstm)−4 (footprint/pad).
- Paires miroirs de gim103 : les deux blocs d'une paire partagent la MÊME
  durée candidate (92/92, 39/39) → cohérent avec des variantes miroir.
- **Correction (FAIT)** : l'ancien bloc « timeline » est un **index de
  slots**, pas des frames : `span/8` comptait accidentellement des groupes
  de deux mots ; la « grille de 8 frames » et toutes les conclusions de
  ticks associées sont annulées. `slot_count == block_count−3` sur 173
  packages ; les 5 anciens « conflits » (chr540 88/83, chr590 34/32,
  chr950 7/5, gim111 1/0, gim661 5/4) sont entièrement expliqués par des
  slots vides issus d'offsets de blocs dupliqués. La sémantique des mots
  u32 internes de l'index n'est pas encore résolue.
- FPS : toujours non déterminée ; à trancher seulement après obtention de
  clés datées.

### 2.5 — IK (À FAIRE)

- Canaux non-FK journalisés puis exclus (éventuel solveur 2-bone
  optionnel, flag `--ik-solve`, seulement après preuve que les canaux
  non-FK sont des positions d'effecteurs). Entrées disponibles :
  `chain_count` du squelette motion et flags de bones.

## Phase 3 — Reconstruction des clips (À FAIRE)

- `curves.py` + `export.py` : `decode_clip` → dicts clips glTF
  ({name, duration, channels[{joint, path, times, values}]}), FPS
  paramétrable, canaux constants → 1 keyframe.
- Conversion frames→secondes ; mapping index motion → index joint DDM via
  les ids identiques ; rééchantillonnage si temps non strictement
  croissants (exigence du writer GLB).
- Génération générique : tout DDM skinné (chr, gim, demo) avec
  motionSequence + motionPackage ET au moins un segment de courbe →
  exporte les clips ; sinon skin seul (comportement actuel).

## Phase 4 — Intégration pipeline + Godot (PARTIELLEMENT FAIT)

- FAIT : `tools/conversion/motion/export.py` (pont
  `discover_character_motion`, `decode_character_animations` — retourne
  `[]` tant que les courbes ne sont pas décodées, aucun guess) ;
  `ddm/skinned.py` rewiré vers `tools.conversion.motion.export`, plus
  aucune référence active à l'ancien module.
- À FAIRE : `export_folder_to_godot.py`, `.vscode/launch.json` (tâche
  « DDM: export with animations », `--animations all|<patterns>`),
  vérif import Godot 4 (`AnimationLibrary`).

## Phase 5 — Tests et validation (PARTIELLEMENT FAIT)

- FAIT : `tests/test_motion_sequence_package.py` (goldens corpus :
  chr100=414 records, skin 146 bones / ids distincts, |quat|≈1 bind,
  index gim103=8 & chr370=38 slots) ; `test_ddm_to_3d.py::…boundary…`
  réécrit sur le nouveau contrat. Suite complète : **128 tests OK** (1 skip).
- À FAIRE : goldens chr100, gim103, chr980, chr370, chr550 ; test
  bout-en-bout GLB (animation_count, noms, durées) ; CLI de masse pour cas
  limites (gim129 2 vs 6 bones, gim227 15 vs 14, chr200 8 Mo / 409 blocs) ;
  validation visuelle Godot (clips partant/arrivant sur poses plausibles).

## Phase 6 — Finalisation (À FAIRE)

- README.md (section pipeline animations), ROADMAP.md, `.vscode/README.md`,
  mise à jour de ce document.
- **Nettoyage final** : suppression des fichiers legacy listés dans la
  section « Architecture & fichiers impliqués » (après tests verts).

## CLI (FAIT)

`python -m tools.conversion.motion <noms...> [--all] [--curve-stats]
[--kb-root Chemin]` — imprime records/squelette/index de slots/blocs par asset.

## Risques connus

- Bit-format des courbes : si le delta-quantisé résiste, fallback
  documenté = échantillons f32 disponibles + tenue de valeur
  (STEP/LINEAR) — **approximation explicite**, jamais silencieuse.
- FPS non prédéterminée → paramètre éditable, détection par cohérence
  durée/bornes documentée.
- IK : option solveur 2-bone pilotée par `chain_count`, désactivée par
  défaut (flag `--ik-solve`), seulement après preuve que les canaux
  non-FK sont des positions d'effecteurs.
