# Godot utility assets

Reusable assets emitted by the DDM converter in `--material-mode godot`. They
are version-controlled here so an export never regenerates them and Godot
resources can reference a stable path.

| File | Purpose |
| --- | --- |
| `majin_multitexture.gdshader` | Custom entry point for decoded effects outside `StandardMaterial3D`, notably Map101's two-albedo/two-normal `zimen` variant and folded detail layers. |
| `majin_material_common.gdshaderinc` | Shared reconstructed lighting, normal and detail-layer implementation. |
| `assign_materials.gd` | `EditorScenePostImport` import script that assigns generated native or custom `Material` resources by name. |

The ordinary opaque, alpha-scissor and alpha-blend families are emitted as
native `StandardMaterial3D` resources. This preserves Godot's own PBR, depth,
shadow and transparency behavior. The custom shader is selected only when the
decoded material needs multiple texture pairs, a folded detail pass, a utility
lookup, or another feature the native material cannot express directly.

## Installing into a Godot project

Copy this folder into the project so it lands at `res://majin_utility/`:

```text
<godot_project>/
  majin_utility/
    majin_multitexture.gdshader
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
`assign_materials.gd` is an **import script**
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
conversions) keep the materials assigned with no manual step.

Nothing here is DDM-specific beyond the shader uniforms; the shader and the
import script can be reused across every converted model.
