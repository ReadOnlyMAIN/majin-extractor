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
    _fold_multipass_details,
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

    def test_detail_layer_exposes_second_texture_set(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            materials = [
                {
                    "index": 0,
                    "name": "gake102__base",
                    "textures": [
                        {"role": "diffuse", "output": "textures/base_c.png"},
                        {"role": "normal", "output": "textures/base_n.png"},
                    ],
                    "detail": {
                        "diffuse": "textures/multi_c.png",
                        "normal": "textures/multi_n.png",
                        "strength": 0.75,
                    },
                },
            ]
            images = {
                "textures/base_c.png": b"\x89PNG\r\n\x1a\n",
                "textures/base_n.png": b"\x89PNG\r\n\x1a\n",
                "textures/multi_c.png": b"\x89PNG\r\n\x1a\n",
                "textures/multi_n.png": b"\x89PNG\r\n\x1a\n",
            }
            result = write_godot_material_assets(out, materials, images)
            manifest = json.loads((out / result["manifest"]).read_text())
            binding = manifest["materials"][0]
            self.assertTrue(binding["switches"]["use_detail"])
            self.assertTrue(binding["switches"]["use_detail_normal_texture"])
            self.assertEqual(binding["uniforms"]["detail_texture"], "textures/multi_c.png")
            self.assertEqual(
                binding["uniforms"]["detail_normal_texture"], "textures/multi_n.png"
            )
            self.assertEqual(binding["scalars"]["detail_strength"], 0.75)
            shader = (out / result["shader"]).read_text()
            self.assertIn("uniform sampler2D detail_texture", shader)
            tres = (out / binding["shader_material"]).read_text()
            self.assertIn("shader_parameter/detail_texture", tres)
            self.assertIn("shader_parameter/use_detail = true", tres)

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


class MultipassDetailFoldTests(unittest.TestCase):
    def _material(self, index, name, diffuse, normal=None):
        textures = [{"role": "diffuse", "output": diffuse}]
        if normal:
            textures.append({"role": "normal", "output": normal})
        return {"index": index, "name": name, "textures": textures}

    def test_overlay_material_is_folded_as_a_detail_layer(self):
        # base and multi share the same positions (painted on the same surface)
        # but use different UVs/textures. 'base' additionally has a face of its
        # own, so the overlay direction is unambiguous (like map101's terrain).
        base = self._material(11, "gake102__base", "textures/base_c.png", "textures/base_n.png")
        multi = self._material(12, "gake102__multi", "textures/multi_c.png", "textures/multi_n.png")
        vertices = [
            {"position": (0, 0, 0), "uv": (0.0, 0.0)},
            {"position": (1, 0, 0), "uv": (0.1, 0.0)},
            {"position": (0, 1, 0), "uv": (0.0, 0.1)},
            {"position": (0, 0, 0), "uv": (0.5, 0.5)},
            {"position": (1, 0, 0), "uv": (0.6, 0.5)},
            {"position": (0, 1, 0), "uv": (0.5, 0.6)},
            # A face belonging to base alone (not covered by multi).
            {"position": (3, 3, 3), "uv": (0.2, 0.2)},
            {"position": (4, 3, 3), "uv": (0.3, 0.2)},
            {"position": (3, 4, 3), "uv": (0.2, 0.3)},
        ]
        parts = [
            {"material_index": 11, "triangles": [(0, 1, 2), (6, 7, 8)]},
            {"material_index": 12, "triangles": [(3, 4, 5)]},
        ]
        folds = _fold_multipass_details([base, multi], parts, vertices)
        self.assertEqual(folds, [
            {"host": "gake102__base", "detail": "gake102__multi", "coverage": 1.0},
        ])
        self.assertEqual(base["detail"]["diffuse"], "textures/multi_c.png")
        self.assertEqual(base["detail"]["normal"], "textures/multi_n.png")
        # The overlay keeps its own material so the GLB primitive stays valid.
        self.assertNotIn("detail", multi)

    def test_material_with_unique_faces_is_not_folded(self):
        # Neither material is a pure overlay: each has a face the other does not
        # cover, so nothing is folded.
        base = self._material(11, "base", "textures/base_c.png")
        other = self._material(12, "other", "textures/other_c.png")
        vertices = [
            {"position": (0, 0, 0), "uv": (0.0, 0.0)},
            {"position": (1, 0, 0), "uv": (0.1, 0.0)},
            {"position": (0, 1, 0), "uv": (0.0, 0.1)},
            # A face belonging to base alone.
            {"position": (3, 3, 3), "uv": (0.2, 0.2)},
            {"position": (4, 3, 3), "uv": (0.3, 0.2)},
            {"position": (3, 4, 3), "uv": (0.2, 0.3)},
            # A face belonging to other alone.
            {"position": (5, 5, 5), "uv": (0.9, 0.9)},
            {"position": (6, 5, 5), "uv": (0.95, 0.9)},
            {"position": (5, 6, 5), "uv": (0.9, 0.95)},
        ]
        parts = [
            {"material_index": 11, "triangles": [(0, 1, 2), (3, 4, 5)]},
            {"material_index": 12, "triangles": [(0, 1, 2), (6, 7, 8)]},
        ]
        folds = _fold_multipass_details([base, other], parts, vertices)
        self.assertEqual(folds, [])
        self.assertNotIn("detail", base)
        self.assertNotIn("detail", other)


if __name__ == "__main__":
    unittest.main()
