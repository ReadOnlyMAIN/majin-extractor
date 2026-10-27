# Godot utility assets

Reusable assets emitted by the DDM converter in `--material-mode godot`. They
are version-controlled here so an export never regenerates them and Godot
resources can reference a stable path.

| File | Purpose |
| --- | --- |
| `majin_multitexture.gdshader` | Custom entry point for decoded effects outside `StandardMaterial3D`, notably Map101's two-albedo/two-normal `zimen` variant and folded detail layers. |
| `majin_multitexture_cutout.gdshader`, `majin_multitexture_alpha.gdshader` | The same custom features with the decoded alpha-scissor or alpha-blend mode. Cutout materials retain their utility, normal and reflection inputs. |
| `majin_foliage.gdshader` | Albedo-only double-sided cutout foliage with corrected back-face normals. |
| `majin_material_common.gdshaderinc` | Shared reconstructed lighting, normal and detail-layer implementation. |
| `assign_materials.gd` | Universal `EditorScenePostImport` script: assigns generated materials on every model and reconnects IK when character target bones are present. |

The ordinary opaque and alpha-blend families are emitted as native
`StandardMaterial3D` resources. The decoded double-sided alpha-scissor foliage
family uses its small dedicated shader because native disabled culling does not
correct the lighting normal on back-facing fragments. Other custom shaders are
selected only for multiple texture pairs, folded detail passes, utility
lookups, or features the native material cannot express directly.

PBR values remain evidence-based conversions from the source legacy material:
`Kd` modulates albedo, `Ks` scales dielectric specular, `Ns` is matched to a
GGX lobe by half-power width, and metallic stays zero when the DDM provides no
conductor evidence. Map101's common `Ks=.8, Ns=32` template becomes specular
`.40`, roughness `.5520` by direct conversion; `zimen`'s `Ks=.1, Ns=64`
becomes `.05`, `.4709`. For rendering, the non-foliage map shader families use
a shared lower specular `.20`: this preserves sky/probe response without a
wet appearance or a reflectance seam at `zimen` transitions. The direct
`.40`/`.05` values remain recorded as `specular_from_phong`.
The dedicated double-sided alpha-scissor foliage family is albedo-only:
roughness `1` (smoothness `0`), specular `0`, and metallic `0`. Its shader
negates `NORMAL` when `FRONT_FACING` is false, so the visible back side reacts
to sunlight using the opposite geometric normal. The direct `.5520`/`.40`
Phong conversion remains recorded only as provenance.

## Installing into a Godot project

Copy this folder into the project so it lands at `res://majin_utility/`:

```text
<godot_project>/
  majin_utility/
    majin_multitexture.gdshader
    majin_multitexture_cutout.gdshader
    majin_multitexture_alpha.gdshader
    majin_material_common.gdshaderinc
    assign_materials.gd
```

Custom `.tres` files reference the shader under `res://majin_utility/`, so the folder name must be
`majin_utility` (or update `GODOT_UTILITY_DIR` in
`tools/conversion/godot_export.py` and re-export).

You can also generate the folder from the converter:

```python
from tools.conversion.godot_export import write_godot_utility
write_godot_utility("/path/to/godot_project/majin_utility")
```

## Assigning materials after import

A GLB (glTF 2.0) is a self-contained container and imports as
`StandardMaterial3D`; it cannot reference the external `.tres` resources.
`assign_materials.gd` is the single general-purpose **import script**
(`@tool extends EditorScenePostImport`) that fixes this automatically on every
import/reimport:

1. Import the converted `.glb` into your project (the tree Godot expects is
   `<model>/<model>.glb`, `<model>/materials/*.tres`,
   `<model>/textures/*.png`).
2. Select the `.glb` in the FileSystem dock, open the **Import** tab, enable
   **Import Script**, and pick `res://majin_utility/assign_materials.gd`.
3. Click **Reimport**. Godot runs `_post_import()` on the imported scene: it
   reads the sibling `materials/material_bindings.json` next to the model and
   assigns each matching `Material` to the mesh surfaces.
4. Unmatched names are reported as a `push_warning`; assigned surfaces are
   counted in the Output panel.

Because the script runs on every import, later reimports (texture changes, new
conversions) keep the materials assigned with no manual step. Use this same
script for maps, ordinary models, and characters; it detects character IK from
the exported control-bone names and otherwise performs no character-specific
work.

## Animated models and baked IK

Use the VS Code launch **DDM: export model with animations** or **Godot: export
selected folder**. Both use the generic compact TRS decoder with `--animations
all --ik bake` by default. Explicit Euler rotations replace local joint
rotations; they are not bind deltas. IK chains are read from skeleton flags,
then baked into regular glTF joint tracks. No runtime IK classes, target-bone
names, anatomical poles or extra import script are needed for playback.

Exports into a project with a saved default scene import preset use that preset
exactly. This includes the material import script, animation options and
embedded-image handling; the resource UID is retained. Use **Discard** for GLB
embedded images when the material import script already assigns the exported
`textures/` resources, avoiding another set of extracted PNGs next to the GLB.

Without an explicit project preset, the `<model>.glb.import` companion disables
animation simplification that moves baked IK endpoints and preserves unrelated
existing settings. The user's project preset takes precedence over those
precision recommendations.

The material post-import script can still assign shaders and textures. Its
legacy humanoid IK hook only applies to older GLBs containing synthetic target
bones; the current exporter does not create those bones and is not solved twice.

`<model>.motion.json` records residuals, unreachable targets, source-state names,
missing/composite states and secondary rig controls that remain unresolved. The
current geometric solver preserves the incoming FK bend plane; equality with
HexaEngine's exact twist, stretch and helper-control rules is not yet certified.

Use **Godot: validate animated GLB** in VS Code, or
`python tools/validation/check_godot_animation.py MODEL.glb --godot GODOT_PATH`,
to import in a temporary project and verify the animations and IK endpoints in
the real engine. See [REVERSE_MOTION.md](../REVERSE_MOTION.md) for evidence.

## Extracting reusable instance meshes

Models below `KB/instance` are kept as one mesh automatically in the default
`--object-mode auto`, even when foliage cards are disconnected. Their original
DDM pivot is retained instead of recentering the mesh on its AABB, since the
HSC transforms are relative to that source origin. Convert them with Godot
material output, preserving the source directory layout:

```bash
python tools/conversion/ddm_to_3d.py game_files/decompressed/KB/instance output/instances --recursive --final --material-mode godot --texture-root game_files/decompressed/KB
```

Use the same `assign_materials.gd` Import Script for those GLBs. The converter
tags their own `materials/material_bindings.json` manifest with
`asset_kind: instance`; the universal script then
expects exactly one `MeshInstance3D`, puts the matching generated materials
directly on its surfaces, and saves the result using the GLB filename. For
example, importing `ins107.glb` creates
`res://terrain/foliage/meshes/ins107.res`, matching the default paths emitted
by `hsc_to_foliage.py`.

### Player character (`chr1`)

Exporting a chr1xx folder collects the body families and chr72x/74x accessories
and creates `chara/chr1.tscn`. The character exposes:

- `cinematic_render` (boolean, **true** by default): cinematic body when enabled,
  gameplay body with its own tar when disabled.
- `body`: costume identity, stored as the cinematic asset ID (chr101, chr111,
  chr116, chr121, chr126, chr131, chr136, chr141, chr151, chr156, chr146).
  Gameplay resolves to chr100, chr110, chr115, chr120, chr125, chr130, chr135,
  chr140, chr150, chr155, chr145 respectively.
- `head`: none or an exported chr720..729 accessory.
- `bracelets`: none or chr740..749, with its two halves following separate forearms.

Both body modes use the complete geometry of their selected source model.
Gameplay never mixes cinematic base geometry with gameplay tar. Current exports
split only normal/tar render groups; old exports with head/torso/legs mesh names
also work, because the controller retains every source body mesh.

The hidden chr100 master keeps the 477 animation clips. Changing body or mode
preserves the AnimationPlayer, playback time, health and accessories. Visible
body rigs receive source deformation through their global bind matrices, which
preserves the cinematic jaw's different bind axes. Static accessories omit the
animated TR arrays in their two-node DDM rig. Head follows the head anchor;
bracelets are split by their authored positive/negative X geometry and follow
bones 13/43. This is a preview attachment reconstruction, not a recovered complete
cinematic cloth/accessory simulation.

The Animation Lab starts on chr1 when present. Its right panel exposes body,
head, bracelets and **Cinematic rendering**. Body labels follow the active family
and tooltips show both IDs. Health and tar appearance controls appear only in
gameplay mode. The panel is hidden for other characters; individual body and
accessory packages are excluded from the character dropdown.

Tar uses the decoded eight-pass equations, original vertex-red coverage and
global textures installed in `tar_globals/`. Flow defaults to .1 following the
user's visual observation. The health slider still supplies a preview PV→tarHP
mapping; `use_original_tar_hp` permits explicit source inputs. Per-player
materials, root-relative UVs, native Godot shadows and an explicit original-frame
lighting replay mode remain available. `darkness_depth_bias` is **zero** by
default; coherent geometry replaces the earlier 1 mm workaround.

See `research/PLAYER_MODEL_USAGE.md`, `research/TAR_SHADER_FORMULA.md` and
`research/TAR_RENDER_INTEGRATION.md` for evidence and remaining rendering limits.
Several late source assets contain test textures or previously unresolved
material settings; the mode switch does not claim to repair all those materials.

Checks: 184 Python tests; Godot integration checks cover all eleven costumes in
both modes, all twenty accessories, source texture availability and preservation
of the 477 animations. Shader equation validation covers 384 controlled GPU cases.

When changing an existing material's shader path manually, remove a stale shader
UID if it still resolves to the previous shader. Newly generated resources use
the intended path without such a stale reference.
