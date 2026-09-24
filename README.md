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

# 4. Convert a known DDM model
python tools/conversion/ddm_to_obj.py game_files/DECOMPRESSED_ALL/KB/chara/chr300/chr300_c01 output
```

The extractor reuses a decompression thread pool across archives. Set the number
of threads with `--workers N`. For a full extraction, `--quiet` avoids printing
one line per resource and reduces terminal overhead.

Recursive texture conversion preserves the relative directory structure so
resources with identical names do not overwrite each other.

DDM conversion remains experimental. It currently exports OBJ geometry, UVs,
MTL materials, and PNG textures for the layouts identified so far. Do not run it
blindly against every DDM file yet. Known findings and limitations are recorded
in [`REVERSE_DDM.md`](REVERSE_DDM.md).

Equivalent configurations are available in VS Code. The
`Pipeline: extraction and textures` task runs PAK extraction followed by both
texture converters.
