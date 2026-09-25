# Majin and the Forsaken Kingdom resource extraction

This project extracts binary resources from the `.pak` archives found on a
mounted game ISO and converts proprietary formats into usable files.

## Project layout

```text
tools/
  extraction/
    pak_extractor.py       PAK archive extraction
  conversion/
    xet_to_png.py          XET textures to PNG
    dds_to_png.py          DDS textures (DXT1/DXT5) to PNG
    ddm_to_obj.py          DDM models to GLB (or OBJ/MTL/PNG)
research/
  legacy/                  archived prototypes and historical documents
REVERSE_DDM.md             current DDM reverse-engineering notes
game_files/                ISO, PAK, and extracted resources (ignored by Git)
output/                    local generated results (ignored by Git)
```

Maintained entry points live exclusively under `tools/`. Files under
`research/legacy/` are retained for research purposes and are not part of the
supported pipeline.

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

# 4. Convert one DDM model
python tools/conversion/ddm_to_obj.py game_files/DECOMPRESSED_ALL/KB/chara/chr300/chr300_c01 output

# Or scan a resource tree while preserving its relative directory layout
python tools/conversion/ddm_to_obj.py game_files/DECOMPRESSED_ALL output/models --recursive --final
```

The extractor reuses a decompression thread pool across archives. Set the number
of threads with `--workers N`. For a full extraction, `--quiet` avoids printing
one line per resource and reduces terminal overhead.

Recursive texture conversion preserves the relative directory structure so
resources with identical names do not overwrite each other.

DDM conversion remains experimental. It exports GLB scenes with geometry,
UVs, normals, vertex colors, material estimates and embedded XET textures for
supported DDM v3 layouts. Recursive scans skip non-DDM files and report
unsupported variants. Findings are recorded in [`REVERSE_DDM.md`](REVERSE_DDM.md).

Each source produces `<name>/<name>.glb`. With `--final`, a fresh output folder
contains only the self-contained GLB. Omit it for JSON/CSV and position-cloud
diagnostics. `--format obj` retains the former OBJ/MTL/PNG export. Positions are
scaled by `0.01` to convert the observed centimeter-like units to meters;
use `--scale` to override this.

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
Legacy Phong materials are approximated as nonmetallic PBR materials; original
Phong values remain in GLB extras. GLB roughness defaults to `0.8`, because the
Phong-derived values make map surfaces unrealistically glossy without the
original game shader. Set `--roughness 0.5` explicitly, or use a negative value
to restore the mathematical Phong conversion. Exact coincident normal-only
passes are omitted when a diffuse surface occupies the same triangle, avoiding
the z-fighting produced by legacy multipass terrain. Auxiliary shader textures
are not mapped.

```bash
python tools/conversion/ddm_to_obj.py game_files/decompressed/KB/map/map101 output/models_glb --final --texture-root game_files/decompressed/KB/texture/common/area1
```

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
