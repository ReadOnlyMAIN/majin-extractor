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
    ddm_to_obj.py          DDM models to OBJ/MTL/PNG
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

DDM conversion remains experimental. It exports OBJ geometry, UVs, legacy Phong
material values, and resolved XET textures for supported DDM v3 geometry
layouts. Recursive scans skip non-DDM files and report unsupported variants
without applying offsets tied to a reference model. Known findings and
limitations are recorded in [`REVERSE_DDM.md`](REVERSE_DDM.md).

Each exported mesh uses the source filename with an `.obj` extension. Final
mode keeps only that OBJ, its required `materials.mtl`, and resolved textures;
omit `--final` to also generate the position cloud, vertex/index CSV files, and
`analysis.json`. OBJ positions are multiplied by `0.01` by default to convert
the observed centimeter-like DDM coordinates to meters. Override this with
`--scale`, for example `--scale 0.1` when testing another unit hypothesis.

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
