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
    GODOT_ASSIGN_SCRIPT,
    GODOT_CUSTOM_SHADER,
    GODOT_FOLIAGE_SHADER,
    GODOT_SHADER_COMMON,
    _fold_multipass_details,
    write_godot_material_assets,
    write_godot_utility,
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
            self.assertIn("shader_type spatial;", GODOT_CUSTOM_SHADER)
            self.assertIn("uniform bool use_blend", GODOT_SHADER_COMMON)
            self.assertEqual(
                result["shader"], "res://majin_utility/majin_multitexture.gdshader"
            )
            self.assertEqual(
                manifest["materials"][0]["material_type"], "StandardMaterial3D"
            )
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
            self.assertIn("uniform sampler2D detail_texture", GODOT_SHADER_COMMON)
            tres = (out / binding["material_resource"]).read_text()
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

    def test_decoded_render_modes_use_native_standard_material_properties(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            materials = [
                {"index": 0, "name": "rock", "textures": [],
                 "render_state": {"mode": "opaque"},
                 "pbr_estimate": {
                     "roughness": 0.552, "specular": 0.4,
                     "diffuse": [1.0, 0.5, 0.25],
                 }},
                {"index": 1, "name": "vine", "textures": [],
                 "render_state": {"mode": "alpha_scissor"},
                 "pbr_estimate": {
                     "roughness": 0.9, "specular": 0.4,
                     "diffuse": [1.0, 1.0, 1.0],
                 }},
                {"index": 2, "name": "paint", "textures": [],
                 "render_state": {"mode": "alpha_blend"}},
            ]
            result = write_godot_material_assets(out, materials, {})
            manifest = json.loads((out / result["manifest"]).read_text())
            self.assertEqual(
                [entry["material_type"] for entry in manifest["materials"]],
                ["StandardMaterial3D"] * 3,
            )
            resources = [
                (out / entry["material_resource"]).read_text()
                for entry in manifest["materials"]
            ]
            self.assertNotIn("transparency =", resources[0])
            self.assertIn("roughness = 0.552", resources[0])
            self.assertIn("metallic_specular = 0.4", resources[0])
            self.assertIn("albedo_color = Color(1, 0.5, 0.25, 1)", resources[0])
            self.assertIn("transparency = 2", resources[1])
            self.assertIn("alpha_scissor_threshold = 0.5", resources[1])
            self.assertIn("cull_mode = 2", resources[1])
            self.assertIn("roughness = 0.9", resources[1])
            self.assertIn("transparency = 1", resources[2])

    def test_four_texture_shader_key_keeps_custom_pipeline_and_all_textures(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            material = {
                "index": 0,
                "name": "zimen",
                "render_state": {"mode": "opaque", "shader_key": "0x00847725"},
                "textures": [
                    {"slot": 0, "role": "diffuse", "output": "textures/a_c.png"},
                    {"slot": 1, "role": "auxiliary", "output": "textures/b_c.png"},
                    {"slot": 2, "role": "normal", "output": "textures/a_n.png"},
                    {"slot": 3, "role": "normal", "output": "textures/b_n.png"},
                ],
            }
            result = write_godot_material_assets(out, [material], {})
            entry = json.loads((out / result["manifest"]).read_text())["materials"][0]
            self.assertEqual(entry["material_type"], "ShaderMaterial")
            self.assertEqual(entry["uniforms"]["secondary_texture"], "textures/b_c.png")
            self.assertEqual(
                entry["uniforms"]["secondary_normal_texture"], "textures/b_n.png"
            )
            tres = (out / entry["material_resource"]).read_text()
            self.assertIn("majin_multitexture.gdshader", tres)
            self.assertIn("shader_parameter/secondary_texture", tres)

    def test_cutout_foliage_uses_two_sided_albedo_only_shader(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            materials = [{
                "index": 0,
                "name": "foliage",
                "textures": [{
                    "role": "diffuse",
                    "output": "textures/foliage.png",
                }],
                "render_state": {
                    "mode": "alpha_scissor",
                    "shader_key": "0x00843105",
                },
                "pbr_estimate": {
                    "roughness": 1.0,
                    "specular": 0.0,
                    "diffuse": [1.0, 1.0, 1.0],
                },
            }]
            result = write_godot_material_assets(out, materials, {})
            manifest = json.loads((out / result["manifest"]).read_text())
            binding = manifest["materials"][0]
            self.assertEqual(binding["material_type"], "ShaderMaterial")
            self.assertIn("majin_foliage.gdshader", binding["shader"])
            tres = (out / binding["material_resource"]).read_text()
            self.assertIn("majin_foliage.gdshader", tres)
            self.assertNotIn("normal_texture", tres)
            self.assertIn("FRONT_FACING", GODOT_FOLIAGE_SHADER)
            self.assertIn("ROUGHNESS = 1.0", GODOT_FOLIAGE_SHADER)
            self.assertIn("SPECULAR = 0.0", GODOT_FOLIAGE_SHADER)

    def test_instance_foliage_shader_keys_use_foliage_material(self):
        for shader_key in ("0x0082b105", "0x0086b105"):
            with tempfile.TemporaryDirectory() as root:
                out = Path(root)
                materials = [{
                    "index": 0,
                    "name": "instance_foliage",
                    "textures": [{
                        "role": "diffuse",
                        "output": "textures/foliage.png",
                    }],
                    "render_state": {
                        "mode": "opaque",
                        "shader_key": shader_key,
                    },
                    "pbr_estimate": {
                        "roughness": 1.0,
                        "specular": 0.0,
                        "diffuse": [1.0, 1.0, 1.0],
                    },
                }]
                result = write_godot_material_assets(out, materials, {})
                manifest = json.loads((out / result["manifest"]).read_text())
                binding = manifest["materials"][0]
                tres = (out / binding["material_resource"]).read_text()
            self.assertEqual(binding["material_type"], "ShaderMaterial")
            self.assertTrue(binding["shader"].endswith("majin_foliage.gdshader"))
            self.assertIn("shader_parameter/base_texture", tres)


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
        # base and multi share positions and UVs (painted on the same surface)
        # but use different textures. 'base' additionally has a face of its
        # own, so the overlay direction is unambiguous (like map101's terrain).
        base = self._material(11, "gake102__base", "textures/base_c.png", "textures/base_n.png")
        multi = self._material(12, "gake102__multi", "textures/multi_c.png", "textures/multi_n.png")
        vertices = [
            {"position": (0, 0, 0), "uv": (0.0, 0.0), "color": 0xFFFFFF00},
            {"position": (1, 0, 0), "uv": (0.1, 0.0), "color": 0xFFFFFF00},
            {"position": (0, 1, 0), "uv": (0.0, 0.1), "color": 0xFFFFFF00},
            {"position": (0, 0, 0), "uv": (0.0, 0.0), "color": 0xFFFFFFFF},
            {"position": (1, 0, 0), "uv": (0.1, 0.0), "color": 0xFFFFFF7F},
            {"position": (0, 1, 0), "uv": (0.0, 0.1), "color": 0xFFFFFF00},
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
            {
                "host": "gake102__base", "detail": "gake102__multi",
                "coverage": 1.0, "overlay_material_index": 12,
                "mask_source": "vertex_alpha",
            },
        ])
        self.assertEqual(base["detail"]["diffuse"], "textures/multi_c.png")
        self.assertEqual(base["detail"]["normal"], "textures/multi_n.png")
        self.assertEqual([vertex["color"] & 0xFF for vertex in vertices[:3]], [255, 127, 0])
        self.assertEqual(base["detail"]["mask_source"], "vertex_alpha")
        # The overlay keeps its material metadata, but its coplanar primitive
        # is excluded from the Godot GLB by the scene exporter.
        self.assertNotIn("detail", multi)

    def test_distinct_overlay_uvs_are_not_folded(self):
        base = self._material(0, "base", "textures/base.png")
        overlay = self._material(1, "overlay", "textures/overlay.png")
        vertices = [
            {"position": (0, 0, 0), "uv": (0, 0)},
            {"position": (1, 0, 0), "uv": (1, 0)},
            {"position": (0, 1, 0), "uv": (0, 1)},
            {"position": (0, 0, 0), "uv": (0.5, 0.5)},
            {"position": (1, 0, 0), "uv": (0.6, 0.5)},
            {"position": (0, 1, 0), "uv": (0.5, 0.6)},
            {"position": (2, 0, 0), "uv": (0, 0)},
            {"position": (3, 0, 0), "uv": (1, 0)},
            {"position": (2, 1, 0), "uv": (0, 1)},
        ]
        parts = [
            {"material_index": 0, "triangles": [(0, 1, 2), (6, 7, 8)]},
            {"material_index": 1, "triangles": [(3, 4, 5)]},
        ]
        self.assertEqual(_fold_multipass_details([base, overlay], parts, vertices), [])

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


class GodotUtilityTests(unittest.TestCase):
    def test_instance_importer_persists_materials_on_extracted_mesh(self):
        script = (
            Path(__file__).resolve().parent.parent
            / "godot" / "utility" / "import_instance.gd"
        ).read_text()
        self.assertIn('extends EditorScenePostImport', script)
        self.assertIn('mesh.surface_set_material(surface, assignments[material_name])', script)
        self.assertIn('ResourceSaver.save(extracted, mesh_path)', script)
        self.assertIn('get_source_file().get_file().get_basename()', script)
        self.assertIn('res://terrain/foliage/meshes', script)

    def test_repository_utility_folder_matches_the_constants(self):
        # godot/utility/ is the versioned source of truth; the Python constants
        # must stay identical so an installed copy never drifts from the export.
        repo_utility = (
            Path(__file__).resolve().parent.parent / "godot" / "utility"
        )
        self.assertEqual(
            (repo_utility / "majin_multitexture.gdshader").read_text(),
            GODOT_CUSTOM_SHADER,
        )
        self.assertEqual(
            (repo_utility / "majin_material_common.gdshaderinc").read_text(),
            GODOT_SHADER_COMMON,
        )
        self.assertEqual(
            (repo_utility / "majin_foliage.gdshader").read_text(),
            GODOT_FOLIAGE_SHADER,
        )
        self.assertEqual(
            (repo_utility / "assign_materials.gd").read_text(),
            GODOT_ASSIGN_SCRIPT,
        )

    def test_write_godot_utility_copies_shader_and_script(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "majin_utility"
            written = write_godot_utility(destination)
            names = sorted(path.name for path in written)
            self.assertEqual(names, [
                "assign_materials.gd",
                "import_instance.gd",
                "majin_foliage.gdshader",
                "majin_material_common.gdshaderinc",
                "majin_multitexture.gdshader",
            ])
            shader = (destination / "majin_multitexture.gdshader").read_text()
            script = (destination / "assign_materials.gd").read_text()
            instance_script = (destination / "import_instance.gd").read_text()
            self.assertIn("shader_type spatial;", shader)
            self.assertIn("extends EditorScenePostImport", script)
            self.assertIn("material_bindings.json", script)
            self.assertIn("model_base.path_join(tres)", script)
            self.assertNotIn("path.get_base_dir().path_join(tres)", script)
            self.assertIn("ResourceSaver.save(extracted, mesh_path)", instance_script)

    def test_write_godot_utility_does_not_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "majin_utility"
            write_godot_utility(destination)
            shader_path = destination / "majin_multitexture.gdshader"
            shader_path.write_text("custom")
            write_godot_utility(destination, overwrite=False)
            self.assertEqual(shader_path.read_text(), "custom")


if __name__ == "__main__":
    unittest.main()
