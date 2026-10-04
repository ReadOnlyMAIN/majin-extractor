import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.conversion.blend_mask import (
    DEFAULT_BLEND_RADIUS,
    MAX_BLEND_MATERIALS,
    build_blend_map,
    select_material_slots,
)
from tools.conversion.ddm.materials import (
    material_mode_of,
    uses_godot_materials,
)
from tools.conversion.godot_export import (
    write_godot_material_assets,
)


class GodotExportTests(unittest.TestCase):
    def test_writes_shader_textures_and_material_bindings(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            materials = [
                {
                    "index": 0,
                    "name": "default",
                    "textures": [
                        {"role": "diffuse", "output": "textures/material_00_diffuse.png"},
                        {"role": "normal", "output": "textures/material_00_normal.png"},
                    ],
                },
            ]
            images = {
                "textures/material_00_diffuse.png": b"\x89PNG\r\n\x1a\n",
                "textures/material_00_normal.png": b"\x89PNG\r\n\x1a\n",
            }
            result = write_godot_material_assets(out, materials, images)
            manifest = json.loads((out / result["manifest"]).read_text())
            shader = (out / result["shader"]).read_text()
            self.assertIn("shader_type spatial;", shader)
            self.assertIn("uniform bool use_blend", shader)
            self.assertEqual(manifest["materials"][0]["switches"]["use_blend"], False)
            self.assertIsNone(manifest["materials"][0]["blend_map"])
            # Single-material path does not generate a blend map.
            self.assertEqual(result["blend_map_count"], 0)

    def _triangle(self, vertices, material_index, positions):
        base = len(vertices)
        for position in positions:
            vertices.append({"position": position, "uv": (0.5, 0.5)})
        return {
            "material_index": material_index,
            "triangles": [(base, base + 1, base + 2)],
        }

    def test_select_material_slots_reserves_owner_slot_zero(self):
        parts = [
            {"material_index": 7, "triangles": []},
            {"material_index": 3, "triangles": []},
            {"material_index": 5, "triangles": []},
            {"material_index": 9, "triangles": []},
            {"material_index": 11, "triangles": []},
        ]
        slots = select_material_slots(parts, max_slots=4, owner=5)
        self.assertEqual(slots[5], 0)
        self.assertEqual(len(slots), MAX_BLEND_MATERIALS)
        self.assertNotIn(11, slots)

    def test_build_blend_map_returns_normalized_slot_table(self):
        vertices = []
        # Two triangles sharing the edge from (0,0) to (1,0). Their centroids
        # are ~0.006 apart (scaled), well inside the 0.05 blend radius.
        side = 0.01
        parts = [
            self._triangle(
                vertices, 0,
                [(0.0, 0.0, 0.0), (side, 0.0, 0.0), (0.0, side, 0.0)],
            ),
            self._triangle(
                vertices, 1,
                [(side, 0.0, 0.0), (side, side, 0.0), (0.0, side, 0.0)],
            ),
        ]
        slots = {0: 0, 1: 1}
        rows, slot_materials = build_blend_map(
            vertices, parts, slots, scale=1.0, radius=DEFAULT_BLEND_RADIUS, size=8,
        )
        self.assertEqual(slot_materials, [0, 1, None, None])
        # Every texel sums to 0 (unmapped) or 1 (mapped) across RGBA.
        for row in rows:
            for offset in range(0, len(row), 4):
                total = sum(row[offset:offset + 4])
                self.assertIn(total, (0, 255))
        # A texel must actually carry neighbour weight, otherwise the blend
        # map machinery is not connected to the geometry at all.
        self.assertTrue(any(
            row[offset + 1] for row in rows for offset in range(0, len(row), 4)
        ))

    def test_write_godot_material_assets_binds_neighbour_diffuse(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            vertices = []
            side = 0.01
            parts = [
                self._triangle(
                    vertices, 0,
                    [(0.0, 0.0, 0.0), (side, 0.0, 0.0), (0.0, side, 0.0)],
                ),
                self._triangle(
                    vertices, 1,
                    [(side, 0.0, 0.0), (side, side, 0.0), (0.0, side, 0.0)],
                ),
            ]
            materials = [
                {
                    "index": 0,
                    "name": "a",
                    "textures": [{"role": "diffuse", "output": "textures/a.png"}],
                },
                {
                    "index": 1,
                    "name": "b",
                    "textures": [{"role": "diffuse", "output": "textures/b.png"}],
                },
            ]
            images = {"textures/a.png": b"\x89PNG\r\n\x1a\n", "textures/b.png": b"\x89PNG\r\n\x1a\n"}
            result = write_godot_material_assets(
                out, materials, images,
                vertices=vertices, mesh_parts=parts, scale=1.0,
            )
            manifest = json.loads((out / result["manifest"]).read_text())
            bindings = {b["material_index"]: b for b in manifest["materials"]}
            # Material 0 should link to material 1's diffuse (its neighbour).
            self.assertTrue(bindings[0]["switches"]["use_blend"])
            self.assertEqual(bindings[0]["uniforms"]["blend_base_texture_1"], "textures/b.png")
            self.assertIsNotNone(bindings[0]["blend_map"])

    def test_write_godot_material_assets_skips_blend_without_geometry(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            materials = [
                {"index": 0, "name": "a", "textures": [{"role": "diffuse", "output": "textures/a.png"}]},
                {"index": 1, "name": "b", "textures": [{"role": "diffuse", "output": "textures/b.png"}]},
            ]
            images = {"textures/a.png": b"\x89PNG\r\n\x1a\n", "textures/b.png": b"\x89PNG\r\n\x1a\n"}
            result = write_godot_material_assets(out, materials, images)
            self.assertEqual(result["blend_map_count"], 0)


class MaterialModeTests(unittest.TestCase):
    def test_pbr_is_the_default_mode(self):
        self.assertEqual(material_mode_of(SimpleNamespace()), "pbr")
        self.assertFalse(uses_godot_materials(SimpleNamespace()))

    def test_godot_mode_emits_godot_assets(self):
        args = SimpleNamespace(material_mode="godot")
        self.assertEqual(material_mode_of(args), "godot")
        self.assertTrue(uses_godot_materials(args))

    def test_original_godot_is_a_deprecated_alias_of_godot(self):
        args = SimpleNamespace(material_mode="original-godot")
        self.assertEqual(material_mode_of(args), "godot")
        self.assertTrue(uses_godot_materials(args))


if __name__ == "__main__":
    unittest.main()
