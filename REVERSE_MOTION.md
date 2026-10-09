# REVERSE_MOTION — Décodage HexaEngine motion → Godot 4

> Statut du plan et avancement. Convention projet : *Evidence over guessing* —
> seuls les faits vérifiés sur le corpus sont décrits comme confirmés.

## Objectif

Décoder les données d'animation HexaEngine (`motionSequence psmr` +
`motionPackage`) des assets skinnés DDM et exporter skin + animations vers
Godot 4 via le pipeline GLB existant.

## Contexte du fichier (convention KB)

- `chara/<n>/<n>` ↔ `motionSequence/<n>/<n>` ↔
  `motionPackage/<n>/BigEndian/<n>` : liés **par convention de nom**
  (aucune référence croisée dans les binaires).
- Extension naturelle : `demo/<d>/motionPackage` (cutscenes), gems (gimXXX).

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
- **Timeline (avant-dernier bloc)** — décodée : `[u32 segment_count]`
  `[segment_count+1 bornes u32 en frames]` `[count × (u32 valeur,
  0xFFFFFFFF)]`. `segment_count == nombre de blocs de courbes` (vérifié
  chr300/chr370/gim103/chr100). Timeline **optionnelle** (paquets
  placeholders minuscules sans timeline).
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
- Chr100 : 457 segments de timeline pour 460 blocs ; chr300 : 150/153.

## Phase 2 — Recherche format des courbes (EN COURS)

Observations accumulées (à confirmer) :

- Blocs de courbes à 4 bytes d'en-tête `[type][A][0][B]` ;
  type ∈ {0,1,2} (+type 3/4 = rares). Distribution corpus :
  type0=1425, type1=1663, type2=794, type3=599, type4=2.
- Contenu : **bit-packed delta-quantisé** (motif répétitif `24 92 49` =
  motif binaire « 001001… » = deltas constants), paires `(f32 valeur,
  u32 0)` (canaux constants), puis flux f32 de keyframes.
- gim103 blocs 4/5 : **1 seul octet** différent (0x00 ↔ 0x02) → sujets A/B
  parfaits (probablement un index/id, pas une valeur numérique).
- gim103 b2/b7 partagent leurs 10 derniers f32.

Protocole testé / à faire :

1. **2.1** cartographier header → sémantique de A/B (discriminateurs :
   compter les f32 du flux vs A/B par bloc).
2. **2.2** décoder la table de canaux bit-packed (candidats : canaux
   indexés dans une carte globale type bloc (0x12,0x0e) de chr100).
3. **2.3** décoder les keyframes — oracles : pose à t0 ≈ bind pose,
   |quat|≈1, blocs à 2 clés = canaux constants.
4. **2.4** lier records ↔ timeline ↔ blocs : tester (A) valeur de segment
   = index de bloc, (B) plages de frames des records vs durées qstm,
   (C) permutation records (tri alphabétique) vs bornes chronologiques
   (candidat : FILETIME des records). Déterminer la FPS (30 vs 60).
5. **2.5** IK : canaux non-FK journalisés puis exclus (éventuel solveur
   2-bone optionnel, flag `--ik-solve`, seulement après preuve).

### Résultats 2.4 (probes qstm / timeline)

- Testé durée candidate à qstm+121/131 **hex** (`0x79`/`0x83`) : valeurs
  aberrantes → hypothèse à la position hex FAUSSE.
- Onzième scan u32 des qstm (step 2, valeurs 1..4000) :
  - champ **qstm+128** VARIE par clip (chr540 `roar_01_00`=90,
    `roar_03_00`=94, `shot_a_03_00`=138) → **candidat durée en frames**
    (à confirmer par recouvrement avec les bornes timeline ;
    span chr540 = 704 frames / 53 records, cohérent en ordre de grandeur) ;
  - constantes partagées entre records du même asset : qstm+38=458,
    +168=44, +172=48 (chr540) ;
  - dernier mot du qstm ≈ len(qstm)−4 (footprint/pad).

Vérifications timeline corpus : `segment_count == len(curve_blocks)`
pour **141/146** packages à timeline ; 5 conflits (chr540 88/83,
chr590 34/32, chr950 7/5, gim111 1/0, gim661 5/4) → la liaison n'est PAS
un simple alignement 1-bloc-per-segment. Premiers `pairs` : chr540
(96,-1) puis (0,-1)… ; chr590 (96)(256)(769) puis (0)… → valeurs
candidates = offsets/indexes non triviaux (à élucider en Phase 2.4).

## Phase 5 partiel (FAIT)

- `tests/test_motion_sequence_package.py` : goldens corpus
  (chr100=414 records, skin 146 bones / ids distincts, |quat|≈1 bind,
  timeline gim103=8 & chr370=38 segments — chr370 41 paires pour 38
  segments noté comme question ouverte 2.4), skip si corpus absent.
- `tests/test_ddm_to_3d.py::test_external_motion_boundary_table_is_detected`
  réécrit sur le nouveau contrat (discover_character_motion via
  `tools/conversion/motion/export`), corpus réel.
- Suite complète : **121 tests OK** (1 skip corpus).

## Phase 4 partiel (FAIT)

- `tools/conversion/motion/export.py` : pont pipeline
  (`discover_character_motion`, `decode_character_animations`) — ce
  dernier retourne `[]` tant que les courbes ne sont pas décodées
  (aucun guess), l'exporte skin+squelette seul, sans régression.
- `ddm/skinned.py` rewiré de `motion_decode` (archivé) vers
  `tools.conversion.motion.export` ; plus aucune référence active à
  l'ancien module.

## CLI (FAIT)

`python -m tools.conversion.motion <noms...> [--all] [--curve-stats]
[--kb-root Chemin]` — imprime records/squelette/timeline/blocs par asset.

### Mise à jour Phase 2.3 — ORACLE TABULÉ (FAIT)

- Les valeurs « constantes » des blocs de courbes = **valeurs de pose
  des canaux figés**, vérifiées contre la bind pose du bloc squelette :
  `curves.bind_pose_hits()` → chr100 **87/145**, chr300 8/19,
  chr540 6/12, gim103 2/5 (tol 1e-3). Exemple exact : gim103 bone id 9
  `T=(-1.2651, -5.6713, 229.172)` retrouvé tel quel dans un bloc.
- Forme « courbe 100 % constante » isolée (bloc B=2, 20 octets) :
  `curves.is_constant_curve` + `constant_curve_value` — gim103 b3/b4
  = 0.8415768146514893, 1 octet de différence (discriminateur A/B en
  recherche).
- Byte 2 du header de bloc = **phase** : 0 sur 4415/4474 blocs, 1 sur
  59 — gardé brut, plus d'hypothèse « doit être 0 ».

### Mise à jour Phase 2.4 — corrélations (PARTIEL)

- `qstm+128` **décimal** varie par clip et s'accorde à l'ordre de grandeur
  des clips (chr300 : 1..142 ; chr540 : 66..266 ; chr590 : 11..45 ;
  gim103 : **92, 39, 92, 39**) → reste le meilleur candidat des durées en
  frames par clip, mais pas encore démontré.
- Paires miroirs de gim103 : les deux blocs d'une paire partagent la MÊME
  durée candidate (92/92, 39/39) → cohérent avec des variantes miroir.
- chr540 et gim103 : bornes timeline **uniformes avec un pas de 8 frames**
  (chr540 360,368,…,1064 ; gim103 40,48,…,104) → une grille de résolution
  8 frames existe au moins pour ces familles ; chr300 est variable.
- FPS : toujours non déterminée (grille 8 frames compatible 30 et 60).

### Résultats 2.1 (probe corpus — research/curve_header_probe.py)

- Sur 206 assets : `A` et `B` varient tous les deux par bloc ET par
  asset (ex. chr300 A=247..253 vs chr100 A=0..251 ; B min..max propre
  à chaque famille). Le nombre de paires `(value,0)` n'est fonction ni
  de A ni de B. → **ni A ni B ne sont un compteur simple** ;
  sémantique encore ouverte, la sonde `research/curve_header_probe.py`
  (→ `research/CURVE_HEADER_FINDINGS.md`) sert de réexécution.

### Mise à jour Phase 2.4 — liaison timeline (PARTIEL/FACTE)

- **FAIT (3 familles)** : nombre de paires `(value, sentinel)` de la
  timeline = **span / 8** — chr370 328/8=41 ✓ ; chr550 504/8=63 ✓ ;
  gim103 64/8=8 ✓. La timeline est donc *une entrée par tick de 8
  frames*, pas une table par clip.
- Hypothèse « valeur = offset de bit cumulatif » INVALIDÉE : les valeurs
  oscillent (289→288→289, 1024→0x171FF0FF→retour). Hypothèse A du plan
  (valeur= bitpack bloc index | flags) non retenue telle quelle ;
  paire (état, flag) par tick probable.
- Records : aucun lien 1-1 avec les ticks (3 records / 38-41 ticks) ;
  la permutation records↔clips reste à élucider (Phase 2.5/3).

## Phases 3 à 6 (À FAIRE)

- **Phase 3** `curves.py` + `export.py`: decode_clip → dicts clips glTF
  ({name, duration, channels[{joint, path, times, values}]}), FPS
  paramétrable, canaux constants → 1 keyframe.
- **Phase 4** intégration : `ddm/skinned.py` → `motion.export...`,
  `export_folder_to_godot.py`, `.vscode/launch.json` (tâche « DDM: export
  with animations »), vérif import Godot 4 (`AnimationLibrary`).
- **Phase 5** tests/targets goldens : chr100, gim103, chr980, chr370,
  chr550 ; test bout-en-bout GLB (animation_count, noms, durées) ; CLI
  de masse pour cas limites (gim129 2 vs 6 bones, gim227 15 vs 14,
  chr200 8 Mo / 409 blocs).
- **Phase 6** finalisation README/ROADMAP + mise à jour de ce document.

## Risques connus

- Bit-format des courbes : si le delta-quantisé résiste, fallback
  documenté = échantillons f32 disponibles + tenue de valeur
  (STEP/LINEAR) — **approximation explicite**, jamais silencieuse.
- FPS non prédéterminée → paramètre éditable, détection par cohérence
  durée/bornes documentée.
- IK : option solveur 2-bone pilotée par `chain_count`, désactivée par
  défaut (flag `--ik-solve`), seulement après preuve que les canaux
  non-FK sont des positions d'effecteurs.
