from pathlib import Path
import unittest

from tools.conversion.hsc_to_foliage import (
    godot_transform,
    HSCInstance,
    read_hsc_instances,
    render_foliage_resource,
)


class HSCFoliageTests(unittest.TestCase):
    def test_map101_instances_are_decoded(self):
        source = Path("game_files/decompressed/KB/map/map101/map101_ins")
        if not source.exists():
            self.skipTest("map101 fixture is unavailable")
        instances = read_hsc_instances(source)
        self.assertEqual(len(instances), 332)
        self.assertEqual(
            {model: sum(i.model_name == model for i in instances)
             for model in sorted({i.model_name for i in instances})},
            {"ins107": 40, "ins108": 62, "ins109": 66,
             "ins110": 76, "ins111": 88},
        )
        self.assertTrue(all(instance.cull_by_distance for instance in instances))
        self.assertEqual(
            {distance: sum(i.cull_distance == distance for i in instances)
             for distance in {i.cull_distance for i in instances}},
            {7000.0: 266, 10000.0: 66},
        )

    def test_identity_transform_preserves_map_coordinate_axes(self):
        instance = HSCInstance(
            (100.0, 200.0, 300.0), (0.0, 0.0, 0.0),
            (1.0, 2.0, 3.0), "ins107", True, 7000.0, 1,
        )
        self.assertEqual(godot_transform(instance), (
            1.0, 0.0, 0.0,
            0.0, 2.0, 0.0,
            0.0, 0.0, 3.0,
            1.0, 2.0, 3.0,
        ))

    def test_source_row_vector_rotation_is_transposed_for_godot(self):
        instance = HSCInstance(
            (0.0, 0.0, 0.0), (0.0, 30.0, 0.0),
            (1.0, 2.0, 3.0), "ins108", True, 7000.0, 1,
        )
        transform = godot_transform(instance)
        sine = 0.5
        cosine = 3.0 ** 0.5 / 2.0
        expected_basis_columns = (
            cosine, 0.0, sine,
            0.0, 2.0, 0.0,
            -3.0 * sine, 0.0, 3.0 * cosine,
        )
        for actual, expected in zip(transform[:9], expected_basis_columns):
            self.assertAlmostEqual(actual, expected)

    def test_optional_z_reflection_converts_basis_and_position(self):
        instance = HSCInstance(
            (100.0, 200.0, 300.0), (0.0, 90.0, 0.0),
            (1.0, 1.0, 1.0), "ins107", True, 7000.0, 1,
        )
        regular = godot_transform(instance)
        reflected = godot_transform(instance, reflect_z=True)
        self.assertEqual(regular[-3:], (1.0, 2.0, 3.0))
        self.assertEqual(reflected[-3:], (1.0, 2.0, -3.0))
        self.assertAlmostEqual(regular[2], -reflected[2])
        self.assertAlmostEqual(regular[6], -reflected[6])

    def test_resource_uses_requested_scripts_mesh_and_visibility(self):
        instance = HSCInstance(
            (0.0, 0.0, 0.0), (0.0, 90.0, 0.0),
            (1.0, 1.0, 1.0), "ins107", True, 7000.0, 6,
        )
        resource = render_foliage_resource([instance])
        self.assertIn(
            'path="res://addons/procedural_tools/foliage/resources/foliage_data.gd"',
            resource,
        )
        self.assertIn('path="res://terrain/foliage/meshes/ins107.res"', resource)
        self.assertIn('resource_name = "map_instance_0000_ins107"', resource)
        self.assertIn("visibility_ranges = Vector2(0, 70)", resource)
        self.assertIn(
            'scene_folliage = Array[ExtResource("1_foliage_data")](', resource,
        )


if __name__ == "__main__":
    unittest.main()
