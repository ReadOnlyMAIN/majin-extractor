"""Independent bind, endpoint and binary-boundary oracles for compact TRS."""
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.conversion.ddm.skinned import decode_skinned_skeleton
from tools.conversion.motion.curves import DecodedCurveBlock, ScalarTrack, decode_curve_block
from tools.conversion.motion.export import decode_character_animations, discover_character_motion
from tools.conversion.motion.ik import IKChain, discover_ik_chains, forward_kinematics, solve_two_bone
from tools.conversion.motion.layout import decode_transform_layout
from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.pose import decode_transform_clip
from tools.conversion.motion.resource import UnsupportedMotion
from tools.conversion.motion.sequence import parse_motion_sequence, parse_sequence_index
from tools.conversion.motion.transforms import euler_quaternion, sample_scalar
from tools.conversion.motion.godot import write_animation_import_settings


ROOT = Path(__file__).resolve().parents[1] / 'game_files/decompressed/KB'


def track(index, mode, frames=(), values=(), tangents=()):
    return ScalarTrack(index, mode, list(frames), list(values), list(tangents))


class ScalarSamplingTests(unittest.TestCase):
    def test_implicit_group_is_not_scalar_zero(self):
        with self.assertRaises(UnsupportedMotion):
            sample_scalar(track(0, 1), 0)
        for mode, value in ((0, 0), (2, math.pi/2), (3, math.pi), (4, -math.pi/2)):
            self.assertEqual(sample_scalar(track(0, mode), 10), value)

    def test_linear_and_hermite_use_different_between_key_values(self):
        linear = track(0, 6, [0, 30], [0, 1])
        cubic = track(0, 7, [0, 30], [0, 1], [0, 0])
        self.assertAlmostEqual(sample_scalar(linear, 7.5), 0.25)
        self.assertAlmostEqual(sample_scalar(cubic, 7.5), 0.15625)
        # Derivatives are per second: x(t)=t over one second stays linear.
        cubic.auxiliaries = [1, 1]
        self.assertAlmostEqual(sample_scalar(cubic, 7.5), 0.25)

    def test_layout_rejects_explicit_group_with_implicit_component(self):
        tracks = [track(i, mode) for i, mode in enumerate([0, 0, 0, 1, 0, 1])]
        block = DecodedCurveBlock(len(tracks), 2, tracks, b'', b'', 0, 1)
        with self.assertRaises(UnsupportedMotion):
            decode_transform_layout(block, 1)

    def test_godot_reexport_preserves_existing_import_customizations(self):
        with tempfile.TemporaryDirectory() as directory:
            glb = Path(directory) / 'model.glb'
            first = write_animation_import_settings(glb)
            self.assertTrue(first['created'])
            config = Path(str(glb) + '.import')
            custom = config.read_text() + 'import_script/path="res://my_materials.gd"\n'
            config.write_text(custom)
            second = write_animation_import_settings(glb)
            self.assertTrue(second['preserved_existing'])
            self.assertEqual(config.read_text(), custom)

    def test_godot_import_patch_keeps_native_variant_values(self):
        with tempfile.TemporaryDirectory() as directory:
            glb = Path(directory) / 'model.glb'
            config = Path(str(glb) + '.import')
            custom = '''[remap]
importer="scene"
uid="uid://existing"
[params]
animation/fps=60
animation/remove_immutable_tracks=true
import_script/path="res://my_materials.gd"
_subresources={"nodes": {&"PATH:AnimationPlayer": {
"optimizer/enabled": true, "custom_path": NodePath("a,b/{keep}")},
"PATH:Skeleton3D": {"retarget/bone_map": Object(BoneMap, "resource_name": "keep")}},
"materials": {"unique": {"use_external/path": "res://keep.tres"}}}
'''
            config.write_text(custom)
            result = write_animation_import_settings(glb)
            updated = config.read_text()
            self.assertTrue(result['updated'])
            for preserved in ['uid="uid://existing"', 'import_script/path="res://my_materials.gd"',
                              'NodePath("a,b/{keep}")', 'Object(BoneMap, "resource_name": "keep")',
                              '"use_external/path": "res://keep.tres"']:
                self.assertIn(preserved, updated)
            self.assertIn('"optimizer/enabled": false', updated)
            self.assertIn('"compression/enabled": false', updated)
            self.assertIn('animation/fps=30.0', updated)
            self.assertIn('animation/remove_immutable_tracks=false', updated)

    def test_project_scene_preset_replaces_old_parameters_without_animation_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'project.godot').write_text('''config_version=5
[importer_defaults]
scene={"animation/fps": 60, "animation/remove_immutable_tracks": true,
"gltf/embedded_image_handling": 0, "import_script/path": "uid://user_script",
"_subresources": {"nodes": {"PATH:Mesh": {"custom": NodePath("keep/me")}}}}
[rendering]
renderer/rendering_method="gl_compatibility"
''')
            glb = project / 'original_files/decoded/model.glb'
            glb.parent.mkdir(parents=True)
            config = Path(str(glb) + '.import')
            config.write_text('''[remap]
importer="scene"
uid="uid://keep_asset_identity"
[params]
gltf/embedded_image_handling=1
import_script/path="res://old.gd"
animation/fps=30
old_override=true
''')
            result = write_animation_import_settings(glb)
            updated = config.read_text()
            self.assertEqual(result['preset'], 'importer_defaults/scene')
            self.assertIn('uid="uid://keep_asset_identity"', updated)
            self.assertIn('animation/fps=60', updated)
            self.assertIn('animation/remove_immutable_tracks=true', updated)
            self.assertIn('gltf/embedded_image_handling=0', updated)
            self.assertIn('import_script/path="uid://user_script"', updated)
            self.assertIn('NodePath("keep/me")', updated)
            self.assertNotIn('optimizer/enabled', updated)
            self.assertNotIn('old_override', updated)
            self.assertNotIn('res://old.gd', updated)
            write_animation_import_settings(glb)
            self.assertEqual(config.read_text(), updated)


class IKGeometryTests(unittest.TestCase):
    def test_two_bone_reach_preserves_lengths(self):
        parents = [None, 0, 1]
        translations = [(0, 0, 0), (2, 0, 0), (1, 0, 0)]
        rotations = [(0, 0, 0, 1)] * 3
        scales = [(1, 1, 1)] * 3
        diagnostics = solve_two_bone(IKChain(0, 1, 2, 8), (1, 1, 0),
                                     parents, translations, rotations, scales)
        self.assertFalse(diagnostics['clamped'])
        positions, _, _ = forward_kinematics(parents, translations, rotations, scales)
        for actual, expected in zip(positions[2], (1, 1, 0)):
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertAlmostEqual(math.dist(positions[0], positions[1]), 2)
        self.assertAlmostEqual(math.dist(positions[1], positions[2]), 1)

    def test_unreachable_target_is_reported(self):
        rotations = [(0, 0, 0, 1)] * 3
        diagnostics = solve_two_bone(IKChain(0, 1, 2, 8), (5, 0, 0),
                                     [None, 0, 1], [(0, 0, 0), (2, 0, 0), (1, 0, 0)],
                                     rotations, [(1, 1, 1)] * 3)
        self.assertTrue(diagnostics['clamped'])
        self.assertAlmostEqual(diagnostics['target_error'], 2)
        self.assertLess(diagnostics['reachable_error'], 1e-8)


@unittest.skipUnless((ROOT / 'motionPackage/chr301/BigEndian/chr301').is_file(), 'KB corpus absent')
class TransformCorpusTests(unittest.TestCase):
    def package(self, name):
        return parse_motion_package(ROOT / f'motionPackage/{name}/BigEndian/{name}')

    def test_variable_layout_pins_correct_humanoid_bones(self):
        package = self.package('chr301')
        groups = decode_transform_layout(decode_curve_block(package.curve_blocks[0]), 81).groups
        positions = {(package.skeleton.bone_ids[g.joint], g.path): g.scalar_start for g in groups}
        self.assertEqual(positions[(110, 'rotation')], 24)
        self.assertEqual(positions[(11, 'rotation')], 169)
        self.assertEqual(positions[(15, 'translation')], 182)
        self.assertEqual(positions[(202, 'translation')], 266)
        # A different segment compresses its implicit root rotation to one
        # descriptor, shifting later groups by two without a bone-ID rule.
        other = self.package('chr300')
        next_groups = decode_transform_layout(decode_curve_block(other.curve_blocks[1]), 62).groups
        next_positions = {(other.skeleton.bone_ids[g.joint], g.path): g.scalar_start for g in next_groups}
        self.assertEqual(next_positions[(110, 'rotation')], 22)

    def test_noncommuting_rotation_matches_independent_ddm_bind(self):
        package = self.package('chr301')
        ddm = decode_skinned_skeleton((ROOT / 'chara/chr301/chr301').read_bytes())
        joint = package.skeleton.bone_ids.index(220)
        group = next(g for g in decode_transform_layout(
            decode_curve_block(package.curve_blocks[0]), 81).groups
                     if g.joint == joint and g.path == 'rotation')
        angles = [sample_scalar(t, 0) for t in group.tracks]
        target = ddm['joints'][joint]['rotation']
        correct = abs(sum(a*b for a, b in zip(euler_quaternion(angles), target)))
        wrong_order = abs(sum(a*b for a, b in zip(euler_quaternion(angles, 'zyx'), target)))
        self.assertGreater(correct, 1 - 1e-10)
        self.assertLess(wrong_order, 0.99)

    def test_gim103_contains_real_hinge_rotation(self):
        package = self.package('gim103')
        clip = decode_transform_clip(package, package.curve_blocks[0])
        channel = next(c for c in clip['channels'] if c['joint'] == 3 and c['path'] == 'rotation')
        # Independent X-axis quaternion for the stored 0.8415768 rad hinge.
        angle = decode_curve_block(package.curve_blocks[0]).tracks[11].values[0]
        self.assertAlmostEqual(channel['values'][0][0], math.sin(angle/2))
        self.assertAlmostEqual(channel['values'][0][3], math.cos(angle/2))
        self.assertNotEqual(channel['values'][0], channel['values'][-1])

    def test_skeleton_hierarchy_and_ik_counts_across_corpus(self):
        count = 0
        for path in (ROOT / 'motionPackage').glob('*/BigEndian/*'):
            package = parse_motion_package(path)
            skeleton = package.skeleton
            self.assertEqual(len(skeleton.parent_indices), skeleton.bone_count)
            self.assertEqual(skeleton.parent_indices[0], None)
            self.assertEqual(len(discover_ik_chains(skeleton)), skeleton.chain_count)
            count += 1
        self.assertEqual(count, 193)

    def test_sequence_tables_have_exact_boundaries_and_record_coverage(self):
        count = 0
        for path in (ROOT / 'motionSequence').glob('*/*'):
            if path.is_file():
                parse_sequence_index(path)
                count += 1
        self.assertEqual(count, 157)

    def test_named_clips_use_package_slot_including_skeleton(self):
        info = discover_character_motion(ROOT / 'gimmick/gim103/gim103')
        clips = decode_character_animations(info)
        named = {clip['name']: clip['source_slot'] for clip in clips}
        self.assertEqual(named['g103_ctp_01_00'], 0)
        self.assertEqual(named['g103_ctp_03_00'], 2)
        self.assertEqual(named['g103_ctp_b_01_00'], 5)
        self.assertEqual(named['g103_ctp_b_03_00'], 7)
        self.assertEqual(len(clips), 8)
        self.assertEqual(info['export_diagnostics']['unresolved_states'], [])

    def test_qstm_does_not_include_next_record_type_word(self):
        path = ROOT / 'motionSequence/gim103/gim103'
        data = path.read_bytes()
        _, records = parse_motion_sequence(path)
        record = records[1]
        name_size = len(record.full_name.encode('ascii')) + 1
        record_start = record.qstm_offset - name_size - 20
        self.assertEqual(struct.unpack_from('>I', data, record_start)[0], record.type_id)
        self.assertEqual(records[0].qstm_offset + len(records[0].qstm.raw), record_start)

    def test_repeated_offsets_do_not_establish_animation_names(self):
        info = discover_character_motion(ROOT / 'chara/chr100/chr100')
        with patch('tools.conversion.motion.pose.decode_transform_clip', return_value={}):
            clips = decode_character_animations(info)
        names = {clip['name'] for clip in clips}
        self.assertEqual(len(clips), 477)
        self.assertEqual(sum(name.startswith('segment_') for name in names), 86)
        # User visual counterexample: physical segment 294 shows a yawning
        # idle, contradicting the proposed run_wall label from zero-span 293.
        self.assertIn('segment_294', names)
        self.assertNotIn('c100_0010_run_wall_01_00', names)
        self.assertNotIn('c100_0010_walk_01_00', names)
        for slot in (27, 111, 217):
            self.assertIn(f'segment_{slot:03d}', names)
        package = self.package('chr100')
        self.assertEqual(package.slots[293].start, package.slots[295].start)
        self.assertEqual(package.slots[293].size, 0)
        missing = info['export_diagnostics']['unresolved_states']
        self.assertEqual(len(missing), 23)
        self.assertEqual({state['reason_code'] for state in missing}, {'zero_storage_span'})

    def test_unselected_and_rejected_states_are_distinguished(self):
        info = discover_character_motion(ROOT / 'gimmick/gim103/gim103')
        with patch('tools.conversion.motion.pose.decode_transform_clip', return_value={}):
            decode_character_animations(info, animations='no_such_animation')
        self.assertEqual({s['reason_code'] for s in info['export_diagnostics']['unresolved_states']},
                         {'not_selected'})
        with patch('tools.conversion.motion.pose.decode_transform_clip',
                   side_effect=UnsupportedMotion('invalid curves')):
            decode_character_animations(info)
        self.assertEqual({s['reason_code'] for s in info['export_diagnostics']['unresolved_states']},
                         {'decode_rejected'})
        self.assertTrue(info['export_diagnostics']['rejected'])

    def test_chr301_baked_ik_reaches_targets(self):
        package = self.package('chr301')
        clip = decode_transform_clip(package, package.curve_blocks[0])
        self.assertEqual(clip['ik']['chains'], 4)
        self.assertEqual(clip['ik']['clamped_samples'], 0)
        self.assertLess(clip['ik']['max_target_error'], 1e-8)
        self.assertEqual(clip['scalar_mapping'], 'compact_trs_program')


if __name__ == '__main__':
    unittest.main()
