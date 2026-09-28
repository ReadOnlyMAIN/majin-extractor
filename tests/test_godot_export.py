import json
import tempfile
import unittest
from pathlib import Path

from tools.conversion.godot_export import write_godot_material_assets


class GodotExportTests(unittest.TestCase):
    def test_writes_shader_textures_and_material_bindings(self):
        materials = [{
            "index": 2,
            "name": "armor_leader",
            "pbr_estimate": {"roughness": 0.24},
            "textures": [
                {"role": "diffuse", "output": "textures/c02.png"},
                {"role": "utility_mask", "output": "textures/u01.png"},
                {"role": "normal", "output": "textures/n02.png"},
                {"role": "matcap", "output": "textures/f02.png"},
            ],
        }]
        images = {
            f"textures/{name}.png": b"png"
            for name in ("c02", "u01", "n02", "f02")
        }
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            result = write_godot_material_assets(out, materials, images)
            manifest = json.loads((out / result["manifest"]).read_text())
            shader = (out / result["shader"]).read_text()
            tres_path = out / result["shader_materials"][0]
            tres = tres_path.read_text()

            self.assertTrue((out / "textures/u01.png").is_file())
            self.assertIn("uniform sampler2D utility_texture", shader)
            self.assertNotIn("uniform float roughness : hint_range(0.0, 1.0, 0.01) =", shader)
            self.assertIn("ALPHA_SCISSOR_THRESHOLD = 0.5;", shader)
            binding = manifest["materials"][0]
            self.assertEqual(binding["uniforms"]["utility_texture"], "textures/u01.png")
            self.assertTrue(binding["switches"]["use_env_sphere_texture"])
            self.assertEqual(binding["shader_material"], "materials/02_armor_leader.tres")
            self.assertEqual(binding["shader"], "../majin_original.gdshader")
            self.assertIn('path="../../majin_original.gdshader"', tres)
            self.assertIn('path="../textures/u01.png"', tres)
            self.assertIn("shader_parameter/utility_texture = ExtResource", tres)
            self.assertIn("shader_parameter/use_utility_texture = true", tres)
            self.assertIn("shader_parameter/roughness = 0.24", tres)
            self.assertEqual(binding["scalars"]["roughness"], 0.24)


if __name__ == "__main__":
    unittest.main()
