# Majin and the Forsaken Kingdom resource extraction

This project extracts the assets of **Majin and the Forsaken Kingdom** (PS3,
HexaEngine) from the game's `.pak` archives and converts the proprietary
formats into **the most generic, engine-agnostic formats possible**, so the
recovered assets can be reused for any purpose. The primary target is **Godot
4**, which drives the concrete choices (glTF/GLB for geometry and animation,
PNG for textures), but nothing in the extraction or conversion pipeline depends
on a single engine.

## Goals

1. **Generic assets first.** Prefer open, well-documented formats (GLB, PNG,
   glTF 2.0 materials) over engine-specific artifacts.
2. **Godot 4 as the reference consumer.** When a format decision is ambiguous,
   favour what imports cleanly into Godot 4 (glTF 2.0, PBR metallic-roughness).
3. **No engine-specific reconstruction.** Assets whose meaning only exists
   inside HexaEngine (the procedural day/night sky, some bespoke shaders) are
   *not* reverse engineered. They are meant to be reproduced with the host
   engine's own primitives (for example a native Godot `Sky`), so the pipeline
   stays generic.
4. **Evidence over guessing.** Every reverse-engineering result is documented
   with its confidence. Unproven values are exposed as editable parameters or
   omitted rather than silently guessed.

## Project layout

```text
tools/
  extraction/
    pak_extractor.py       PAK archive extraction
  conversion/
    xet_to_png.py          XET textures to PNG
    dds_to_png.py          DDS textures (DXT1/DXT5) to PNG
    ddm_to_3d.py           DDM → GLB entry point (re-export shim)
    ddm/                   DDM decoder package
      binary.py            big-endian reads and packed-attribute decoders
      materials.py         material parsing, textures, PBR estimates
      geometry.py          topology, submesh descriptors, map sections
      skinned.py           skinned character skeleton/skin decoding
      scene.py             model/scene decoding and GLB export
      cli.py               command-line interface
    glb_export.py          low-level static and skinned GLB writer
    godot_export.py        Godot 4 native/custom material assets
    blend_mask.py          submesh seam blend-map generation
    material_pbr_estimator.py  Reserved matcap/mask-to-PBR research API
    motion/                Generic compact TRS animation decoding and baked IK
  research/
    shader_inspect.py      PS3 shader sampler-binding inspection
    rsx_fp_disasm.py       RSX fragment program disassembler
research/
  legacy/                  archived prototypes and historical documents
REVERSE_DDM.md             current DDM reverse-engineering notes
MAP_BINARY_FORMATS.md      map folder formats and detailed map101 inventory
MATERIAL_PBR.md            matcap/mask-to-PBR research plan
ROADMAP.md                 project action plan and current status
game_files/                ISO, PAK, and extracted resources (ignored by Git)
output/                    local generated results (ignored by Git)
```

Maintained entry points live exclusively under `tools/`. Files under
`research/legacy/` are retained for research purposes and are not part of the
supported pipeline. The action plan and current status are tracked in
[`ROADMAP.md`](ROADMAP.md).

## Installation

```powershell
python -m pip install -r requirements.txt
```

## Pipeline

The source path below reflects one possible local layout. Replace it with the
`package` directory from your mounted or extracted ISO when necessary.

```powershell
# 1. Extract every PAK archive
python tools/extraction/pak_extractor.py "game_files/ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package" --out game_files/DECOMPRESSED_ALL

# 2. Convert XET textures
python tools/conversion/xet_to_png.py game_files/DECOMPRESSED_ALL --out output/textures --recursive

# 3. Convert DDS textures
python tools/conversion/dds_to_png.py game_files/DECOMPRESSED_ALL --out output/textures --recursive

# 4. Convert one DDM model (the undecorated file is the skinned character)
python tools/conversion/ddm_to_3d.py game_files/DECOMPRESSED_ALL/KB/chara/chr300/chr300 output

# Or scan a resource tree while preserving its relative directory layout
python tools/conversion/ddm_to_3d.py game_files/DECOMPRESSED_ALL output/models --recursive --final
```

The extractor reuses a decompression thread pool across archives. Set the number
of threads with `--workers N`. For a full extraction, `--quiet` avoids printing
one line per resource and reduces terminal overhead.

PAK resources are matched to their names through the archive's indexed
`0x120`-byte records, not by pairing a list of names with whichever deflate
streams happen to decompress successfully. This distinction matters for
archives such as `chr100.pak`: it contains 14 XET textures followed by a
677,059-byte skinned DDM named `KB/chara/chr100/chr100`. A failed or empty
stream must not shift the remaining names. The extractor also preserves
same-name resources by keeping the DDM at its undecorated path and adding a
`.crg` suffix to the character-rig graph.

Outputs made with the former positional stream-matching extractor should be
regenerated. A telltale stale `chr100` extraction has a small `\x00CSR` file at
`KB/chara/chr100/chr100` and no `chr100_c02`; a correct extraction has a
`\x00ddm` file at that main path and all 14 named XET files.

Block encoding `1` is raw deflate; encoding `0` is an uncompressed stored
block. The latter is used for the nested `fspe_jpn` and `fspe_rus` language
PAKs. Indexed paths normally begin with `KB/`, but `static.pak` also contains
the valid `system/shader/HxMaterial` resource.

Recursive texture conversion preserves the relative directory structure so
resources with identical names do not overwrite each other.

Compiled PS3 shaders extracted from `static.pak` can be inspected directly.
The report includes uniforms, sampler hardware units, and the real fragment
program range. JSON output also includes the inline constant patch locations as
`fragment_constant_offsets`. `--dump-programs` writes RSX fragment bytecode
suitable for a disassembler:

```bash
python tools/research/shader_inspect.py game_files/decompressed/KB/shader/KbBaseP28_L1
python tools/research/shader_inspect.py game_files/decompressed/KB/shader/KbBaseP28_L1 \
  --dump-programs output/shaders
```

DDM conversion supports geometry, UVs, normals, materials and embedded textures
for the implemented DDM v3 layouts. Supported character files also include their
skeleton and skin. Skeleton storage order is decoded from the DDM itself; a
motion package is not required to recover its bind pose. Unsupported geometry
variants are reported as `[UNSUPPORTED]`, separately from corrupt data.

### Animated models and Godot 4

In VS Code, run **DDM: export model with animations** for one model, or
**Godot: export selected folder** for a recursive export. Select the input path,
output directory, animation names/globs (default `all`) and IK mode (default
`bake`). The decoder follows binary structure, without character-name branches.

```bash
python tools/conversion/ddm_to_3d.py PATH_TO_DDM output/decoded \
  --animations all --ik bake --scale 0.01 --final
python tools/conversion/export_folder_to_godot.py PATH_TO_FOLDER_UNDER_KB \
  --godot-project PATH_TO_GODOT_PROJECT
```

`--animations` accepts `all`, `none`, comma-separated names/globs, or zero-based
physical curve-slot indices. For example, `--animations '*move*,*turn*'` selects
named locomotion states. Anonymous helper segments retain `segment_NNN` names.
Alias names declared by the sequence are preserved. Named states without a
matching standalone segment remain explicitly unresolved in the motion report.

The compact transform program is verified on **4,436 curve blocks across 193
packages**. It resolves variable-width translation/rotation/scale groups in
skeleton order. Explicit rotations are absolute local XYZ Euler values in
radians (`Rz Ry Rx`), independently checked against DDM bind quaternions.
Linear and Hermite source curves are sampled at the measured 30 Hz clock;
glTF interpolates those samples. IK chains come from parent/flag metadata and
are calculated into ordinary glTF joint rotations, so playback requires no
Godot IK addon. `--ik off` exports the FK comparison.

Each animated model writes `<name>.glb`, `<name>.glb.import` and `<name>.motion.json`, including with
`--final`. The latter records source slots, named-state bindings, rejected or
missing states, IK residuals/clamped targets and unresolved secondary controls.
When exporting into a Godot project, its saved default scene import preset is
applied as defined, including embedded-image handling and the material import
script. Existing resource UIDs are retained. Outside a project with an explicit
preset, the `.glb.import` companion disables animation simplification that would
otherwise move baked IK endpoints, preserving unrelated import settings.
The larger `analysis.json` is omitted with `--final`. Positions are scaled by
0.01 to meter-like units; `--scale` overrides this.

**Remaining limits:** secondary rig controls and exact source-engine IK
pole/twist/stretch rules still need verification. Composite qstm states and
several geometry variants, including the rigid gim103 DDM, remain unsupported.
The pipeline now exports full skeletal motion on supported character layouts;
complete fidelity for every game model is still a research goal.

Validate an export in an actual Godot 4 project, either with the VS Code launch
**Godot: validate animated GLB**, or:

```bash
python tools/validation/check_godot_animation.py PATH_TO_GLB --godot PATH_TO_GODOT
```

This uses a temporary project and the normal editor importer, then samples each
animation's start/middle/end, checks finite poses and compares imported IK
endpoints to the source targets with the documented reach limits. The adjacent
motion report and original source package must remain available for this check.

Research details: [REVERSE_MOTION.md](REVERSE_MOTION.md),
[CURRENT_WORK.md](CURRENT_WORK.md), and
[the compact-program evidence](research/TRANSFORM_LAYOUT_FINDINGS.md).
The former `motion_decode.py` prototypes and their anatomical mappings are
archived under `research/legacy/`; old experimental animation overrides are
retired. Run the maintained inspector with `python -m tools.conversion.motion`.

### Material output modes

`--material-mode` selects between two material strategies:

- **`pbr`** (default) — a portable glTF 2.0 material only. Legacy Phong is
  converted to PBR metallic-roughness, a reflection matcap may refine the
  roughness, and legacy specular color is carried by `KHR_materials_specular`.
  The GLB is self-contained and imports into any glTF 2.0 viewer or engine.
- **`godot`** — faithfully reconstructs the original look for **Godot 4**.
  It keeps the same portable PBR material in the GLB *and* additionally writes
  external PNG textures, native `StandardMaterial3D` resources for ordinary
  PBR materials, custom shaders only for unsupported multi-texture effects, and a
  `materials/material_bindings.json` manifest. Use this mode whenever a PBR
  approximation is not enough (smooth submesh transitions, matcap/environment
  lookups, the utility mask) and you want a guaranteed-faithful Godot 4 result.
  `original-godot` is accepted as a deprecated alias of `godot`.

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr300/chr300 output \
  --material-mode godot --final
```

The `godot` mode adds one ready-to-use Godot `Material` `.tres` per DDM
material, external PNG textures, and `materials/material_bindings.json`. After
importing the GLB in Godot, assign the matching `.tres` listed for each DDM
material in the manifest.

### Godot utility assets

The custom multi-texture/foliage shaders and material-assignment helper are versioned in
[`godot/utility/`](godot/utility) (see [`godot/README.md`](godot/README.md)).
They are **not** regenerated on every export. Ordinary opaque and alpha-blend
families use `StandardMaterial3D`. Double-sided cutout foliage uses
`majin_foliage.gdshader` to correct back-face normals; other decoded features
outside the native model use `majin_multitexture.gdshader`.
Install the whole utility folder once into your Godot project.

A GLB cannot reference external Godot resources, so the imported model starts
with `StandardMaterial3D` on every surface. `godot/utility/assign_materials.gd`
is an `EditorScenePostImport` **import script**: set it as the **Import Script**
of the `.glb` in the Import tab, and it assigns the generated `.tres` files by
material name from `material_bindings.json` on every import/reimport.

`reflection_strength` and `invert_utility` are
exposed because the P31/P33 sampler bindings are proven but their exact RSX
blend formula and mask polarity are not yet decoded.

### Multipass detail layers (Godot)

Map materials often paint a second texture onto the same surface the base
material already covers — for example `gake102__multi` sits on exactly the same
geometry as `gake102__base`, using a different texture (rock over ground). This
is the "textures painted on the 3D" effect: one surface, two texture sets.

The portable `pbr` mode cannot express this, so it collapses exact duplicates to
avoid z-fighting. The `godot` mode instead **folds the overlay into its host as
a second detail layer**: the host `.tres` gains `detail_texture`,
`detail_normal_texture` and a `detail_strength`, and the generated shader mixes
both texture sets in one draw. When a material's faces are *entirely* covered by
a larger host material with matching UVs, the overlay is detected automatically
(100% coverage). Its vertex alpha is transferred to the host as the authored
paint mask, and the coplanar overlay primitive is omitted from the Godot GLB.
The fold is recorded in `material_bindings.json` under `detail_folds`.
This is where the two modes diverge: `godot` reproduces the layered look, `pbr`
stays a portable single-texture approximation.

### Smooth terrain paint transitions

Map101 stores its paint weights in the alpha byte of `COLOR_0`. For example,
the outer vertices of `tikeikusa_` are mostly alpha 0, its interior is mostly
255, and intermediate values form the soft edge. Godot's native alpha-blend
material consumes that interpolated source alpha directly instead of clipping
it or guessing a transition from spatial proximity. `zimenA_` and
`ALFsyokubutu_` use the same mode. `zimen_` is explicitly opaque in the DDM;
its vertex alpha must not turn the whole surface transparent.

The older UV-space proximity blend-map reconstruction remains available in the
code for DDM variants with no source alpha masks. It is automatically disabled
for a model such as map101 where authored vertex alpha exists, preventing a
second heuristic blend from being applied over the original paint weights.

For maps, `--object-mode auto` (the default) creates one selectable GLB node per
connected geometry component, joining exact shared edges across material/UV
seams while preserving vertex attributes. Other models remain one object with
multiple material primitives. Each node has a local origin at its component's
bounding-box center and a translation preserving its placement. Exactly
identical exported local meshes share mesh data between nodes.

Reusable models below `KB/instance` are the exception: their source origin is
preserved in the mesh because HSC placement transforms are authored relative
to that pivot. Their GLB node therefore has zero translation. This also allows
`assign_materials.gd` to extract the mesh without losing a vertical or lateral
pivot offset.

This reconstructs editable objects from baked geometry; it does **not** recover
original authoring instances or their pivots. Disconnected pieces of one prop
can become separate objects, and connected props can remain together. Use
`--object-mode submeshes` for DDM descriptor groups, `connected` to explicitly
split any model, or `single` to keep one object. These modes apply to GLB only.
Legacy Phong materials are converted to PBR values while their original values
remain in GLB extras. Roughness is derived by matching the DDM Blinn-Phong
half-power width to Godot/glTF GGX; neutral `Ks` controls relative dielectric
specular strength and `Kd` modulates albedo. In PBR
mode, a reflection matcap overrides that fallback with documented roughness and
confidence metadata based on highlight coverage. Matcaps do not determine
metalness: without stronger evidence, materials remain dielectric and legacy
specular color is carried by `KHR_materials_specular`.
The decoded eleven-float block is diffuse RGBA, two unknown scalars, specular
RGB, one unknown scalar, and the legacy exponent; it does not contain an
independent ambient RGB color or a native PBR roughness/metalness value.
`--roughness` remains available as an explicit diagnostic override. Exact coincident normal-only
passes are omitted when a diffuse surface occupies the same triangle, avoiding
the z-fighting produced by legacy multipass terrain. Auxiliary shader textures
are not mapped.

Texture-role inference is provisional and combines a filename-suffix hint with
image content statistics; suffixes are correlations, not a decoded contract.
The classifier reads `*_c*` (base-color candidate), `*_n*` (normal),
`*_f*` (view-dependent matcap/reflection) and `*_u*` (utility mask), then
confirms or vetoes each hint against the decoded pixels: a strongly blue image
is a normal map even without a suffix, and a matcap suffix always excludes the
image from albedo. Every texture records a `role_source` field
(`suffix`, `content`, `suffix+content`, `content+suffix`, `size_fallback`,
`demoted`, or `unresolved`) in `analysis.json` so the decision is auditable.

Shader reflection identifies `chr300_u01` as `textureSamplerUtil`, but its
scalar operation is not yet decoded and it must not be treated directly as glTF
roughness or metallic data. Small `*_f*` images can be view-dependent
matcap/reflection lookups; core glTF 2.0 has no matcap material model, and
treating such a lookup as albedo is incorrect, so `*_f*` references are always
excluded from albedo and `*_c*` references take priority as base color,
including small swatches such as `chr300_c01`. The PBR approximation and its
limits are documented in [`MATERIAL_PBR.md`](MATERIAL_PBR.md).

DDM normal textures use the DirectX-style tangent-space Y− convention observed
in the original assets. When a texture is classified as a normal map, the DDM
exporter inverts its green channel before embedding it in GLB or writing the
Godot material assets. The resulting PNG follows the Y+ convention expected by
glTF 2.0 and Godot 4. Standalone `xet_to_png.py` conversion remains lossless and
does not alter channels because it has no material-role context.

```bash
python tools/conversion/ddm_to_3d.py game_files/decompressed/KB/map/map101 output/models_glb --final --texture-root game_files/decompressed/KB/texture/common/area1
```

### Map instance tables to Godot foliage

`hsc_to_foliage.py` converts a map's HSC `*_ins` table directly to a Godot 4
`FoliageSceneData` text resource. It preserves each instance transform and
model in the same coordinate axes as the exported map, converts source
positions from centimetre-like units to metres, and maps the source culling
distance to `visibility_ranges`:

```bash
python tools/conversion/hsc_to_foliage.py game_files/decompressed/KB/map/map101/map101_ins output/foliage/map101_foliage.tres
```

The defaults target these project resources:

```text
res://addons/procedural_tools/foliage/resources/foliage_data.gd
res://addons/procedural_tools/foliage/resources/foliage_scene_data.gd
res://terrain/foliage/meshes/{model}.res
```

The resulting map101 resource contains 332 entries referencing `ins107.res`
through `ins111.res`. Those five DDMs are exported as one GLB mesh each
automatically because the default `auto` mode recognizes `KB/instance`. Use
`godot/utility/assign_materials.gd` as their Godot Import Script: it assigns
the generated materials to every model, but saves a reusable `.res` mesh only
when that model's manifest declares `asset_kind: instance`.
For a different naming convention, pass `--mesh-path-template`, which must
contain `{model}`. Script paths, Euler order, position scale and coordinate
conversion also have explicit CLI overrides; see `--help`.

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
