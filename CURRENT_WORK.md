# CURRENT_WORK — État de la recherche Phase 2.2/2.3 (mapping canaux humanoïdes)

> Document de reprise : où en est la recherche, ce qui est prouvé, ce qui est
> invalidé (y compris les invalidations non évidentes), et ce qui reste à
> faire. Convention *Evidence over guessing* — chaque fait cite sa sonde.
> Ce document couvre le travail **non encore documenté dans REVERSE_MOTION.md**
> au moment de sa rédaction ; le fichier main peut avoir rattrapé depuis.

## État d'avancement de l'étape en cours (Phase 2.2 → 3)

**Objectif de l'étape** : démontrer le mapping `scalar_index → (bone, composante)`
pour les packages humanoïdes (chr300/chr370/chr590/…) afin de débloquer
l'export des clips (Phase 3).

**Ce qui est FAIT (code commité, tests verts) :**

- `curves.py` :
  - `carry_fidelity` / `carry_chains` / `block_end_values` / `block_constants` —
    oracle de chaînes de segments (un bloc suivant tient la valeur du
    précédent sur ses canaux sans payload). Mesuré : chr300 31 chaînes
    (la plus grande 37 blocs, fidélité moyenne intra-chaîne 0.879),
    chr540 31 chaînes, chr370 26, gim103 4 (fidélité 0.958).
  - `BASE_CONSENSUS = 0.8` — la porte de `resolve_scalar_channel_base` exige
    maintenant un vote **quasi unanime** des ancres de bind pose. La plurality
    brute fabriquait des poses fantômes (quaternion norms 38..2002 sur chr540).
  - `QUAT_NORM_TOLERANCE` — oracle de pose dans `decode_scalar_clip` : une
    rotation n'est émise que si **chaque** quaternion émis est unitaire
    (±1e-3, normalisation explicite). Un bloc qui échoue refuse tout le
    bloc (jamais de canal deviné).
- `export.py` :
  - `FPS_FALLBACK = 30` — horloge **vérifiée** (plus un fallback) : les
    tangentes mode 7 sont des dérivées par seconde ; le ratio
    `(v1 − v0) / tangente` sur les gaps d'une frame mesure 1/30 sur
    chr300 (10 305 gaps) et chr540 (115 033 gaps). Voir
    `research/HUMANOID_LAYOUT_FINDINGS.md`.
  - `decode_character_animations` : double porte documentée (consensus base +
    bijectivité record↔bloc). **Comportement mesuré** : gim103 → 4 clips
    exportés (translation-only), chr540 → **0 clip** (son vote 205/181/317
    est un split multi-section, la porte refuse), chr300/chr370 → 0
    (pas d'ancres). Le verbe « no-ghost » est appliqué à la lettre.
- Tests : `tests/test_motion_curves.py` élargi (goldens gim103 base=21,
  translation-only ; refus chr540 sous base 205 ; chaînes chr300) ;
  suite complète **138 tests OK** (1 skip).

## Faits nouveaux mesurés (sondes, corpus)

Source : `research/HUMANOID_LAYOUT_FINDINGS.md`, `PACKAGE_GROUP_FINDINGS.md`,
`QUATERNION_SPACING_FINDINGS.md` (probes `humanoid_layout_probe.py`,
`package_group_probe.py`, `quaternion_spacing_probe.py`).

1. **La lecture quaternion est invalidée sur TOUT le corpus** (fact dur) :
   0 quaternions unitaires parmi tous les quads consécutifs datés —
   gim103 0/24, chr300 0/1920, chr370 0/342, chr540 0/1111, chr590 0/463,
   chr100 0/5608. Le test exhaustif meet-in-the-middle sur tous les
   4-sous-ensembles (des millions testés) donne aussi 0 hit
   (chr100 : 3 hits isolation 78M+), i.e. ce ne sont pas non plus des
   composantes de quaternion réordonnées. **Les rotations ne sont donc PAS
   stockées comme composantes quaternion dans ces scalaires** — candidat
   restant : triplets d'euler (degrés, cf. valeurs ~100-400 en constants).
2. **Le mapping « une base globale par package » est mort pour les
   humanoïdes** : chr540 mélange au moins trois sections de canaux dans un
   seul bloc (segments d'ancres s[429..451]→offset 181, s[602..630]→205,
   s[1022..1092]→317) ; chr100/chr330/chr360/chr361 présentent le même
   motif de vote éclaté. En revanche plusieurs petits assets votent
   **unanimes** : gim103=21 (8/8), gim108/238/324=7 (61/61), gim533=7 (27/27),
   gim202=7 (18/18), gim615/616=3 (10/10), gim129=7 (12/13), gim130=3 (40/40).
3. **Les déscripteurs sont périodiques par bone, 7 par groupe, avec le code 1
   en position 6** (comp 6 = R.w, mode 1 = sans payload) : gim108 b1 =
   `[0,0,1,1,1,1,1 | 7×6, 1 | 7×6, 1 …]`, le code 5/7 sur les comps 0..5.
   Les blocs couvrent une **plage contiguë de canaux** `[base, base+dc)` ;
   le nombre d'ancres (ex. gim503 b1 : 46/168 ; gim108 b1 : 59/231) montre
   que la base est **potentiellement locale au bloc**, pas globale.
4. **Préfixe racine (root prefix)** observé sur chr300/chr540 : les scalaires
   2..4 portent la translation racine en espace modèle (chr300 : 112.147
   vs bind 114.54 ; chr540 : constante 1024.18), s5..s7 = deltas d'orientation
   racine démarrant à 0. Le préfixe s'ouvre avec 2 modes 0 (carry) + 7 modes
   « datés/const » + 1 mode 1.
5. **Verdicts de la porte de consensus par package** (PACKAGE_GROUP_FINDINGS) :
   24 packages unanimes (exportables), 39 « split » (porte verrouillée),
   le reste sans ancres. **Les squelettes partagés n'impliquent pas des bases
   partagées** (ex. chr330/chr332 votent pareil mais pas gim511/512/…/514),
   donc la porte DOIT rester par-package — hypothèse de consensus de groupe
   invalidée pour la base (le probe `package_group_probe.py` le mesure).
6. **Cas structurellement contradictoires documentés** : chr330 vote la MÊME
   valeur unique (7.177520) vers des bases différentes 227/225/229/223 selon
   l'indice scalaire — impossible avec une base globale : l'espace d'indices
   n'est pas global par package (motif à retravailler par sections).

## Pistes invalidées (dont les invalidations ne sont pas évidentes)

| Piste | Pourquoi c'est non-évident | Preuve |
|---|---|---|
| Quaternion = 4 scalaires consécutifs (comps 3..6) | l'alignement stride-7 est exact (codes descripteurs périodiques par 7) — on s'attendrait à ce que les quaternions suivent | 0 quaternion unitaire partout ; le test exhaustif 4-sous-ensembles (meet-in-the-middle, des millions) donne aussi 0 → même réordonnés ce n'est pas un quaternion |
| Base de canaux globale par package | gim103 ET gim533/gim202/gim108/gim615 se comportent exactement comme si la base était globale (votes unanimes) → le modèle fonctionne *parfois*, ce qui suggère qu'il devrait toujours | chr540/chr100/chr330 : ancres votant 2-3 bases selon la position dans le bloc (segments contigus d'ancres avec offset constant) |
| « Les sections de valeurs suivent le fil de descripteurs » | le flux de modes s'arrête proprement à `dc`, et le padding post-descripteurs est vide pour les blocs u8 | INVALIDÉ : l'alignement mesuré 1/150 (chr300), 0 ailleurs — une table de temps quantifiés s'intercale (désormais décodée par `decode_curve_block`) |
| Slots ↔ clips bijective 1-1 | chr300 a 150 slots pour 121 records | `slot_count == block_count−3` tient sur 173 packages ; les slots sont des **états/sous-segments** adressés par la machine qstm (Phase 2.4) |
| La valeur de tête du groupe de mots d'index est un compteur de payload | intuitif (0x800 ≈ nombre de mots) | chr300 slots 97..115 : valeur constante 0x800 alors que `sample_count` varie de 15 à 111 — aucune corrélation |
| Squelettes identiques ⇒ même base de canaux | intuitive (même rig = même layout) | gim511..514 (4 assets, même squelette 7 bones) votent tous pareil (5/7/9) mais chr330/chr332 votent 227/225/229 ; et chr360/chr361 sont décalés de +360 exactement — bases **dupliquées avec rigs différents** |
| Recoder les rotations en normalisant les composantes brutes | le mode 7 stocke déjà des paires (valeur, tangente) qui semblent poser l'interpolation | chr540 base 205 : 5144 quartets testés, écart médian à la norme 0.899 (garbage), pas une erreur d'échelle simple |

## Ce qui reste à faire pour cette étape (Phase 2.2/2.3 humanoïde)

1. **Identifier l'unité des rotations** (le candidat euler degrés) : les
   constantes de rotations se lisent ~100..400 (chr370 : 300.0002, 271.05…
   dans les constantes de bind). À vérifier : conversion degrés→radians
   (facteur π/180), ordre des axes (ZYX/XZY…), et pivot global (les quads
   stride-7 avec comp 6 non-stockée suggèrent R.w **dérivé** ou non stocké).
2. **Démontrer la base par bloc** : chaque bloc semble porter sa propre
   plage `[base, base+dc)` (descripteurs périodiques + ancres contiguës
   avec offset constant). Retrouver comment la base est encodée (dans le
   padding post-descripteurs ? dans les mots d'index de slots ?).
3. **Refaire les oracles de pose avec des eulers** : `|q|≈1` devient
   « l'angle reconstruit depuis 3 scalaires doit reproduire la composante
   quaternion du bind pose » sur les blocs constants (ancres), puis sur les
   clés datées (continuité).
4. **Étendre `decode_scalar_clip`** au mode euler (gating par l'oracle #3)
   et reconnecter `decode_character_animations` (porte de consensus peut
   rester, mais doit accepter les bases multi-sections quand chaque section
   est démontrée bloc par bloc).
5. **Machine qstm** (Phase 2.4c) : le bytecode `01 00 00 02 | 01 06 00 07…`
   et les escaliers de temps restent non décodés — reporté après le premier
   export end-to-end (décision déjà prise dans REVERSE_MOTION.md).
6. **Validation visuelle Godot** (Phase 5) : clips plausibles au premier et
   dernier frame, avec les assets unanimes en premier (gim103, gim533,
   gim202, gim615/616, gim108) puis les humanoïdes.

## Comment reprendre le travail

```bash
# sondes (rejouables, résultats dans research/*FINDINGS.md)
python3 research/humanoid_layout_probe.py [asset ...]
python3 research/package_group_probe.py [asset|groupe ...]
python3 research/quaternion_spacing_probe.py [asset ...]

# pont d'export (gim103 exporte ; chr300/chr540 restent refusés tant que
# leur mapping n'est pas démontré)
python3 -m unittest tests.test_motion_curves tests.test_motion_sequence_package
```

Les trois fichiers de findings et les 3 probes sont **commités** — ils
constituent le point de départ exact. Les tests verts pin les invariants
(base 21 gim103, refus chr540, chaînes chr300, FPS 30).
