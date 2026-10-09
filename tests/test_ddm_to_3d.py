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
from tools.conversion.motion import export as motion_decode

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

    def test_instance_asset_auto_mode_keeps_disconnected_foliage_together(self):
        source = Path(
            'game_files/decompressed/KB/instance/ins107/ins107'
        )
        if not source.exists():
            self.skipTest('ins107 fixture is unavailable')
        args = SimpleNamespace(
            vertex_count=None, position_offset=None, index_offset=None,
            index_count=None, no_textures=True, texture_root=None,
            final=True, scale=0.01, object_mode='auto',
        )
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                ddm.analyze_file(
                    source, output, args,
                    relative_path=Path('ins107/ins107'),
                )
            glb = (output / 'ins107/ins107.glb').read_bytes()
            length = struct.unpack_from('<I', glb, 12)[0]
            document = json.loads(glb[20:20 + length])
            self.assertEqual(len(document['nodes']), 1)
            self.assertEqual(len(document['meshes']), 1)
            self.assertEqual(
                document['nodes'][0]['extras']['separation'], 'single'
            )
            self.assertEqual(
                document['nodes'][0]['extras']['origin'], 'source_origin'
            )
            self.assertEqual(document['nodes'][0]['translation'], [0.0, 0.0, 0.0])

    def test_recursive_output_only_collapses_matching_container_name(self):
        root = Path('output')
        self.assertEqual(
            ddm.output_directory_for(
                Path('source/ins107/ins107'), root, Path('ins107/ins107'),
            ),
            root / 'ins107',
        )
        self.assertEqual(
            ddm.output_directory_for(
                Path('source/group/model'), root, Path('group/model'),
            ),
            root / 'group/model',
        )

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

    def test_phong_shininess_matches_ggx_half_power_width(self):
        estimate = ddm.phong_to_pbr_estimate({
            'shininess': 32.0,
            'specular': [0.8, 0.8, 0.8],
            'diffuse': [1.0, 0.5, 0.25],
        })
        self.assertAlmostEqual(estimate['roughness'], 0.5520096136264181)
        self.assertEqual(
            estimate['roughness_source'],
            'blinn_phong_to_ggx_half_power_match',
        )
        self.assertAlmostEqual(estimate['specular'], 0.4)
        self.assertEqual(estimate['diffuse'], [1.0, 0.5, 0.25])

        low_specular = ddm.phong_to_pbr_estimate({
            'shininess': 64.0,
            'specular': [0.1, 0.1, 0.1],
            'diffuse': [1.0, 1.0, 1.0],
        })
        self.assertAlmostEqual(low_specular['roughness'], 0.4709369682047095)
        self.assertAlmostEqual(low_specular['specular'], 0.05)

    def test_cutout_foliage_uses_broad_shader_family_roughness(self):
        estimate = ddm.phong_to_pbr_estimate({
            'shininess': 32.0,
            'specular': [0.8, 0.8, 0.8],
            'diffuse': [1.0, 1.0, 1.0],
        })
        ddm.apply_render_state_pbr_policy(estimate, {
            'mode': 'alpha_scissor',
            'shader_key': '0x00843105',
        })
        self.assertAlmostEqual(estimate['roughness'], 1.0)
        self.assertAlmostEqual(estimate['specular'], 0.0)
        self.assertAlmostEqual(
            estimate['roughness_from_phong'], 0.5520096136264181,
        )
        self.assertAlmostEqual(estimate['specular_from_phong'], 0.4)
        self.assertEqual(
            estimate['roughness_source'],
            'double_sided_cutout_foliage_shader_family',
        )

        for shader_key in ('0x0082b105', '0x0086b105'):
            instance_estimate = ddm.phong_to_pbr_estimate({
                'shininess': 32.0,
                'specular': [0.8, 0.8, 0.8],
            })
            ddm.apply_render_state_pbr_policy(instance_estimate, {
                'mode': 'opaque',
                'shader_key': shader_key,
            })
            self.assertEqual(instance_estimate['roughness'], 1.0)
            self.assertEqual(instance_estimate['specular'], 0.0)
            self.assertEqual(
                instance_estimate['roughness_source'],
                'double_sided_cutout_foliage_shader_family',
            )

        opaque = ddm.phong_to_pbr_estimate({
            'shininess': 32.0,
            'specular': [0.8, 0.8, 0.8],
        })
        ddm.apply_render_state_pbr_policy(opaque, {
            'mode': 'opaque',
            'shader_key': '0x00847125',
        })
        self.assertAlmostEqual(opaque['roughness'], 0.5520096136264181)

    def test_map_surface_shader_families_share_low_nonzero_specular(self):
        common = ddm.phong_to_pbr_estimate({
            'shininess': 32.0,
            'specular': [0.8, 0.8, 0.8],
        })
        zimen = ddm.phong_to_pbr_estimate({
            'shininess': 64.0,
            'specular': [0.1, 0.1, 0.1],
        })
        ddm.apply_render_state_pbr_policy(common, {
            'mode': 'opaque',
            'shader_key': '0x00847125',
        })
        ddm.apply_render_state_pbr_policy(zimen, {
            'mode': 'opaque',
            'shader_key': '0x00847725',
        })
        self.assertAlmostEqual(common['specular'], 0.2)
        self.assertAlmostEqual(zimen['specular'], 0.2)
        self.assertAlmostEqual(common['specular_from_phong'], 0.4)
        self.assertAlmostEqual(zimen['specular_from_phong'], 0.05)
        self.assertEqual(
            zimen['specular_source'],
            'shared_map_surface_visual_calibration',
        )

        disabled = ddm.phong_to_pbr_estimate({
            'shininess': 0.0,
            'specular': [0.0, 0.0, 0.0],
        })
        ddm.apply_render_state_pbr_policy(disabled, {
            'mode': 'opaque',
            'shader_key': '0x00847725',
        })
        self.assertEqual(disabled['specular'], 0.0)
        self.assertEqual(disabled['roughness'], 1.0)
        self.assertNotIn('specular_from_phong', disabled)

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

    def test_legacy_material_block_accepts_disabled_specular_lobe(self):
        values = (1.0, 1.0, 0.5, 1.0, 0.0, 0.0,
                  0.0, 0.0, 0.0, 0.0, 0.0)
        data = b'prefix' + struct.pack('>11f', *values) + b'suffix'
        phong = ddm.find_phong_parameters(data, 0, len(data))
        self.assertEqual(phong['offset'], len(b'prefix'))
        self.assertEqual(phong['specular'], [0.0, 0.0, 0.0])
        self.assertEqual(phong['shininess'], 0.0)
        estimate = ddm.phong_to_pbr_estimate(phong)
        self.assertEqual(estimate['roughness'], 1.0)
        self.assertEqual(estimate['specular'], 0.0)
        self.assertEqual(
            estimate['roughness_source'], 'disabled_legacy_specular_lobe',
        )

    def test_material_render_state_trailer_decodes_pipeline_mode(self):
        phong_offset = 9
        for value, mode in enumerate(('opaque', 'alpha_scissor', 'alpha_blend')):
            data = bytes((value,)) + bytes.fromhex('00843105 00000002') + b'payload'
            state = ddm.decode_material_render_state(
                data, {'offset': phong_offset},
            )
            self.assertEqual(state['mode_value'], value)
            self.assertEqual(state['mode'], mode)
            self.assertEqual(state['shader_key'], '0x00843105')
            self.assertEqual(state['variant_word'], 2)

    def test_material_render_state_accepts_zero_variant_word(self):
        data = b'\x00' + bytes.fromhex('0084733f 00000000') + b'payload'
        state = ddm.decode_material_render_state(data, {'offset': 9})
        self.assertEqual(state['mode'], 'opaque')
        self.assertEqual(state['shader_key'], '0x0084733f')
        self.assertEqual(state['variant_word'], 0)

    def test_material_render_state_rejects_unknown_layout(self):
        self.assertIsNone(ddm.decode_material_render_state(
            b'\x00' * 16, {'offset': 9},
        ))

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
        # The detector validates the 255-sum weight invariant of the first
        # vertex, so give it a real normalized weight (1,0,0,0 -> 255 total).
        first_vertex = offset + 24 + 2204 * 2 + 891 * 16
        data[first_vertex + 24:first_vertex + 28] = bytes((255, 0, 0, 0))
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
    def test_skinned_detection_rejects_false_attribute_signature(self):
        # A stray u32 == 8 inside content that has no valid weight bytes must be
        # skipped in favour of the real skinned group (this is chr500's bug: its
        # first signature is a false positive that summed to 123, not 255).
        def build_group(vertices, index_count, placeholder):
            blob = bytearray(ddm.MAGIC + struct.pack('>I', 3))
            blob.extend(b'\0' * 24)
            offset = len(blob)
            blob.extend(struct.pack('>6I', 1, 1, 8, vertices, 8, index_count))
            blob.extend(b'\0' * (index_count * 2 + vertices * 16))
            first_vertex = offset + 24 + index_count * 2 + vertices * 16
            blob.extend(b'\0' * (vertices * 28))
            blob[first_vertex + 24:first_vertex + 28] = placeholder
            blob.extend(b'\0' * 64)
            return blob, offset

        # False group first: weights sum to 123 (invalid).
        false_group, _ = build_group(5, 10, bytes((40, 40, 40, 3)))
        # Real group second: weights sum to 255 (valid). Its absolute offset in
        # the concatenated file accounts for the false group's length.
        real_group, real_offset_local = build_group(891, 2204, bytes((255, 0, 0, 0)))
        data = bytes(false_group) + bytes(real_group)
        real_offset = len(false_group) + real_offset_local

        header = ddm.find_skinned_geometry_header(data)

        self.assertIsNotNone(header)
        self.assertEqual(header['offset'], real_offset)
        self.assertEqual(header['vertex_count'], 891)



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
        corpus = Path(__file__).resolve().parent.parent / \
            'game_files/decompressed/KB'
        sequence = corpus / 'motionSequence/gim103/gim103'
        model = corpus / 'chara/gim103/gim103'
        if not sequence.is_file() or not model.is_file():
            self.skipTest('KB corpus motionSequence/motionPackage unavailable')
        result = motion_decode.discover_character_motion(model)
        self.assertIsNotNone(result)
        self.assertEqual(result['clip_count'], 4)
        self.assertEqual(result['skeleton_bone_ids'][0], 0)
        self.assertFalse(result['clip_names'][0].startswith('KB/'))


if __name__ == '__main__':
    unittest.main()
