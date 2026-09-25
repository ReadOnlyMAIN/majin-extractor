"""Regression coverage for the observed multi-section map DDM layout."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

from tools.conversion import ddm_to_obj as ddm


def map_section(primitive, positions, indices, material=0):
    count = len(positions)
    data = bytearray(struct.pack('>4I', 7, count, 1, len(indices)))
    data.extend(struct.pack(f'>{len(indices)}H', *indices))
    for _ in positions:
        data.extend(struct.pack('>4I', 0x7FC00000, 0, 0, 0x3C00))
    # The last attribute marker is shared with the first position record.
    for i, position in enumerate(positions):
        if i:
            data.extend(struct.pack('>I', 0))
        data.extend(struct.pack('>3fI2e', *position, 0xFFFFFFFF, 0.0, 0.0))
    data.extend(struct.pack('>I', 0))
    primitives = len(indices) // 3 if primitive == 3 else len(indices) - 2
    data.extend(struct.pack('>15I',
        0xFFFFFFFF, primitive, primitives, 0, count, material,
        0, 0, 0, 0, 0, 0, 0, 0, len(indices)))
    return bytes(data)


class MapGeometryTests(unittest.TestCase):
    def setUp(self):
        self.positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        self.first = map_section(3, self.positions[:3], [0, 1, 2])
        self.second = map_section(4, self.positions, [0, 1, 2, 3], material=1)
        self.data = ddm.MAGIC + struct.pack('>I', 3) + self.first + self.second

    def test_exports_all_sections_with_correct_face_offsets(self):
        args = SimpleNamespace(
            vertex_count=None, position_offset=None, index_offset=None,
            index_count=None, no_textures=True, texture_root=None,
            final=False, scale=1.0, format="obj",
        )
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'map'
            source.write_bytes(self.data)
            output = Path(root) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                ddm.analyze_file(source, output, args)
            report = json.loads((output / 'map/analysis.json').read_text())
            obj = (output / 'map/map.obj').read_text().splitlines()
            self.assertEqual(report['vertex_count'], 7)
            self.assertEqual(len(report['geometry_sections']), 2)
            self.assertEqual(report['index_buffer']['unconsumed_count'], 0)
            self.assertEqual([p['triangle_count'] for p in report['mesh_parts']], [1, 2])
            self.assertEqual([line for line in obj if line.startswith('f ')], [
                'f 1/1/1 2/2/2 3/3/3',
                'f 4/4/4 5/5/5 6/6/6',
                'f 6/6/6 5/5/5 7/7/7',
            ])
            self.assertIn('usemtl material_1', obj)

    def test_rejects_incomplete_and_inconsistent_sections(self):
        self.assertEqual(ddm.find_map_geometry_sections(self.first[:-1]), [])
        damaged = bytearray(self.first)
        struct.pack_into('>I', damaged, len(damaged) - 4, 4)
        self.assertEqual(ddm.find_map_geometry_sections(damaged), [])
        damaged = bytearray(self.first)
        struct.pack_into('>H', damaged, 16, 3)  # Out-of-range local vertex.
        self.assertEqual(ddm.find_map_geometry_sections(damaged), [])

    def test_default_glb_final_contains_separate_map_nodes(self):
        # Offset the second section so its geometry is a separate object.
        shifted = [(x + 10, y, z) for x, y, z in self.positions]
        data = ddm.MAGIC + struct.pack('>I', 3) + self.first
        data += map_section(4, shifted, [0, 1, 2, 3], material=1)
        args = SimpleNamespace(
            vertex_count=None, position_offset=None, index_offset=None,
            index_count=None, no_textures=True, texture_root=None,
            final=True, scale=0.01,
        )
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'map'
            source.write_bytes(data)
            output = Path(root) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                ddm.analyze_file(source, output, args)
            files = list((output / 'map').iterdir())
            self.assertEqual([p.name for p in files], ['map.glb'])
            glb = files[0].read_bytes()
            length = struct.unpack_from('<I', glb, 12)[0]
            document = json.loads(glb[20:20 + length])
            self.assertEqual(len(document['nodes']), 2)
            self.assertNotEqual(document['nodes'][0]['translation'],
                                document['nodes'][1]['translation'])

    def test_unresolved_texture_does_not_crash_or_become_diffuse(self):
        texture = {'conversion': None, 'role': 'unresolved'}
        ddm.classify_material_textures([texture])
        self.assertEqual(texture['role'], 'unresolved')

    def test_skinned_layout_is_identified_before_static_stream_heuristics(self):
        data = bytearray(ddm.MAGIC + struct.pack('>I', 3))
        data.extend(b'\0' * 24)
        offset = len(data)
        data.extend(struct.pack('>6I', 2, 1, 8, 891, 32, 2204))
        data.extend(b'\0' * (2204 * 2 + 891 * 44))
        header = ddm.find_skinned_geometry_header(data)
        self.assertEqual(header, {
            'offset': offset,
            'section_count': 2,
            'first_submesh_count': 1,
            'vertex_attribute_count': 8,
            'vertex_count': 891,
            'bone_palette_count': 32,
            'vertex_stride': 28,
            'index_count': 2204,
        })
        args = SimpleNamespace(
            vertex_count=None, position_offset=None, index_offset=None,
            index_count=None, no_textures=True, texture_root=None,
            final=True, scale=0.01,
        )
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'skinned'
            source.write_bytes(data)
            output = Path(root) / 'out'
            with self.assertRaisesRegex(RuntimeError, 'skeleton transform count'):
                ddm.analyze_file(source, output, args)
            self.assertFalse((output / 'skinned').exists())

    def test_empty_output_cleanup_never_removes_nonempty_parent(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'output'
            parent = output / 'character'
            failed = parent / 'failed'
            failed.mkdir(parents=True)
            marker = parent / 'successful.glb'
            marker.write_bytes(b'glTF')
            ddm.prune_empty_output_directories(failed, output)
            self.assertFalse(failed.exists())
            self.assertEqual(marker.read_bytes(), b'glTF')

    def test_external_motion_boundary_table_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            kb = Path(root) / 'KB'
            model = kb / 'chara/chr300/chr300'
            sequence = kb / 'motionSequence/chr300/chr300'
            package = kb / 'motionPackage/chr300/BigEndian/chr300'
            for path in (model, sequence, package):
                path.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(b'')
            # Three relative boundaries describe two clips. The last boundary
            # is the end-of-file sentinel.
            motion = bytearray(0xC0)
            struct.pack_into('>I3I', motion, 0x80, 3, 0x20, 0x30, 0x40)
            motion[0xA0] = 3
            # A two-byte header plus two hierarchy words precede the IDs;
            # equivalently the ID table begins at clip + 2 * bone_count.
            motion[0xA6:0xA9] = bytes((7, 3, 9))
            sequence.write_bytes(motion)
            package.write_bytes(b'\0crg' + struct.pack('>2I', 2, 9))
            result = ddm.find_external_character_motion(model)
            self.assertEqual(result['clip_count'], 2)
            self.assertEqual(result['first_clip_offset'], 0xA0)
            self.assertEqual(result['sequence_end_offset'], 0xC0)
            self.assertEqual(result['skeleton_bone_ids'], [7, 3, 9])
            self.assertEqual(result['package_entry_count'], 9)
            self.assertFalse(result['decoded'])


if __name__ == '__main__':
    unittest.main()
