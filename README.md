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
    motion_decode.py       Character motion discovery and track decoding
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

DDM conversion remains experimental. It exports GLB scenes with geometry,
UVs, normals, vertex colors, material estimates and embedded XET textures for
supported DDM v3 layouts. Character DDMs also export their skeleton, skinning
weights and bone hierarchy. Their animations live in separate proprietary
`motionSequence`/`motionPackage` resources; the converter detects and reports
those clips and validates their constant, linear and tangent scalar curves.
The root translation and Y-axis heading bindings are established. Joint rotation
research is available behind explicit experimental options; it is not enabled by
default. Those options are diagnostic and do not yet produce a correctly posed
full character animation. Recursive scans skip non-DDM files and report
unsupported variants without creating per-file output directories. A DDM that
is valid but uses a layout the converter does not implement is reported as
`[UNSUPPORTED]` with the detected variant family, its raw version word, and the
reason (for example `v3 (map/static + skinned character), version=3: no submesh
descriptor found for this layout`). These differ from `[ERROR]` lines, which
flag genuinely corrupt, truncated or unreadable files. Empty directories
created before a later conversion error are pruned, while nonempty output is
retained. Findings are recorded in [`REVERSE_DDM.md`](REVERSE_DDM.md).

Motion parsing is isolated in `motion_decode.py`. It can inspect a character
independently, while `ddm_to_3d.py` calls its API for skinned characters and
passes proven channels to the GLB exporter. The exporter already accepts
translation, rotation and scale channels; identifying the curve-to-joint and
transform conventions remains the missing stage.

For focused motion research, dump complete decoded tracks for a few clips:

```bash
python tools/conversion/motion_decode.py \
  game_files/decompressed/KB/chara/chr300/chr300 \
  --clips 0,1,2 --dump-tracks output/chr300-motion-tracks.json
```

Scalar positions can also be compared across several named motions. This is
useful because the smaller 247/249/251 descriptor tables are prefix-coded
variants of the 253-position `chr300` table rather than independent layouts:

```bash
python tools/conversion/motion_decode.py \
  game_files/decompressed/KB/chara/chr300/chr300 \
  --clips 0,7,8,15,20,38,40,42,44,46,78,109 \
  --dump-layout output/chr300/motion_layout_compare.json
```

Use `--all-clips` with `--dump-layout` to compare all 150 clips. Nonzero bytes
retained in key-table alignment gaps are reported but are not rejected; after
accounting for this writer behavior, every `chr300` scalar segment decodes to
its exact boundary.

An opt-in first animation milestone exports the validated root translation and
rotation of selected `chr300` clips. Scalar 0 contains an extracted Y heading
in radians, scalar 1 is reserved zero, scalars 2–4 contain translation XYZ,
and scalars 5–7 contain the root's local XYZ Euler rotation. The exporter
composes the extracted heading with that local rotation into one glTF channel:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr300/chr300 output \
  --animation-clips 0 --experimental-root-motion
```

This composition reconstructs the full ±180-degree change in the
`turn_180_*_a` clips: extracted and local Y rotations each contribute roughly
90 degrees. The `turn_90_*` clips do not contain a persistent 90-degree root
change. Their names describe the gameplay action, while entity orientation is
apparently supplied by state/gameplay logic outside the scalar clip. Per-clip
initial/final root transforms are recorded in `analysis.json` so such
non-standalone motions can be identified without relying on their names.

Cross-character comparison invalidated the former contiguous joint-suffix
interpretation. After the eight verified root scalars, related humanoid rigs
store ordered three-scalar transform groups for both skeleton joints and
auxiliary rig controls. `chr302` has 66 groups for 42 non-root joints, leaving
24 auxiliary groups; `chr303` inserts exactly 20 groups when its 20 additional
bones appear; `chr301` has 30 auxiliary groups. The controls are interleaved by
anatomical segment, not collected in a prefix or suffix. Within each observed
humanoid segment, joint triplets follow skeleton order and are followed by a
fixed control block: 3 controls after the trunk and each arm, 1 after the
pelvis, and 7 after each leg. `chr301` additionally has 6 controls after its
accessory chain. The experimental humanoid export now uses this structural
binding and emits rotations for every skeleton joint needed by the hierarchy;
only auxiliary rig-control triplets outside the skeleton are omitted. The Euler space/order interpretation remains
experimental, while root motion remains independently available.
The 332/334/336/338 layouts (and their chr302/303 equivalents) also contain
optional two-scalar controller sections rather than a simple truncated suffix;
their indices are remapped before binding. Cross-character equality identifies
model-space IK targets for both wrists and ankles. The opt-in
`--experimental-humanoid-ik-mode bake` probe bakes those targets through
two-bone chains instead of interpreting their position values as Euler angles.
Use `--experimental-humanoid-ik-mode godot` to preserve the decoded FK curves
and export four controls for a Godot runtime solver. The triplet following each
target can be disabled with `--experimental-ik-target-orientation none`. Its
default `source-row` interpretation treats it as an absolute Euler orientation
using the same row-vector-to-Godot transpose established for HSC instances,
then cancels the selected reference pose. Its exact semantics remain
experimental. The legacy `--experimental-humanoid-ik` and
`--experimental-export-ik-targets` flags remain aliases for `bake` and `godot`.
`--experimental-rotation-units auto` is retained for comparison. The bound
joint curves use radians by default: their extrema repeatedly land near
multiples of pi, consistently with the verified root Euler channels.

`--experimental-rotation-axes` keeps the stored `xyz` component order and can
emit separately named axis-permutation probes. Structurally bound humanoid
triplets are composed as Euler deltas in each joint's local bind space. The
default model is `bind_local * delta`; `--experimental-rotation-model` can emit
the reversed composition as a separately named comparison probe.
`--experimental-rotation-signs` independently controls the source-component
signs. The `zyx` coordinate-basis probe uses `---` to account for the odd
X/Z permutation's handedness change.

The earlier `--experimental-controller-bake` probe is retained only for code
comparison. It cannot repair the newly established interleaved layout and is
not reached by automatic joint export.

The command writes a separately named `_motion_experimental.glb`:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr300/chr300 output \
  --animation-clips 0 --experimental-root-motion \
  --experimental-rotation-joints 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61 \
  --experimental-rotation-units degrees
```

For the 81-joint humanoid `chr301`, export a first complete test without
spelling out every joint index:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr301/chr301 output \
  --animation-clips 0 --experimental-root-motion \
  --experimental-rotation-joints all \
  --experimental-rotation-units degrees
```

For reference-pose diagnostics, a clip endpoint can be cancelled before the
Euler curves are composed with the local bind pose. The root heading can also
be excluded when it duplicates the root joint's local Y rotation:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr301/chr301 output \
  --animation-clips 0,2,3,8 --experimental-root-motion \
  --experimental-root-rotation-source local \
  --experimental-rotation-joints all \
  --experimental-rotation-reference-clip 8 \
  --experimental-rotation-reference-frame end
```

The state-sequence table supplies original clip names. For example,
`motion_000` is `c300_0000_boredom_01_00`; these names are preserved in the
analysis report and exported glTF animation.

Earlier four-scalars-per-joint and contiguous three-scalars-per-joint layouts
were both rejected. Descriptor counts are rig-dependent, and arithmetic
identities such as `253 = 5 + 4*62` or `338 = 95 + 3*81` do not establish a
scalar-to-joint binding.

`chr300.crg` is a separate character-rig graph. Its eight fixed 128-byte
records are now exposed in `analysis.json`, including referenced bone IDs,
float parameters, and flags. Values such as `-30`, `25`, `35`, `70`, `90`, and
`120` may describe controller limits, physical constraints, or collision
volumes; `.crg` has not yet been proven to drive animation. The final motion
segment is decoded separately as 150 variable-size per-clip metadata records.
Its leading flags and event bytes correlate with motion classes and events,
not with a global scalar-to-joint binding.

Each source produces `<name>/<name>.glb`. With `--final`, a fresh PBR output
folder contains only the self-contained GLB. Omit it for the JSON diagnostics
used during reverse engineering. Positions are
scaled by `0.01` to convert the observed centimeter-like units to meters;
use `--scale` to override this.

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
`import_instance.gd` to extract the mesh without losing a vertical or lateral
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
`godot/utility/import_instance.gd` as their Godot Import Script: it assigns the
generated materials to the mesh surfaces and saves them as `ins107.res`, etc.
For a different naming convention, pass `--mesh-path-template`, which must
contain `{model}`. Script paths, Euler order, position scale and coordinate
conversion also have explicit CLI overrides; see `--help`.

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
