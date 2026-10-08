"""Focused regressions for character scalar-curve storage."""

from pathlib import Path
import math
import struct
import tempfile
import unittest

from tools.conversion.motion_decode import (
    UnsupportedMotion,
    apply_experimental_humanoid_ik,
    decode_character_animations,
    decode_character_rig_graph,
    decode_motion_metadata,
    decode_motion_skeleton,
    decode_experimental_root_translation,
    decode_root_yaw,
    decode_root_rotation,
    decode_experimental_joint_rotations,
    decode_scalar_clip,
    compare_scalar_layout,
    infer_humanoid_joint_scalar_starts,
    infer_rotation_group_start,
    remap_humanoid_scalar_starts,
)


class MotionDecodeTests(unittest.TestCase):
    @staticmethod
    def scalar_clip(modes, frame_count, key_frames, payload):
        descriptor = bytearray()
        for start in range(0, len(modes), 8):
            packed = sum(
                mode << (index * 3)
                for index, mode in enumerate(modes[start:start + 8])
            )
            descriptor.extend(packed.to_bytes(3, "big"))
        key_table = bytearray()
        for index, mode in enumerate(modes):
            if mode in (6, 7):
                interior = key_frames[index]
                key_table.append(len(interior))
                key_table.extend(interior)
        clip = bytearray(struct.pack(">HH", len(modes), frame_count))
        clip.extend(descriptor)
        clip.extend(key_table)
        clip.extend(b"\0" * (-len(clip) % 4))
        clip.extend(payload)
        return bytes(clip)

    def test_godot_ik_export_preserves_fk_and_exports_positions_only(self):
        bone_ids = (11, 13, 15, 41, 43, 45, 200, 201, 202, 210, 211,
                    212, 251, 66, 204, 217)
        chain_parents = {
            13: 11, 15: 13, 43: 41, 45: 43,
            201: 200, 202: 201, 211: 210, 212: 211,
        }
        by_id = {bone_id: index for index, bone_id in enumerate(bone_ids)}
        skeleton = {"joints": []}
        for index, bone_id in enumerate(bone_ids):
            parent_id = chain_parents.get(bone_id)
            skeleton["joints"].append({
                "index": index,
                "global_id": bone_id,
                "parent": by_id[parent_id] if parent_id is not None else None,
                "translation": (1.0, 0.0, 0.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            })
        scalar_starts = {
            by_id[251]: 0, by_id[66]: 8,
            by_id[204]: 16, by_id[217]: 24,
        }
        decoded = {
            "frame_count": 1,
            "scalar_count": 32,
            "tracks": [{"mode": 3} for _ in range(32)],
        }
        fk_channel = {
            "joint": by_id[11], "path": "rotation", "times": [0.0],
            "values": [(0.0, 0.0, 0.0, 1.0)],
            "interpolation": "LINEAR",
        }
        channels = [fk_channel]

        apply_experimental_humanoid_ik(
            decoded, skeleton, channels, scalar_starts,
            export_control_channels=True, bake_ik=False,
            target_orientation_mode="none",
        )

        self.assertEqual(fk_channel["values"], [(0.0, 0.0, 0.0, 1.0)])
        controls = [channel for channel in channels if channel.get("ik_control")]
        self.assertEqual(len(controls), 4)
        self.assertEqual({channel["path"] for channel in controls}, {"translation"})
        self.assertFalse(any(channel.get("ik_baked") for channel in channels))

    def test_decodes_constant_linear_and_tangent_curves(self):
        clip = self.scalar_clip(
            [5, 6, 7, 1], 5, {1: [2], 2: [1, 3]},
            struct.pack(
                ">12f", 10.0, 20.0, 21.0, 22.0,
                30.0, 0.1, 31.0, 0.2, 32.0, 0.3, 33.0, 0.4,
            ),
        )
        decoded = decode_scalar_clip(clip)

        self.assertEqual(decoded["scalar_count"], 4)
        self.assertEqual(decoded["tracks"][0]["values"], [10.0])
        self.assertEqual(decoded["tracks"][1]["frames"], [0, 2, 4])
        self.assertEqual(decoded["tracks"][1]["values"], [20.0, 21.0, 22.0])
        self.assertEqual(decoded["tracks"][2]["frames"], [0, 1, 3, 4])
        self.assertEqual(decoded["tracks"][2]["values"], [30.0, 31.0, 32.0, 33.0])
        for actual, expected in zip(
            decoded["tracks"][2]["tangents"], [0.1, 0.2, 0.3, 0.4],
        ):
            self.assertAlmostEqual(actual, expected)

    def test_accepts_nonzero_key_table_alignment_bytes(self):
        clip = bytearray(self.scalar_clip(
            [6, 1], 4, {0: [1]}, struct.pack(">3f", 1.0, 2.0, 3.0),
        ))
        # Descriptor end=7, count+time end=9, then three alignment bytes.
        clip[9:12] = bytes((0x21, 0x43, 0x65))

        decoded = decode_scalar_clip(bytes(clip))

        self.assertEqual(decoded["key_table_padding"], [0x21, 0x43, 0x65])
        self.assertEqual(decoded["tracks"][0]["values"], [1.0, 2.0, 3.0])

    def test_inspection_does_not_export_unproven_rig_channels(self):
        clip = self.scalar_clip([5], 2, {}, struct.pack(">f", 4.0))
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(clip)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [0, len(clip)],
            }
            animations = decode_character_animations(info, {"joints": []})

        self.assertEqual(animations, [])
        self.assertFalse(info["decoded"])
        self.assertTrue(info["inspected_clips"][0]["scalar_storage_decoded"])
        self.assertEqual(info["inspected_clips"][0]["mode_counts"]["5"], 1)

    def test_inspection_reports_root_transform_endpoints(self):
        clip = self.scalar_clip(
            [6, 0, 5, 5, 5, 1, 6, 1], 2,
            {0: [], 6: []},
            struct.pack(">7f", 0, math.pi / 2, 10, 20, 30, 0, math.pi / 2),
        )
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(clip)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [0, len(clip)],
            }
            skeleton = {"joints": [{"translation": (0, 0, 0),
                                      "rotation": (0, 0, 0, 1)}]}
            decode_character_animations(info, skeleton)

        transform = info["inspected_clips"][0]["root_transform"]
        self.assertEqual(transform["translation_initial"], (10, 20, 30))
        self.assertAlmostEqual(
            abs(transform["rotation_final"][1]), 1.0,
        )

    def test_decodes_ordered_motion_reference_skeleton(self):
        # Two-byte header, one hierarchy entry, IDs, alignment, then 7 floats
        # (local translation + quaternion) per joint.
        segment = bytearray((2, 3, 0, 0, 7, 9, 0, 0))
        segment.extend(struct.pack(">7f", 1, 2, 3, 0, 0, 0, 1))
        segment.extend(struct.pack(">7f", 4, 5, 6, 0, 0, 1, 0))

        skeleton = decode_motion_skeleton(bytes(segment))

        self.assertEqual(skeleton["version"], 3)
        self.assertEqual(skeleton["bone_ids"], [7, 9])
        self.assertIsNone(skeleton["joints"][0]["parent_index"])
        self.assertEqual(skeleton["joints"][1]["parent_index"], 0)
        self.assertEqual(skeleton["joints"][1]["hierarchy_flags"], 0)
        self.assertEqual(skeleton["joints"][1]["translation"], (4, 5, 6))

    def test_decodes_fixed_character_rig_graph_records(self):
        graph = bytearray(0x88 + 0x80)
        graph[:4] = b"\0crg"
        struct.pack_into(">I", graph, 8, 2)
        struct.pack_into(">I", graph, 0x80, 1)
        struct.pack_into(">8I", graph, 0x88, 3, 101, 250, 4, 1, 2, 1, 0)
        struct.pack_into(">f", graph, 0x88 + 8 * 4, -30.0)

        decoded = decode_character_rig_graph(bytes(graph))

        self.assertEqual(decoded["record_count"], 1)
        self.assertEqual(decoded["records"][0]["bone_id_a"], 101)
        self.assertEqual(decoded["records"][0]["bone_id_b"], 250)
        self.assertEqual(decoded["records"][0]["parameters"][0], -30.0)

    def test_decodes_variable_per_clip_metadata(self):
        segment = struct.pack(
            ">3I2I2B2I", 2, 12, 22,
            0x120, 0xFFFFFFFF,
            4, 7,
            0x8000, 0xFFFFFFFF,
        )

        decoded = decode_motion_metadata(segment)

        self.assertEqual(decoded["record_count"], 2)
        self.assertEqual(decoded["records"][0]["flags"], 0x120)
        self.assertEqual(decoded["records"][0]["event_data"], [4, 7])
        self.assertEqual(decoded["records"][1]["flags"], 0x8000)

    def test_compares_prefix_coded_scalar_layouts(self):
        short = self.scalar_clip([5, 1], 2, {}, struct.pack(">f", 2.0))
        long = self.scalar_clip([5, 1, 5], 3, {}, struct.pack(">2f", 4.0, 8.0))
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(short + long)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [0, len(short), len(short) + len(long)],
                "clip_name_slots": ["short", "long"],
            }
            compared = compare_scalar_layout(info, [0, 1], group_start=1)

        self.assertEqual(compared["canonical_scalar_count"], 3)
        self.assertEqual(compared["scalar_counts"], {2: 1, 3: 1})
        self.assertEqual(compared["clips"][0]["name"], "short")
        self.assertEqual(compared["slots"][2]["implicit_suffix_clip_count"], 1)
        self.assertEqual(compared["slots"][2]["value_max"], 8.0)

    def test_builds_experimental_root_translation(self):
        clip = self.scalar_clip(
            [1, 1, 6, 5, 5], 3, {2: [1]},
            struct.pack(">5f", 1, 2, 3, 20, 30),
        )
        decoded = decode_scalar_clip(clip)
        channel = decode_experimental_root_translation(
            decoded, {"joints": [{"translation": (10, 20, 30)}]}, fps=2,
        )
        self.assertEqual(channel["times"], [0, 0.5, 1])
        self.assertEqual(channel["values"], [(1, 20, 30), (2, 20, 30), (3, 20, 30)])

    def test_builds_root_yaw_from_scalar_zero(self):
        clip = self.scalar_clip(
            [6, 0], 3, {0: [1]},
            struct.pack(">3f", 0.0, math.pi / 2, math.pi),
        )

        channel = decode_root_yaw(decode_scalar_clip(clip), fps=2)

        self.assertEqual(channel["times"], [0, 0.5, 1])
        self.assertEqual(channel["values"][0], (0.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(channel["values"][1][1], 2 ** -0.5)
        self.assertAlmostEqual(channel["values"][2][1], 1.0)

    def test_composes_extracted_and_local_root_yaw(self):
        clip = self.scalar_clip(
            [6, 0, 1, 1, 1, 1, 6, 1], 2,
            {0: [], 6: []},
            struct.pack(">4f", 0.0, math.pi / 2, 0.0, math.pi / 2),
        )
        skeleton = {"joints": [{"rotation": (0, 0, 0, 1)}]}

        channel = decode_root_rotation(decode_scalar_clip(clip), skeleton)

        self.assertEqual(channel["source_scalar_indices"], [0, 5, 6, 7])
        self.assertAlmostEqual(abs(channel["values"][-1][1]), 1.0)
        self.assertAlmostEqual(channel["values"][-1][3], 0.0, places=6)

    def test_builds_normalized_continuous_experimental_rotations(self):
        modes = [1] * 8
        modes[2] = 5
        clip = self.scalar_clip(modes, 2, {}, struct.pack(">f", 2.0))
        decoded = decode_scalar_clip(clip)
        skeleton = {"joints": [
            {"rotation": (0, 0, 0, 1)},
            {"rotation": (0, 0, 2 ** -.5, 2 ** -.5)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [0], fps=30, angle_units="degrees",
        )[0]

        self.assertEqual(channel["joint"], 0)
        self.assertEqual(channel["times"], [0, 1 / 30])
        self.assertAlmostEqual(sum(x * x for x in channel["values"][0]), 1.0)
        self.assertAlmostEqual(channel["values"][0][0], -math.sin(math.radians(1)))
        self.assertEqual(channel["values"][0][1:3], (0.0, 0.0))
        self.assertAlmostEqual(channel["values"][0][3], math.cos(math.radians(1)))
        self.assertEqual(channel["source_scalar_indices"], [2, 3, 4])

        implicit = decode_experimental_joint_rotations(
            decoded, skeleton, [1], angle_units="radians",
        )[0]["values"][0]
        for actual, expected in zip(implicit, skeleton["joints"][1]["rotation"]):
            self.assertAlmostEqual(actual, expected)

    def test_converts_motion_global_rotation_to_exported_local_rotation(self):
        decoded = decode_scalar_clip(self.scalar_clip([1] * 6, 2, {}, b""))
        half = 2 ** -0.5
        skeleton = {"joints": [
            {"parent": None, "rotation": (0, 0, half, half)},
            {"parent": 0, "rotation": (0, 0, 0, 1)},
        ]}
        reference = {"joints": [
            {"rotation": (0, 0, half, half)},
            {"rotation": (0, 0, half, half)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [1], group_start=0,
            reference_skeleton=reference,
        )[0]

        for value in channel["values"]:
            for actual, expected in zip(value, (0, 0, 0, 1)):
                self.assertAlmostEqual(actual, expected)

    def test_decodes_structurally_bound_euler_as_absolute_local_rotation(self):
        decoded = decode_scalar_clip(self.scalar_clip(
            [5, 1, 1], 2, {}, struct.pack(">f", 1.0),
        ))
        skeleton = {"joints": [
            {"parent": None, "rotation": (0, 0, 0, 1)},
            {"parent": 0, "rotation": (0, 0, 0, 1)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [1], scalar_starts={1: 0},
            rotation_model="local_absolute",
        )[0]

        self.assertAlmostEqual(channel["values"][0][0], math.sin(0.5))
        self.assertAlmostEqual(channel["values"][0][3], math.cos(0.5))
        self.assertIn("local_absolute", channel["source_representation"])

    def test_cancels_reference_orientation_before_local_bind_composition(self):
        decoded = decode_scalar_clip(self.scalar_clip(
            [5, 1, 1], 2, {}, struct.pack(">f", 1.0),
        ))
        half = 2 ** -0.5
        skeleton = {"joints": [
            {"parent": None, "rotation": (0, 0, half, half)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [0], scalar_starts={0: 0},
            rotation_model="local_delta_post",
            reference_decoded=decoded, reference_frame=0,
        )[0]

        for actual, expected in zip(
            channel["values"][0], skeleton["joints"][0]["rotation"],
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(channel["rotation_reference_frame"], 0)

    def test_bakes_unskinned_duplicate_controller_to_deforming_joint(self):
        modes = [1] * 6
        modes[3] = 5
        decoded = decode_scalar_clip(
            self.scalar_clip(modes, 2, {}, struct.pack(">f", 2.0))
        )
        skeleton = {"joints": [
            {"parent": None, "rotation": (0, 0, 0, 1)},
            {"parent": None, "rotation": (0, 0, 0, 1)},
        ]}
        reference = {"joints": [
            {"parent_index": None, "translation": (1, 2, 3),
             "rotation": (0, 0, 0, 1)},
            {"parent_index": None, "translation": (1, 2, 3),
             "rotation": (0, 0, 0, 1)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [0], group_start=0,
            reference_skeleton=reference, deforming_joint_indices=[0],
            angle_units="degrees",
        )[0]

        self.assertEqual(channel["controller_joint"], 1)
        self.assertAlmostEqual(channel["values"][0][0], -math.sin(math.radians(1)))

        remapped = decode_experimental_joint_rotations(
            decoded, skeleton, [1], group_start=0,
            reference_skeleton=reference, axis_map="zyx",
            angle_units="degrees",
        )[0]
        self.assertEqual(remapped["source_axis_map"], "zyx")
        self.assertAlmostEqual(
            remapped["values"][0][2], -math.sin(math.radians(1)),
        )

        handed = decode_experimental_joint_rotations(
            decoded, skeleton, [1], group_start=0,
            reference_skeleton=reference, axis_map="zyx", axis_signs="---",
            angle_units="degrees",
        )[0]
        self.assertEqual(handed["source_axis_signs"], "---")
        self.assertAlmostEqual(
            handed["values"][0][2], math.sin(math.radians(1)),
        )

    def test_rejects_contiguous_rotation_suffix_with_auxiliary_triplets(self):
        full = self.scalar_clip([1] * 11, 2, {}, b"")
        short = self.scalar_clip([1] * 9, 2, {}, b"")

        with self.assertRaisesRegex(UnsupportedMotion, "auxiliary triplets"):
            infer_rotation_group_start(
                full + short, [0, len(full), len(full + short)], 2,
            )

    def test_binds_observed_humanoid_segments_around_auxiliary_triplets(self):
        bone_ids = [
            0, 1, 2, 101, 110, 120,
            10, 11, 35, 13, 37, 15, 251, 36, 34,
            40, 41, 65, 43, 67, 45, 250, 66, 64,
            4, 200, 206, 201, 208, 202, 203, 209, 207, 204,
            214, 210, 216, 211, 218, 212, 213, 219, 217,
        ]
        skeleton = {"joints": [
            {"global_id": bone_id} for bone_id in bone_ids
        ]}

        starts, layout = infer_humanoid_joint_scalar_starts(skeleton, 206)

        self.assertEqual(layout["triplet_count"], 66)
        self.assertEqual(layout["auxiliary_triplet_count"], 24)
        self.assertEqual(starts[1], 8)
        self.assertEqual(starts[6], 32)
        self.assertEqual(starts[15], 68)
        self.assertEqual(starts[24], 104)
        self.assertEqual(starts[25], 110)
        self.assertEqual(starts[34], 158)

        early_omitted = remap_humanoid_scalar_starts(
            skeleton, starts, 204, 206,
        )
        middle_omitted = remap_humanoid_scalar_starts(
            skeleton, starts, 202, 206,
        )
        both_omitted = remap_humanoid_scalar_starts(
            skeleton, starts, 200, 206,
        )
        self.assertEqual(early_omitted[6], 30)
        self.assertEqual(early_omitted[34], 156)
        self.assertEqual(middle_omitted[6], 32)
        self.assertEqual(middle_omitted[34], 156)
        self.assertEqual(both_omitted[6], 30)
        self.assertEqual(both_omitted[34], 154)

    def test_layout_report_preserves_empty_clip_slots(self):
        clip = self.scalar_clip([5], 2, {}, struct.pack(">f", 4.0))
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(clip)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [0, 0, len(clip)],
            }
            compared = compare_scalar_layout(info, [0, 1])

        self.assertEqual(compared["clip_count"], 2)
        self.assertEqual(compared["decoded_clip_count"], 1)
        self.assertEqual(compared["empty_clip_count"], 1)
        self.assertTrue(compared["clips"][0]["empty_clip_slot"])

if __name__ == "__main__":
    unittest.main()
