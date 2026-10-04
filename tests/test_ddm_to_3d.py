"""Regression coverage for the observed multi-section map DDM layout."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.conversion import ddm_to_3d as ddm
from tools.conversion import motion_decode

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
        # A disjoint second section keeps its own faces; overlapping coordinates
        # would be collapsed as an exact multipass duplicate during export.
        self.second_positions = [(10, 0, 0), (11, 0, 0), (10, 1, 0), (11, 1, 0)]
        self.first = map_section(3, self.positions[:3], [0, 1, 2])
        self.second = map_section(4, self.second_positions, [0, 1, 2, 3], material=1)
        self.data = ddm.MAGIC + struct.pack('>I', 3) + self.first + self.second

    def test_exports_all_sections_to_glb(self):
        args = SimpleNamespace(
            vertex_count=None, position_offset=None, index_offset=None,
            index_count=None, no_textures=True, texture_root=None,
            final=False, scale=1.0,
        )
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'map'
            source.write_bytes(self.data)
            output = Path(root) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                ddm.analyze_file(source, output, args)
            report = json.loads((output / 'map/analysis.json').read_text())
            self.assertEqual(report['vertex_count'], 7)
            self.assertEqual(len(report['geometry_sections']), 2)
            self.assertEqual(report['index_buffer']['unconsumed_count'], 0)
            self.assertEqual([p['triangle_count'] for p in report['mesh_parts']], [1, 2])
            # GLB is the only geometry output; it must be present and valid.
            glb = (output / 'map/map.glb').read_bytes()
            self.assertEqual(glb[:4], b'glTF')
            length = struct.unpack_from('<I', glb, 12)[0]
            document = json.loads(glb[20:20 + length])
            total_triangles = sum(
                document['accessors'][primitive['indices']]['count'] // 3
                for mesh in document['meshes']
                for primitive in mesh['primitives']
            )
            self.assertEqual(total_triangles, 3)

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

    def test_character_color_wins_over_larger_matcap(self):
        def texture(reference, width, height):
            return {
                'reference': reference,
                'conversion': {
                    'width': width,
                    'height': height,
                    'content': {
                        'blue_normal_ratio': 0.0,
                        'red_mask_ratio': 0.0,
                        'grayscale_ratio': 0.0,
                    },
                },
            }

        color = texture('chr300_c01', 16, 16)
        matcap = texture('chr300_f', 128, 128)

        ddm.classify_material_textures([color, matcap])

        self.assertEqual(color['role'], 'diffuse')
        self.assertEqual(matcap['role'], 'matcap')

    def test_matcap_updates_pbr_estimate_with_provenance(self):
        material = {
            'index': 0,
            'pbr_estimate': {'roughness': 0.5, 'metallic': None},
            'textures': [{
                'reference': 'chr300_f02', 'role': 'matcap',
                'conversion': {'width': 16, 'height': 16, 'content': {
                    'highlight_ratio': 0.16, 'mean_luminance': 0.3,
                    'peak_luminance': 0.9, 'mean_saturation': 0.5,
                }},
            }],
        }
        ddm.apply_matcap_pbr_estimates([material])
        estimate = material['pbr_estimate']
        self.assertAlmostEqual(estimate['roughness'], 0.16 ** 0.25)
        self.assertIsNone(estimate['metallic'])
        self.assertEqual(estimate['source'], 'matcap_reflection_estimate')
        self.assertEqual(estimate['texture_reference'], 'chr300_f02')

    def test_texture_resolution_finds_canonical_common_area(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model = root / 'KB/map/map101/map101'
            copied = root / 'KB/map/map699/shared_c'
            canonical = root / 'KB/texture/common/area1/shared_c'
            model.parent.mkdir(parents=True)
            copied.parent.mkdir(parents=True)
            canonical.parent.mkdir(parents=True)
            model.write_bytes(ddm.MAGIC)
            copied.write_bytes(b'\x00xetcopy')
            canonical.write_bytes(b'\x00xetcanonical')
            found = ddm.resolve_texture_path('shared_c', model, root)
            self.assertEqual(found, canonical)

    def test_phong_shininess_becomes_gltf_perceptual_roughness(self):
        estimate = ddm.phong_to_pbr_estimate({'shininess': 30.0})
        self.assertAlmostEqual(estimate['roughness'], 0.5)
        self.assertEqual(estimate['roughness_formula'],
                         'pow(2 / (Ns + 2), 0.25)')

    def test_legacy_material_block_separates_diffuse_alpha_from_unknowns(self):
        values = (1.0, 0.8, 0.6, 1.0, 0.0, 0.0,
                  0.2, 0.3, 0.4, 0.0, 32.0)
        data = b'prefix' + struct.pack('>11f', *values) + b'suffix'
        phong = ddm.find_phong_parameters(data, 0, len(data))
        self.assertEqual(phong['offset'], len(b'prefix'))
        for actual, expected in zip(phong['diffuse'], (1.0, 0.8, 0.6)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(phong['diffuse_alpha'], 1.0)
        self.assertEqual(phong['unknown_after_diffuse'], [0.0, 0.0])
        self.assertAlmostEqual(phong['specular'][0], 0.2)
        self.assertEqual(phong['shininess'], 32.0)

    def test_normal_texture_green_channel_is_converted_from_directx(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is unavailable')
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            texture_path = root / 'normal.png'
            Image.new('RGBA', (1, 1), (10, 20, 30, 40)).save(texture_path)
            texture = {
                'output': 'normal.png',
                'role': 'normal',
                'conversion': {},
            }

            payload = ddm.export_texture_payload(root, texture)

            with Image.open(io.BytesIO(payload)) as converted:
                self.assertEqual(converted.convert('RGBA').getpixel((0, 0)),
                                 (10, 235, 30, 40))
            self.assertEqual(
                texture['conversion']['normal_y_conversion'],
                'directx_y_negative_to_gltf_y_positive',
            )

    def test_missing_map_phong_uses_same_map_median(self):
        materials = [
            {'index': 0, 'pbr_estimate': {'roughness': 0.4}},
            {'index': 1, 'pbr_estimate': None},
            {'index': 2, 'pbr_estimate': {'roughness': 0.6}},
        ]
        ddm.stabilize_map_pbr_estimates(materials)
        fallback = materials[1]['pbr_estimate']
        self.assertAlmostEqual(fallback['roughness'], 0.5)
        self.assertEqual(fallback['roughness_source'],
                         'same_map_material_median_fallback')
        self.assertEqual(fallback['confidence'], 0.25)

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

    def test_main_succeeds_when_directory_contains_no_ddm(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'input'
            source.mkdir()
            (source / 'not_a_model').write_bytes(b'\x00hcs' + b'\0' * 8)
            output = root / 'output'
            argv = ['ddm_to_3d.py', str(source), str(output), '--recursive']
            stdout = io.StringIO()

            with mock.patch('sys.argv', argv), contextlib.redirect_stdout(stdout):
                ddm.main()

            self.assertIn('0 decoded', stdout.getvalue())
            self.assertIn('1 non-DDM files skipped', stdout.getvalue())
            self.assertIn('No DDM file decoded.', stdout.getvalue())

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
            # A valid character rig graph: 0x88-byte header + 9 * 0x80 records.
            # boundary_count lives at offset 8, record_count at offset 0x80.
            rig = bytearray(0x88 + 9 * 0x80)
            rig[:4] = b'\0crg'
            struct.pack_into('>I', rig, 8, 10)
            struct.pack_into('>I', rig, 0x80, 9)
            package.write_bytes(bytes(rig))
            result = motion_decode.discover_character_motion(model)
            self.assertEqual(result['clip_count'], 2)
            self.assertEqual(result['segment_count'], 2)
            self.assertEqual(result['first_clip_offset'], 0xA0)
            self.assertEqual(result['sequence_end_offset'], 0xC0)
            self.assertEqual(result['skeleton_bone_ids'], [7, 3, 9])
            self.assertEqual(result['package_entry_count'], 9)
            self.assertFalse(result['decoded'])


if __name__ == '__main__':
    unittest.main()
