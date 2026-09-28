"""Focused regressions for character scalar-curve storage."""

from pathlib import Path
import math
import struct
import tempfile
import unittest

from tools.conversion.motion_decode import (
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
        modes = [1] * 13
        modes[5:9] = [5, 1, 1, 3]
        clip = self.scalar_clip(modes, 2, {}, struct.pack(">f", 2.0))
        decoded = decode_scalar_clip(clip)
        skeleton = {"joints": [
            {"rotation": (0, 0, 0, 1)},
            {"rotation": (0, 0, 2 ** -.5, 2 ** -.5)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [0], fps=30,
        )[0]

        self.assertEqual(channel["joint"], 0)
        self.assertEqual(channel["times"], [0])
        self.assertAlmostEqual(sum(x * x for x in channel["values"][0]), 1.0)
        self.assertAlmostEqual(channel["values"][0][0], math.sin(math.radians(1)))
        self.assertEqual(channel["values"][0][1:3], (0.0, 0.0))
        self.assertAlmostEqual(channel["values"][0][3], math.cos(math.radians(1)))
        self.assertEqual(channel["ignored_scalar_index"], 8)

        implicit = decode_experimental_joint_rotations(
            decoded, skeleton, [1], angle_units="radians",
        )[0]["values"][0]
        for actual, expected in zip(implicit, skeleton["joints"][1]["rotation"]):
            self.assertAlmostEqual(actual, expected)

if __name__ == "__main__":
    unittest.main()
