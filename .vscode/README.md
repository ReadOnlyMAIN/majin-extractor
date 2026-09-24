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
output/models            OBJ/MTL models
```

This layout is optional. Replace the suggested values before launching a
configuration if your files are stored elsewhere.

## Debugging with F5

`launch.json` provides four configurations:

- `PAK: extract a file or directory`;
- `XET: convert a file or directory`;
- `DDS: convert a file or directory`;
- `DDM: convert a model`.

Recursive mode is enabled for the XET and DDS converters. The PAK extractor asks
for a worker count and defaults to `4`.

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
