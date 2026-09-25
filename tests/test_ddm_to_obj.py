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


if __name__ == '__main__':
    unittest.main()
