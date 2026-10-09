# Project roadmap

This document tracks the current status and the agreed action plan for the
extractor. See [`README.md`](README.md) for the project goals and pipeline, and
[`REVERSE_DDM.md`](REVERSE_DDM.md) / [`MATERIAL_PBR.md`](MATERIAL_PBR.md) for the
reverse-engineering notes.

## Guiding principles

- Extract everything into the **most generic formats possible** (GLB, PNG,
  glTF 2.0 materials) so the assets are reusable outside this project.
- Target **Godot 4** as the reference consumer when a format decision is
  ambiguous.
- Do not reverse engineer assets whose meaning is bound to HexaEngine internals
  (the procedural sky, bespoke engine shaders). Reproduce them with host-engine
  primitives instead.
- Document every result with its confidence; never export a guessed value
  silently.

## Current status

| Area | Status | Notes |
| --- | --- | --- |
| PAK extraction | Stable | Indexed `0x120` records; stored + deflate blocks |
| XET → PNG | Stable | Lossless |
| DDS → PNG | Stable | DXT1 / DXT5 |
| DDM → GLB (geometry, UV, normals, materials) | Working (experimental) | DDM v3 layouts only |
| DDM skinned characters (skeleton, skin) | Working, **known ordering bug** | GLB `JOINTS_0`/`WEIGHTS_0` validated, but `decode_skinned_skeleton` without a motion resource pairs transforms with 4-block-reversed ids (REVERSE_DDM.md §28.2) → scrambled bind pose for the ~44 no-motion bundles |
| DDM → glTF animations | Partial, **layout root cause found** | Root translation + heading verified; joint rotations blocked by per-clip scalar layouts (REVERSE_DDM.md §28.5) and mid-stream insertions ignored by the contiguous binder (hand effector at scalar 184 vs binder's 182) |
| RSX shader → Godot PBR material | Approximation | `godot` shader + `.tres` + blend maps |
| Matcap/mask → PBR estimator | Placeholder | Research API only; not active |
| Engine-specific sky | Out of scope | Reproduce with a native Godot `Sky` |

## Action plan

The steps below are ordered. Each step must leave the test suite green.

### 1. Remove sky / engine-specific reconstruction — done

- The HexaEngine sky reconstruction path has been removed
  (`tools/conversion/godot/sky_to_godot.py`, its tests, and the sky research
  note). The sky is to be rebuilt with Godot's native `Sky`/`Environment`.
- The generic RSX inspection tools (`shader_inspect.py`, `rsx_fp_disasm.py`)
  are kept because they also serve material shader research.

### 2. Drop the OBJ export path — done

- GLB is now the only geometry output. The `--format` switch, the OBJ/MTL
  writers, the deprecated `ddm_to_obj.py` wrapper, and their tests/launch
  configurations have been removed.
- Lightweight CSV/JSON diagnostics used during reverse engineering are kept
  behind the default (non-`--final`) path.

### 3. Clean up the code and architecture — done

- The monolithic `ddm_to_3d.py` (≈2450 lines) is split into a focused
  `tools/conversion/ddm/` package: `binary` (primitive reads), `materials`
  (materials/textures/PBR), `geometry` (topology/submeshes/map sections),
  `skinned` (skeleton + skin), `scene` (export orchestration) and `cli`.
  `ddm_to_3d.py` is now a thin, backward-compatible re-export shim.
- Dead code and unused imports were removed across the package.
- Unused imports, undefined names and blank-line runs were cleaned up.

### 4. Stabilize and generalize DDM → GLB + RSX → PBR — in progress

Two material output modes are exposed through `--material-mode`: `pbr` (a
portable, approximated glTF 2.0 material) and `godot` (a faithful Godot 4
reconstruction with a shader, `.tres` materials and blend/environment maps).
`original-godot` remains accepted as a deprecated alias of `godot`.

- Done: golden-output test (`tests/test_glb_golden.py`) freezing the byte-exact
  GLB of a reference DDM, plus structural and repeatability checks.
- Done: explicit `godot` mode with a back-compatible alias, normalised through
  `material_mode_of`/`uses_godot_materials`, and documented PBR-vs-Godot.
- Done: suffix-aware, content-validated texture classification
  (`texture_suffix_role` + `role_source` provenance in `materials.py`,
  covered by `tests/test_material_classify.py`).
- Done: unsupported DDM layouts raise a structured `UnsupportedDDMVariant`
  carrying `version`/`variant`/`reason`, reported by the CLI as
  `[UNSUPPORTED]` (distinct from `[ERROR]` for corrupt files), covered by
  `tests/test_ddm_variant.py`.
- Done: multipass overlays (map materials painted on the same geometry, e.g.
  `gake102__multi` over `gake102__base`) are collapsed as exact duplicates in
  `pbr`, and folded into the host as a shader-mixed **detail layer** in `godot`
  (`_fold_multipass_details` + `detail_texture`/`detail_normal_texture`).
- Done: reusable Godot assets live once in `godot/utility/` (shader +
  `assign_materials.gd`) instead of being regenerated per export. Simple
  families emit `StandardMaterial3D`; only unsupported variants reference the
  stable `res://majin_utility/majin_multitexture.gdshader` path.
- Port `map101` in `godot` mode as the reference map workflow, then iterate on
  the remaining fidelity gaps (P31/P33 environment vector, utility-mask
  polarity, partial decals) — `godot` may diverge freely from `pbr`.
- Decode the P31/P33 RSX blend formula and utility-mask polarity instead of
  exposing them as manual parameters; where an effect is not reproducible as
  PBR, keep it in the `godot` shader path.
- Done: skinned-group detection now validates the 255-sum weight invariant of a
  candidate's first vertex, so a stray `u32 == 8` inside vertex data is rejected
  (this fixed `chr500`, which previously failed with `weights sum to 123`).
  Every decoded vertex of every decodable character is now validated for finite
  positions, in-range UVs, normalized weights and resolvable joint indices; see
  `REVERSE_DDM.md` "Skinned group detection" and
  `research/skin_decode_probe.py` (`--best`, `--validate`, `--dump`).
- Done: `_joint_global_matrices` resolves the bind hierarchy iteratively with a
  cycle guard, so a rig whose parent chain loops (chr500) exports instead of
  raising `RecursionError`.
- Open: a character file can contain several skinned groups (body + weapons);
  the decoder exports the first one. Decoding every group would include held
  weapons and is tracked here.



### 5. Export animations in the GLB

- Done: bone_000 rotation uses the complete XYZ Euler triplet s5..s7. The
  former scalar-0-only workaround lost all rotation in `boredom`; multiplying
  s0 by s5..s7 doubled turn clips. Findings and
  the cross-character verification are in `research/MOTION_ANALYSIS.md`, with
  `research/motion_probe.py` to reproduce them.
- Done (generic binding): the scalar-curve → joint binding no longer depends on
  the hard-coded humanoid signature. `decode_character_animations` now tries the
  verified humanoid binder, then a **name-free generic root/spine binder** driven
  by the motion skeleton itself:
  - FK vs IK comes from the reference skeleton's `constraint_flags`
    (`0x03`/`0x04`/`&0x08`), via `classify_reference_roles`;
  - rotation vs position follows the IK role (effectors are model-space
    positions), not a magnitude threshold;
  - the contiguous root chain (`parent_index == index - 1`) gives the ordered
    prefix, via `root_chain_length`;
  - the canonical scalar count is the largest triplet-aligned variant
    (`select_canonical_scalar_count`), which also fixes chr300's older layout.
  Across the 112 characters, **all 34 with a motion resource export clips**
  (chr200 346, chr100 256, chr300 150, chr301 140, chr560 57, chr370 38 ...),
  with 0 errors. See `research/MOTION_ANALYSIS.md` "Generic rig binding".
- Done: `research/motion_semantics_probe.py` inspects the FK/IK and
  rotation/position discriminants per character.
- Remaining: promote the verified channels out of the experimental flags, and
  add a golden test on a short clip.
- Remaining: bind the rigs whose IK effectors the generic root chain does not
  reach yet (the conservative binder stops at the root/spine prefix).
- Understand the descriptor modes `0..4` and the prefix-coded 247/249/251/253
  layouts.

### 5b. Generic DDM variant discrimination — in progress

Goal: the decoder must select the right layout for **every** DDM in the game
from bytes alone, never from a hard-coded offset or a character name.

- In progress: catalogue cross-character discriminants (version word, attribute
  count, vertex stride, skeleton size, endianness tag) across chr200/300/301/500/
  700 and the static maps. Findings and confidence levels are recorded in
  `REVERSE_DDM.md` (section "Generic discriminating fields").
- Done: `research/ddm_variant_probe.py` + `research/ddm_structural_scan.py`
  survey every DDM. Key confirmed results: the version word is constant `3`
  (never a discriminant), the type word is orthogonal to the layout, and there
  is **no** header-level static/skinned flag — the split must stay structural.
  The skeleton is gated on `0xB0` (transform count) and `0xB4` (root bone id).
- Open: decode the 34-file "2-bone prop" family (`chr7xx`/`chr9xx`, `0xB0 = 2`)
  that currently falls through to `static-or-other`.
- Compare the PSX-era (`BigEndian`) motion interpretation with the PS3 file
  layout to see which fields survive the port and can act as version gates.
  Confirmed so far: the `ENDIAN` path token, the `LittleEndian` branch and the
  `chr051/052` names in `PathCharaMotion`, plus the payload-free quarter-turn
  angle modes `1..4` in every clip.

### 5c. DDM skeleton storage-order fix — done (2026-10-09 act session)

Goal: every skinned bundle decodes its bind pose from the DDM alone.

Done:
- `tools/conversion/ddm/skinned.py` gained `_unreverse_word_blocks` and
  `decode_skinned_skeleton` now reads the id and parent tables over
  `padded_count` bytes and un-reverses every aligned 4-byte block
  (little-endian u32 words); the "duplicate zero byte" dedup hack is gone.
- The motion resource's `transform_bone_ids` remains as an order validator
  (exact or unique-order prefix, for chr900's extra attachment node).
- `tools/conversion/motion_decode.py` gained the diagnostic
  `compare_motion_skeleton_to_ddm(...)` annotation
  (`motion_translation_error` in the motion report; chr100-332/350-590,
  900, 921 measure ≤ 1.2e-4; chr510/922/940 expose real bind-pose deltas).
- Corpus regressions live in `tests/test_ddm_variant.py`
  (`SkeletonStorageOrderTests`: sane unit roots, chr900 extras node,
  chr500 zero parent cycles). Full suite green (151 tests).

Follow-ups noted in §28: per-clip layout derivation (5d below),
rotation composition experiment, prop family skeleton layout, tangents,
CRG semantics.

- Read the `0xB4` id table and parent pool over `padded_count` bytes and
  un-reverse every 4-byte block (little-endian u32 words), per
  `REVERSE_DDM.md` §28.1; drop the "duplicate zero byte" dedup hack (padding
  then lands at the array end naturally).
- Pair translations/rotations with the un-reversed order. The motion resource
  becomes a **validator** (assert translations within ~1e-4) instead of the
  order source.
- Verified expectations: chr101/110 root `[0, 98.6, 0.63]`, chr800
  `[0, 110, 0]` with identity rotation; chr500 parent cycles drop to 0;
  all bundles with a motion resource keep resolving byte-exact.
- Add a skinned golden GLB test so this stays frozen afterwards.

### 5d. Per-clip scalar layout derivation — done for the humanoid binder (2026-10-09 act session)

Goal: replace the canonical-count arithmetic binding with a per-clip
structural derivation.

Done:
- `tools/conversion/motion_decode.py` gained the structural anchor machinery:
  `collect_constant_anchors` (mode-5 payload constants + the ±π/2, π
  carriers; the pervasive mode-0/1 zero carriers are excluded, ambiguous
  duplicate value keys are dropped), `build_canonical_anchor_map` (the
  invariant rig constants of the canonical-count clip) and
  `derive_humanoid_scalar_starts` (monotone anchor matching, piecewise
  per-block shift, strict monotonic + in-range guards on the derived starts).
- `decode_character_animations` and `infer_humanoid_rotation_bases` now bind
  every clip through that derivation and keep `remap_humanoid_scalar_starts`
  as a documented fallback (`per_clip_scalar_layout.kind` records which one
  served: `per_clip_anchors` vs `canonical_count_arithmetic`).
- Latent bug fixed along the way: the canonical-count scan also read the
  *skeleton* segment's leading bytes as a clip count (chr302/303 read a
  triplet-aligned 11012 and silently lost the verified humanoid binder to
  the generic root-chain fallback). `skeleton_segment_offset` is now
  excluded.
- Corpus validation (geometric constraint: sampled IK target triplets keep
  the verified effector positions, feet near ground / hands in range):
  chr301 derived 140/140 vs legacy 136/140; chr302 150/150 vs 145/150;
  chr303 149/150 vs 145/150. Zero legacy fallbacks needed on the chr3x
  family.
- Regressions: `tests/test_motion_decode.py`
  (`test_per_clip_anchors_*` synthetic identity/deletion/ambiguity cases,
  `test_per_clip_scalar_layout_derives_on_chr3x_humanoids` corpus test).

Still open (moved to the remaining §J items):
- rotation composition / Euler order and the exact 184-vs-182 effector
  offset inside the target block (5b/4 of the plan);
- the same per-clip derivation generalized beyond the humanoid binder
  (generic root-chain rigs read a constant layout);
- mode-7 Hermite tangents→CUBICSPLINE, fps, CRG, prop family (0xB0=2).

- Re-attribute the published per-bone verification table
  (`MOTION_ANALYSIS.md`) to the verified scalar positions (184 not 182) once
  the derivation is in.

### 4. Composition des rotations — preuve enregistrée (2026-10-09 act session)

- Le défaut d'axe de la tête est départagé par la base DDM, sans swizzle : sur
  le clip physique 2 commun à chr301/302/303, le canal Z local de l'os 110 est
  transformé par le bind en axe modèle X. Le modèle historique global conserve
  une forte composante Z (inclinaison latérale), tandis que la composition
  locale, rotation complète de bone_000 incluse, donne
  `(0.716,-0.697,0.053)` avec une composante Z presque annulée. Probe reproductible :
  `research/fk_rotation_probe.py`; régression corpus dans
  `tests/test_motion_decode.py`.
- La magnitude n'attribue plus une sémantique `position`: seuls les effecteurs
  déclarés par les flags squelette sont des positions. Les grands triplets non
  déclarés restent `unknown_controller` et sont exclus du FK; une rotation
  déroulée au-delà de 2π reste une rotation.
- La sous-structure des segments de jambes est mesurée (REVERSE_DDM.md
  §28.7) : triplets de rotation liés alternés avec des contrôleurs de pas aux
  constantes invariantes `(25.389, 0, ±9.981)`, `(0, 15.185, 0)`,
  `(34.75, 0, 0)` — une unité de plus côté droit, d'où des sextets
  effecteur à +2 (os 204) / +4 (os 217) du slot lié.
- `derive_humanoid_ik_targets` : scan structurel par clip des sextets
  `[cible xyz][orientation xyz]`, branché dans `apply_experimental_humanoid_ik`
  (fallback = anciens offsets fixes, tracés). Le bug publié « cible main lue à
  182 au lieu de 184 vérifié » est corrigé par structure, plus d'arithmétique
  en dur. Corpus : les quatre sextets sont trouvés dans chaque clip canonique
  chr3x.
- Sweep FK jambes (6 ordres Euler × 3 compositions × 8 convictions): avec les
  triplets de rotation mal alignés (contrôleurs de pas lus comme rotations),
  erreur bornée 47..142 cm sur tous les clips chr3x et jamais discriminante.
  La composition exacte (ordre Euler, absolu vs delta-de-bind) ne peut être
  tranchée qu'après la règle exacte par os des slots de rotation du segment
  jambes — reste l'inconnue n°1 de §J.
- Suite de session : la cartographie fine (MOTION_ANALYSIS.md) établit que le
  pied (os 202) n'a pas de triplet propre — son slot est le porteur qui
  précède le sextet, son orientation vit dans le triplet d'orientation du
  sextet. Les longueurs des blocs contrôleurs varient (4/3/3) et la phase
  modulo-3 glisse entre les côtés : un raffinement automatique des slots
  resterait une heuristique non démontrée — il n'est PAS câblé (le scanner
  d'effecteurs, lui, est prouvé et câblé). La piste machine à états par
  triplets invariants est consignée pour la session suivante.

### 6. Reorganize the tools and research tree (after steps 4 and 5)

Deferred until after steps 4 and 5 so the DDM code is functionally stable
first. The current tree works and is already layered (no import cycles), but
`tools/conversion/` is a flat bag that mixes four concepts: format decoders,
output writers, shader research and the DDM decoder. The target groups modules
by responsibility:

```text
tools/
  extraction/            PAK extraction (unchanged)
  formats/               input format decoders
    xet.py               (ex xet_to_png.py)
    dds.py               (ex dds_to_png.py)
    motion.py            (ex motion_decode.py)
    ddm/                 current DDM package
  writers/               output writers
    glb.py               (ex glb_export.py)
    godot.py             (ex godot_export.py)
    blend_mask.py
  shaders/               RSX shader research
    inspect.py           (ex shader_inspect.py)
    disasm.py            (ex rsx_fp_disasm.py)
  entry/                 CLI entry points
    convert_ddm.py       (ex ddm_to_3d.py shim)
research/
  material_pbr.py        (ex material_pbr_estimator.py)
```

- Fix the fragile absolute import in `rsx_fp_disasm.py`
  (`from tools.research.shader_inspect import ...`) so all internal imports use
  one consistent style.
- Remove the duplicated `try/except ImportError` fallbacks by separating the
  library from its CLI entry points.
- Update tests, `.vscode/launch.json`, `.vscode/tasks.json` and the docs to the
  new paths. Keep `ddm_to_3d.py` available as a compatibility wrapper until
  callers migrate.

## Documentation maintenance

- `README.md`: goals, pipeline, and usage.
- `ROADMAP.md` (this file): status and action plan.
- `REVERSE_DDM.md`: validated DDM findings.
- `MATERIAL_PBR.md`: material/PBR research plan.
- `research/README.md`: research tooling overview.
