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

### Player equipment (`chr1xx`)

Exporting any `KB/chara/chr1xx` folder with `export_folder_to_godot.py`
exports all available sibling `chr1xx` packages and creates `chara/chr1.tscn`.
Only `chr100` receives animations. Open or instantiate `chr1.tscn`, then select
`head`, `torso` (including arms), and `legs` in the Inspector. Selection also
works at runtime by assigning these properties. `available_armor()` lists
the eight visually identified clean armor package IDs;
`get_animation_player()` returns the chr100 player. The pairs below were
identified visually by the user, rather than decoded from game metadata:

| Clean armor | Package containing contamination |
| --- | --- |
| chr101 | chr100 |
| chr111 | chr110 |
| chr116 | chr115 |
| chr121 | chr120 |
| chr126 | chr125 |
| chr131 | chr130 |
| chr136 | chr135 |
| chr141 | chr140 |

Each slot loads the clean mesh from its armor package and the matching
`armor_*_darkness` mesh from its paired package. `health_ratio` controls a local-Y
alpha transition from clean (1) to covered (0), rising from feet to head.
`darkness_height_range` is in exported meters; `darkness_feather` controls the
soft edge. The height-to-health mapping is currently linear, an approximation
of the behavior reported by the user, not a recovered original shader.
The dedicated `player_darkness.gdshader` uses per-instance parameters so health
changes do not mutate shared materials or other characters. Install the updated
`majin_utility` shaders together with the controller and generated materials.
Packages chr145/146/150/151/155/156 remain exported for inspection but are excluded
from the equipment selector while their material appearance is unresolved.
The controller keeps each piece's Skin and transfers source deformation by
bone name through each rig's global rest matrices. Extra unmapped bones retain
their local rest pose; differing bind axes are preserved (notably the jaw).

The three GLB meshes (`armor_head`, `armor_torso`, `armor_legs`) are inferred
from summed triangle skin influences below bones 101 (head) and 4 (pelvis/legs),
with other bones assigned to the torso. This preserves every source triangle,
but the game's equipment/unlock table and exact clothing boundaries remain
unverified. Package names are used rather than guessed armor names. Mixed sets
may need boundary adjustments after visual comparison with the game.

The decoder now reads both rigid seven-attribute sections (24-byte vertices,
one palette index with implicit unit weight) and weighted eight-attribute
sections. In particular, chr100 contains 16 sections and 13,515 source vertices;
the old weighted-only detection started inside the group and read only 2,998.

Runtime verification in the connected Godot 4.8-dev7 project confirmed all eight
armor pairs and all 24 contamination slots. All 114 visible contamination
surfaces used `player_darkness.gdshader`. Health values 1, .75, .5, .25, and 0
produced matching height thresholds across the three slots; clean, half-covered,
and fully covered states were inspected visually. A mixed chr116/chr111/chr126
outfit followed the chr100 animation with no pose mismatch on shared bones.
The 477 animations were present; this does not validate every clip visually.

When changing an existing material's shader path manually, remove an old shader
`uid` from that external resource reference if it still identifies the previous
shader. Godot can otherwise resolve the UID to the old shader despite the new
path, leaving some contamination surfaces unaffected by the height cutoff.
Freshly generated materials use the correct shader path without a stale UID.


The jaw correction is documented in `research/PLAYER_CONTAMINATION_FINDINGS.md`.
The earlier equal-local-pose check was insufficient: chr101 jaw bone_120 has a
180-degree bind-axis difference from chr100. The controller now transfers
`source_global_pose * inverse(source_global_rest) * target_global_rest` and
converts back through the target parent's posed transform. The contamination
preview also compensates for animated vertical root motion. This remains an
approximation of the original KbTarModel effect, whose reflection explicitly
contains tarHP, tarColor, rootPos and a separate tar sampler.
