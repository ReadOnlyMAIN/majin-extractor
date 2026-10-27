from pathlib import Path
import unittest

from tools.conversion.ddm.skinned import (
    decode_skinned_geometry, decode_skinned_skeleton, find_skinned_geometry_header,
)
from tools.conversion.ddm.materials import parse_materials
from tools.conversion.player_armor import partition_player_parts, is_player_asset, darkness_material_indices


class PlayerArmorTests(unittest.TestCase):
    def test_partition_keeps_each_triangle_and_uses_bone_ancestry(self):
        skeleton = {'joints': [
            {'index': 0, 'global_id': 0, 'parent': None},
            {'index': 1, 'global_id': 101, 'parent': 0},
            {'index': 2, 'global_id': 120, 'parent': 1},
            {'index': 3, 'global_id': 4, 'parent': 0},
        ]}
        vertices = [dict(joints=(joint,), weights=(1.0,))
                    for joint in (0, 0, 0, 2, 2, 2, 3, 3, 3)]
        triangles = [(0, 1, 2), (3, 4, 5), (6, 7, 8)]
        result = partition_player_parts(vertices, [{'triangles': triangles}], skeleton)
        self.assertEqual({part['armor_slot']: part['triangles'] for part in result},
                         dict(torso=[triangles[0]], head=[triangles[1]], legs=[triangles[2]]))
        self.assertTrue(is_player_asset('chr100'))
        self.assertTrue(is_player_asset('chr156'))
        self.assertFalse(is_player_asset('chr200'))

    def test_darkness_surfaces_are_separate_from_equipment(self):
        materials = [dict(index=i, name=name) for i, name in enumerate(
            ('body', 'T_body', 'T_huku', 'Tbody', 'tar_face', 'chr140T_manto', 'huku'))]
        self.assertEqual(darkness_material_indices(materials), {1, 2, 3, 4, 5})
        skeleton = {'joints': [dict(index=0, global_id=101, parent=None)]}
        vertices = [dict(joints=(0,), weights=(1.0,)) for _ in range(3)]
        parts = [dict(material_index=1, triangles=[(0, 1, 2)])]
        result = partition_player_parts(vertices, parts, skeleton, materials)
        self.assertEqual(result[0]['armor_slot'], 'head_darkness')
        self.assertEqual(result[0]['triangles'], [(0, 1, 2)])

    @unittest.skipUnless(Path('game_files/decompressed/KB/chara/chr100/chr100').is_file(),
                         'requires local game corpus')
    def test_chr100_starts_with_rigid_section_and_decodes_all_materials(self):
        data = Path('game_files/decompressed/KB/chara/chr100/chr100').read_bytes()
        header = find_skinned_geometry_header(data)
        self.assertEqual(header['vertex_attribute_count'], 7)
        self.assertEqual(header['section_count'], 16)
        skeleton = decode_skinned_skeleton(data)
        geometry = decode_skinned_geometry(data, header, skeleton)
        self.assertEqual(len(geometry['vertices']), 13515)
        self.assertEqual(len(data) - geometry['end_offset'], 16)
        count = max(part['material_index'] for part in geometry['mesh_parts']) + 1
        materials = parse_materials(data, header['offset'], count)
        self.assertEqual(len(materials), 9)
        self.assertIn('eye', [material['name'] for material in materials])
        for vertex in geometry['vertices'][:161]:
            self.assertEqual(vertex['weights'], (1.0, 0.0, 0.0, 0.0))
            self.assertIn(skeleton['joints'][vertex['joints'][0]]['global_id'], (110, 111, 112))
        parts = partition_player_parts(geometry['vertices'], geometry['mesh_parts'], skeleton)
        self.assertEqual(sum(len(p['triangles']) for p in parts),
                         sum(len(p['triangles']) for p in geometry['mesh_parts']))

    @unittest.skipUnless(Path('game_files/decompressed/KB/chara/chr116/chr116').is_file(),
                         'requires local game corpus')
    def test_untextured_rig_material_keeps_texture_bindings_in_order(self):
        for name, expected in (('chr101', 'JRIG_MT'), ('chr116', 'chr101_JRIG_MT')):
            data = Path(f'game_files/decompressed/KB/chara/{name}/{name}').read_bytes()
            header = find_skinned_geometry_header(data)
            materials = parse_materials(data, header['offset'], 8)
            self.assertEqual(len(materials), 8)
            rig = next(material for material in materials if material['name'] == expected)
            self.assertEqual(rig['texture_references'], [])
            self.assertTrue(any(material['texture_references'] for material in materials))

    @unittest.skipUnless(Path('game_files/decompressed/KB/chara/chr101/chr101').is_file(),
                         'requires local game corpus')
    def test_clean_jaw_has_distinct_bind_axis(self):
        root = Path('game_files/decompressed/KB/chara')
        rigs = [decode_skinned_skeleton((root / name / name).read_bytes())
                for name in ('chr100', 'chr101')]
        jaws = [next(joint for joint in rig['joints'] if joint['global_id'] == 120)
                for rig in rigs]
        # Copying absolute local poses would replace this 180-degree difference.
        dot = sum(a * b for a, b in zip(jaws[0]['rotation'], jaws[1]['rotation']))
        self.assertAlmostEqual(dot, 0.0, places=5)

    @unittest.skipUnless(Path('game_files/decompressed/KB/motionSequence/chr100/chr100').is_file(),
                         'requires local game corpus')
    def test_chr100_unnamed_segments_have_no_source_name(self):
        from tools.conversion.motion.sequence import parse_sequence_index
        from tools.conversion.motion.package import parse_motion_package
        root = Path('game_files/decompressed/KB')
        index = parse_sequence_index(root / 'motionSequence/chr100/chr100')
        package = parse_motion_package(root / 'motionPackage/chr100/BigEndian/chr100')
        anonymous = [block for block in package.curve_blocks
                     if not index.records_for_slot(block.slot_index)]
        self.assertEqual(len(anonymous), 86)
