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
| DDM skinned characters (skeleton, skin) | Working (experimental) | GLB `JOINTS_0`/`WEIGHTS_0` |
| DDM → glTF animations | Partial | Root translation + Y heading verified; full joint binding unresolved |
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
- Port `map101` in `godot` mode as the reference map workflow, then iterate on
  the remaining fidelity gaps (P31/P33 environment vector, utility-mask
  polarity, partial decals) — `godot` may diverge freely from `pbr`.
- Decode the P31/P33 RSX blend formula and utility-mask polarity instead of
  exposing them as manual parameters; where an effect is not reproducible as
  PBR, keep it in the `godot` shader path.

### 5. Export animations in the GLB

- Establish the scalar-curve → joint → transform binding for the full skeleton
  (not just the root).
- Understand the descriptor modes `0..4` and the prefix-coded 247/249/251/253
  layouts.
- Validate against known clips (for example `turn_180_*`) and promote the
  verified channels out of the experimental flags.
- Add a golden test on a short clip.

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
