# Shared VS Code configuration

This directory contains a portable configuration for Windows, Linux, and macOS.
It does not enforce absolute paths, a terminal, a theme, or personal editor
preferences.

## Files

- `launch.json`: interactive launch configurations that request paths at runtime;
- `tasks.json`: reproducible tasks that use the conventional project layout;
- `settings.json`: minimal repository-specific settings;
- `extensions.json`: recommended Python extensions, without forced installation.

## Prerequisites

1. Open the repository root in VS Code.
2. Install the recommended extensions when VS Code offers them.
3. Create or select a Python environment with `Python: Select Interpreter`.
4. Install the dependencies:

   ```text
   python -m pip install -r requirements.txt
   ```

Tasks use the interpreter selected by the Python extension instead of a
platform-specific `python` executable.

## Paths

Each debug configuration requests its input and output paths. Relative paths are
resolved from the repository root, and absolute paths are also accepted. The
suggested values follow this convention:

```text
game_files/package       PAK files from the ISO
game_files/decompressed  extracted binary resources
output/textures          PNG textures
output/models            GLB models
```

This layout is optional. Replace the suggested values before launching a
configuration if your files are stored elsewhere.

## Debugging with F5

`launch.json` provides these configurations:

- `PAK: extract a file or directory`;
- `XET: convert a file or directory`;
- `DDS: convert a file or directory`;
- `Godot: export selected folder`;
- `DDM: experimental animation` (root-motion research on a skinned character);
- `DDM: debug manual layout (no textures)`.

Recursive mode is enabled for the XET and DDS converters. The Godot exporter
asks for one extracted folder below `KB`. For example, selecting
`game_files/decompressed/KB/map/map101` mirrors it to
`output/decoded/map/map101`, runs the DDM conversion in Godot/final mode, then
converts `map101_ins` to `map101_foliage.tres`. Folders without an `*_ins` table
still export their DDM files and simply skip foliage generation. For folders
under `KB/chara`, the same pipeline exports every discovered animation clip,
including root motion and the four runtime IK targets consumed by the Godot
import script. The complete animated FK pose is exported first, then Godot's
runtime IK modifiers solve the arm and leg positions over it. Hand and foot
rotations remain driven by FK because the adjacent source orientation triplets
have not been proven to use the HSC instance rotation convention. Joint
rotations use the reference skeleton embedded in the motion resource; no
animation clip is treated as an artificial bind pose. Animation triplets are
composed as `bind_local * delta` in each joint's DDM bind frame. This maps the
chr30x head's stored local-Z controller to its model-X flexion axis instead of
the old model-Z sideways tilt. Root rotation uses bone_000's complete verified
slots 5–7 triplet, rotation units default to radians, and the animation launch defaults
to all structurally bound joints. The PAK extractor asks for a worker count
and defaults to `4`.

## Tasks

Open `Terminal > Run Task` to run a step without the debugger. Unlike the F5
configurations, tasks use the conventional relative layout shown above. This
allows them to run sequentially without repeatedly requesting the same paths.

The `Pipeline: extraction and textures` task, also available through
`Ctrl+Shift+B`, runs:

1. PAK extraction;
2. recursive XET conversion;
3. recursive DDS conversion.

Use the corresponding F5 configurations when working with a different layout.
Tasks use the interpreter selected with `Python: Select Interpreter`.

DDM conversion remains separate because the decoder does not support every
layout yet. Its status is documented in `REVERSE_DDM.md`.

## Shared settings

`settings.json` only contains repository-specific settings: Python environment
activation, import resolution, and exclusion of large resource directories from
search and file watching. Terminal, theme, auto-save, and formatter choices
remain in each developer's user settings.
