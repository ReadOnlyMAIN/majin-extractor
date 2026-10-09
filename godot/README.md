# Godot utility assets

Reusable assets emitted by the DDM converter in `--material-mode godot`. They
are version-controlled here so an export never regenerates them and Godot
resources can reference a stable path.

| File | Purpose |
| --- | --- |
| `majin_multitexture.gdshader` | Custom entry point for decoded effects outside `StandardMaterial3D`, notably Map101's two-albedo/two-normal `zimen` variant and folded detail layers. |
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

## Experimental humanoid IK

Convert a humanoid with `--experimental-humanoid-ik-mode godot` in addition to
the experimental animation options. Unlike `bake`, this preserves the FK limb
rotations for Godot and adds four unweighted animated control
bones named `ik_hand_l_target`, `ik_hand_r_target`,
`ik_foot_l_target`, and `ik_foot_r_target`. Select the GLB in Godot's Import
tab, set the same `res://majin_utility/assign_materials.gd` as its Import
Script, and reimport it. The script assigns the character materials, then
creates `ModifierBoneTarget3D` adapters for four targets plus one `TwoBoneIK3D`
modifier after the animation system. Because that
solver ignores target rotation, a following `CopyTransformModifier3D` copies
only the target rotations onto the four end bones; their IK-solved positions
remain intact.

The maintained folder exporter enables all structurally bound rotations with
`local_delta_post`, radians, and bone_000's complete slots 5–7 rotation. These choices are
made while building the GLB; the Godot import script preserves them and does
not reinterpret FK axes. Large undeclared controller triplets remain omitted
instead of being treated as rotations or IK positions.

The `godot` IK mode does not run the offline two-bone solver and does not mark
any limb channel as `ik_baked`. It exports the original decoded FK channels
plus the four animated targets; `TwoBoneIK3D` is therefore the only IK solve.
Use the separate `bake` mode only when a portable GLB with no runtime solver is
required.

The four fallback pole offsets are expressed in the original DDM units. The
export manifest records `model_scale`, and the import script applies that same
factor before adding an offset to the scaled skeleton rest pose. With the
standard `--scale 0.01`, `(0,-50,25)` therefore becomes `(0,-0.5,0.25)` in
Godot units. Pole bones, if a future decoder exports them explicitly, continue
to take precedence over these synthetic fallbacks.

Elbows and knees use the same editable anatomical fallback poles.
By default, target orientations use the source engine's row-vector convention,
transposed into Godot space and made relative to the selected reference pose.
Use `--experimental-ik-target-orientation none` for the position-only control
comparison.

Nothing here is DDM-specific beyond the shader uniforms; the shader and the
import script can be reused across every converted model.

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
