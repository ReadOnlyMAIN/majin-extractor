# Godot utility assets

Reusable assets emitted by the DDM converter in `--material-mode godot`. They
are version-controlled here so an export never regenerates them and Godot
resources can reference a stable path.

| File | Purpose |
| --- | --- |
| `majin_original.gdshader` | Spatial shader reproducing the reconstructed DDM look: multi-texture blend maps, multipass detail layers, matcap/environment lookup and the utility mask. |
| `assign_materials.gd` | EditorScript that assigns the generated `ShaderMaterial` `.tres` files to an imported GLB, matching by material name through `material_bindings.json`. |

## Installing into a Godot project

Copy this folder into the project so it lands at `res://majin_utility/`:

```text
<godot_project>/
  majin_utility/
    majin_original.gdshader
    assign_materials.gd
```

The generated `.tres` files reference
`res://majin_utility/majin_original.gdshader`, so the folder name must be
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
Run `assign_materials.gd` once after importing the model:

1. In the Godot 4 editor, open `assign_materials.gd` and run it
   (**File > Run**), or attach it to a `@tool` script and run from the scene.
2. It scans `MODEL_DIR` (default `res://`) for `material_bindings.json`, then
   walks the selected nodes (or the edited scene) and assigns the matching
   `ShaderMaterial` to every mesh surface by material name.
3. Unmatched names are reported as a warning.

Nothing here is DDM-specific beyond the shader uniforms; the shader and the
assignment script can be reused across every converted model.
