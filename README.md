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
    ddm_to_3d.py           DDM models to GLB (glTF 2.0)
    glb_export.py          low-level static and skinned GLB writer
    godot_export.py        Godot 4 shader + ShaderMaterial assets
    blend_mask.py          submesh seam blend-map generation
    material_pbr_estimator.py  Reserved matcap/mask-to-PBR research API
    motion_decode.py       Character motion discovery and track decoding
  research/
    shader_inspect.py      PS3 shader sampler-binding inspection
    rsx_fp_disasm.py       RSX fragment program disassembler
research/
  legacy/                  archived prototypes and historical documents
REVERSE_DDM.md             current DDM reverse-engineering notes
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
unsupported variants without creating per-file output directories. Empty
directories created before a later conversion error are pruned, while nonempty
output is retained. Findings are recorded in
[`REVERSE_DDM.md`](REVERSE_DDM.md).

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

For focused `chr300` experiments, selected scalar triples can be interpreted as
absolute extrinsic XYZ Euler angles. Implicit components retain their bind-pose
angle. This interpretation preserves the head and torso in the initial test,
but the mask, arms, lower body, and tentacle chains prove that it is not the
complete rig binding. In particular, treating positions `5 + 4*j .. 7 + 4*j`
as Euler values and ignoring the fourth position is only a working hypothesis,
not a decoded format. `--experimental-rotation-units auto` is retained for
reproducing that experiment; its radians/degrees split is not established.
The command writes a separately named `_motion_experimental_auto.glb`:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr300/chr300 output \
  --animation-clips 0 --experimental-root-motion \
  --experimental-rotation-joints 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61 \
  --experimental-rotation-units auto
```

The state-sequence table supplies original clip names. For example,
`motion_000` is `c300_0000_boredom_01_00`; these names are preserved in the
analysis report and exported glTF animation.

An attempted three-scalars-per-joint prefix layout was rejected after visual
testing: it rotates the torso by approximately 90 degrees around X and then Y
and propagates that error to the head and tentacles. It is not available as a
converter option. Cross-character comparison also shows that descriptor counts
are rig-dependent, so the numerical identity `253 = 5 + 4*62` must not be
generalized to other characters.

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

Godot 4 reconstruction assets can be emitted alongside the PBR fallback:

```bash
python tools/conversion/ddm_to_3d.py \
  game_files/decompressed/KB/chara/chr300/chr300 output \
  --material-mode original-godot --final
```

This mode adds `materials/majin_original.gdshader`, one ready-to-use
`ShaderMaterial` `.tres` per DDM material, external PNG textures, and
`materials/material_bindings.json`. After importing the GLB in Godot, assign
the matching `.tres` listed for each DDM material in the manifest.
`reflection_strength` and `invert_utility` are
exposed because the P31/P33 sampler bindings are proven but their exact RSX
blend formula and mask polarity are not yet decoded.

### Smooth submesh transitions (blend maps)

The original game assigns one material per submesh, which produces hard seams
where two submeshes meet. The real renderer hides those seams with a shader
blend that cannot be recovered from the exported geometry alone: the RSX
fragment program may use a mask texture, a derivative-based stencil, or
per-fragment arithmetic that no longer exists in the DDM data. The
`original-godot` mode therefore reconstructs an *approximation* of the smooth
transition and bakes it into a UV-space texture.

When `--material-mode original-godot` is used and the DDM contains more than
one material, the exporter measures, for every vertex, how close it is to
faces belonging to other materials. Vertices within `--blend-radius` metres of
a foreign face receive a weight proportional to the linear falloff to that
face's centroid. The owner material always keeps at least half the total
weight so a vertex does not drift toward an unrelated neighbour. These weights
are packed into an RGBA texture (`materials/blend_NN.png`, 256×256) indexed by
the vertex UV, and the generated `.gdshader` samples it to interpolate between
up to four base-color textures:

- channel `R` weights the owner's `base_texture`;
- channels `G`, `B`, `A` weight `blend_base_texture_1/2/3`, which the manifest
  binds to the *neighbouring* materials actually present in range (not to the
  owner's own textures);
- texels that no vertex maps to fall back to the owner-only weight, so the
  single-material fast path is unchanged.

The blend map is heuristic. It reproduces the visible effect (no hard color
seam at a submesh boundary) without claiming to replicate the original RSX
arithmetic. Treat the generated textures and the `use_blend` switch as an
editable starting point, not as recovered source data. The following limits
apply:

- Vertex weights are baked per-texel in UV space. If two vertices share the
  same UV but belong to different owners, their weights are averaged; this is
  common in the DDM's non-atlased UV layout.
- The blend is purely color-based. It does not affect normals, roughness, or
  the utility/matcap paths, so a seam that is visible in specular response but
  not in albedo will not be hidden.
- Larger `--blend-radius` values (the default is `0.05`, roughly five source
  units at the default `0.01` scale) produce wider transitions but can pull
  small isolated submeshes toward their surroundings. Smaller values keep the
  blend tighter but may miss narrow suture bands.
- Blending is skipped entirely for a material whose submesh has no foreign
  face within range. In that case `use_blend` stays `false` and the material
  uses the unchanged single-texture path.

Geometry that triggers blending is required: passing `--material-mode
original-godot` on a DDM where every material is spatially isolated produces
zero blend maps (`blend_map_count = 0` in the analysis report) and all
materials keep `use_blend = false`.

For maps, `--object-mode auto` (the default) creates one selectable GLB node per
connected geometry component, joining exact shared edges across material/UV
seams while preserving vertex attributes. Other models remain one object with
multiple material primitives. Each node has a local origin at its component's
bounding-box center and a translation preserving its placement. Exactly
identical exported local meshes share mesh data between nodes.

This reconstructs editable objects from baked geometry; it does **not** recover
original authoring instances or their pivots. Disconnected pieces of one prop
can become separate objects, and connected props can remain together. Use
`--object-mode submeshes` for DDM descriptor groups, `connected` to explicitly
split any model, or `single` to keep one object. These modes apply to GLB only.
Legacy Phong materials are converted to PBR values while their original values
remain in GLB extras. Roughness is derived from DDM Phong shininess. In PBR
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

Texture-role inference is still provisional for character materials. Shader
reflection identifies `chr300_u01` as `textureSamplerUtil`, but its scalar
operation is not yet decoded and it must not be treated directly as glTF
roughness or metallic data. Small `*_f*` images can be
view-dependent matcap/reflection lookups; core glTF 2.0 has no matcap material
model, and treating such a lookup as albedo is incorrect. The current generic
classifier therefore excludes `*_f*` references from albedo and gives `*_c*`
references priority as base color, including small color swatches such as
`chr300_c01`. The PBR approximation and its limits are documented in
[`MATERIAL_PBR.md`](MATERIAL_PBR.md).

DDM normal textures use the DirectX-style tangent-space Y− convention observed
in the original assets. When a texture is classified as a normal map, the DDM
exporter inverts its green channel before embedding it in GLB or writing the
Godot material assets. The resulting PNG follows the Y+ convention expected by
glTF 2.0 and Godot 4. Standalone `xet_to_png.py` conversion remains lossless and
does not alter channels because it has no material-role context.

```bash
python tools/conversion/ddm_to_3d.py game_files/decompressed/KB/map/map101 output/models_glb --final --texture-root game_files/decompressed/KB/texture/common/area1
```

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
