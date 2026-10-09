"""Focused regressions for character scalar-curve storage."""

from pathlib import Path
import math
import struct
import sys
import tempfile
import unittest

from tools.conversion.motion_decode import (
    UnsupportedMotion,
    apply_experimental_humanoid_ik,
    decode_character_animations,
    decode_character_rig_graph,
    decode_motion_metadata,
    decode_motion_state_records,
    decode_motion_skeleton,
    decode_experimental_root_translation,
    decode_root_yaw,
    decode_root_rotation,
    decode_experimental_joint_rotations,
    decode_scalar_clip,
    compare_scalar_layout,
    discover_character_motion,
    classify_reference_roles,
    infer_generic_joint_scalar_starts,
    infer_generic_root_prefix,
    infer_humanoid_joint_scalar_starts,
    infer_rotation_group_start,
    derive_humanoid_scalar_starts,
    collect_constant_anchors,
    root_chain_length,
    select_canonical_scalar_count,
    associate_motion_state_records,
    remap_humanoid_scalar_starts,
    _sample_linear,
)
from tools.conversion.ddm.skinned import decode_skinned_skeleton


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
        bone_ids = (11, 13, 15, 37, 41, 43, 45, 67, 200, 201, 202,
                    210, 211, 212, 204, 217)
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
            by_id[15]: 0, by_id[37]: 3,
            by_id[45]: 5, by_id[67]: 8,
            by_id[204]: 16, by_id[217]: 24,
        }
        decoded = {
            "frame_count": 1,
            "scalar_count": 32,
            "tracks": [{"mode": 3} for _ in range(32)],
        }
        expected_targets = {
            "ik_hand_l_target": (10.0, 11.0, 12.0),
            "ik_hand_r_target": (20.0, 21.0, 22.0),
            "ik_foot_l_target": (30.0, 31.0, 32.0),
            "ik_foot_r_target": (40.0, 41.0, 42.0),
        }
        for start, values in zip(
            (0, 5, 16, 26), expected_targets.values(),
        ):
            for scalar, value in enumerate(values, start):
                decoded["tracks"][scalar] = {"mode": 5, "values": [value]}
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
        self.assertEqual(
            {channel["ik_control"] for channel in controls},
            set(expected_targets),
        )
        self.assertEqual(
            {channel["ik_control"]: channel["values"][0]
             for channel in controls},
            expected_targets,
        )
        self.assertFalse(any(channel.get("ik_baked") for channel in channels))

        # A zero source-space orientation is an offset from the end effector's
        # bind basis, not an absolute identity basis in Godot space.
        half = 2 ** -0.5
        skeleton["joints"][by_id[15]]["rotation"] = (0.0, 0.0, half, half)
        zero_decoded = {
            "frame_count": 1,
            "scalar_count": 34,
            "tracks": [{"mode": 1} for _ in range(34)],
        }
        oriented_channels = []
        apply_experimental_humanoid_ik(
            zero_decoded, skeleton, oriented_channels, scalar_starts,
            export_control_channels=True, bake_ik=False,
            target_orientation_mode="source-row",
        )
        hand_rotation = next(
            channel for channel in oriented_channels
            if channel.get("ik_control") == "ik_hand_l_target"
            and channel["path"] == "rotation"
        )
        for actual, expected in zip(
            hand_rotation["values"][0], (0.0, 0.0, half, half),
        ):
            self.assertAlmostEqual(actual, expected)

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

    def test_mode7_uses_per_second_cubic_hermite_tangents(self):
        track = {
            "mode": 7,
            "frames": [0, 2],
            "values": [0.0, 1.0],
            "tangents": [30.0, 0.0],
        }

        # At alpha=.5, h10=.125 and the two-frame span is 2/30 s:
        # .5 from the values plus .25 from the first tangent.
        self.assertAlmostEqual(_sample_linear(track, 1, 0.0), 0.75)

    def test_payload_free_modes_are_cardinal_angle_constants(self):
        expected = {
            0: 12.5,
            1: 0.0,
            2: math.pi / 2.0,
            3: math.pi,
            4: -math.pi / 2.0,
        }
        for mode, value in expected.items():
            self.assertAlmostEqual(
                _sample_linear({"mode": mode}, 0, 12.5), value,
            )

    def test_accepts_nonzero_key_table_alignment_bytes(self):
        clip = bytearray(self.scalar_clip(
            [6, 1], 4, {0: [1]}, struct.pack(">3f", 1.0, 2.0, 3.0),
        ))
        # Descriptor end=7, count+time end=9, then three alignment bytes.
        clip[9:12] = bytes((0x21, 0x43, 0x65))

        decoded = decode_scalar_clip(bytes(clip))

        self.assertEqual(decoded["key_table_padding"], [0x21, 0x43, 0x65])
        self.assertEqual(decoded["tracks"][0]["values"], [1.0, 2.0, 3.0])

    def test_decodes_wide_key_times_for_long_clips(self):
        clip = bytearray(struct.pack(">HH", 1, 300))
        clip.extend((6).to_bytes(3, "big"))
        clip.extend(struct.pack(">3H", 2, 148, 192))
        while len(clip) % 4:
            clip.append(0)
        clip.extend(struct.pack(">4f", 1.0, 2.0, 3.0, 4.0))

        decoded = decode_scalar_clip(bytes(clip))

        self.assertTrue(decoded["wide_key_times"])
        self.assertEqual(decoded["tracks"][0]["frames"], [0, 148, 192, 299])

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

    def test_default_selection_inspects_every_animation_clip(self):
        clip = self.scalar_clip([5], 2, {}, struct.pack(">f", 4.0))
        clip_count = 5
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(clip * clip_count)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [
                    index * len(clip) for index in range(clip_count + 1)
                ],
            }
            decode_character_animations(info, {"joints": []})

        self.assertEqual(len(info["inspected_clips"]), clip_count)

    def test_generic_binding_exports_shared_root_spine_animation(self):
        # A five-joint contiguous root chain with no humanoid IDs must still be
        # bound by the generic root/spine rule (previously this depended on the
        # hard-coded [0,1,2,101,110] prefix).
        clip = self.scalar_clip([0, 0] + [1] * 18, 2, {}, b"")
        skeleton = {"joints": [
            {
                "global_id": bone_id,
                "parent": None if index == 0 else index - 1,
                "translation": (0.0, 1.0, 0.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            }
            for index, bone_id in enumerate((0, 1, 2, 101, 110))
        ]}
        with tempfile.TemporaryDirectory() as root:
            sequence = Path(root) / "sequence"
            sequence.write_bytes(clip)
            info = {
                "sequence_path": str(sequence),
                "animation_boundaries": [0, len(clip)],
            }
            animations = decode_character_animations(
                info, skeleton, experimental_root_motion=True,
                experimental_rotation_joints=list(range(5)),
            )

        self.assertEqual(len(animations), 1)
        self.assertEqual(
            {channel["joint"] for channel in animations[0]["channels"]},
            {0, 1, 2, 3, 4},
        )
        # The generic binder keys on the skeleton's contiguous root chain, not
        # on character names or bone IDs.
        self.assertEqual(
            info["humanoid_transform_layout"]["kind"],
            "generic_root_chain",
        )
        self.assertTrue(info["generic_rig_binding"])

    def test_select_canonical_scalar_count_skips_unaligned_maximum(self):
        # chr300 stores 247/249/251/253; the maximum (253) is not triplet
        # aligned, so the engine's aligned variant 251 must win.
        self.assertEqual(select_canonical_scalar_count([247, 249, 251, 253]),
                         (251, 8))
        # chr301/302/303 already had an aligned maximum: no behaviour change.
        self.assertEqual(select_canonical_scalar_count([332, 334, 336, 338]),
                         (338, 8))
        self.assertEqual(select_canonical_scalar_count([200, 202, 204, 206]),
                         (206, 8))

    def test_root_chain_length_is_structural(self):
        def joint(parent):
            return {"parent_index": parent}
        # 0->1->2->3 then a branch: chain length 4.
        reference = {"joints": [
            joint(None), joint(0), joint(1), joint(2), joint(1),
        ]}
        self.assertEqual(root_chain_length(reference), 4)

    def test_classify_reference_roles_marks_ik_effectors_as_positions(self):
        # The engine flags IK chains in the hierarchy byte: 0x03 start,
        # 0x04 middle, 0x08 effector. The effector is a model-space position.
        joints = [
            {"index": 0, "constraint_flags": 0x00},
            {"index": 1, "constraint_flags": 0x00},
            {"index": 2, "constraint_flags": 0x03},
            {"index": 3, "constraint_flags": 0x04},
            {"index": 4, "constraint_flags": 0x08},
        ]
        roles = classify_reference_roles({"joints": joints,
                                          "ik_chain_count": 1,
                                          "ik_chain_layout_valid": True})

        self.assertEqual(roles["ik_effectors"], [4])
        self.assertEqual(roles["ik_starts"], [2])
        self.assertEqual(roles["ik_middles"], [3])
        self.assertEqual(roles["fk_joints"], [0, 1])
        self.assertEqual(roles["roles"][4], "position")
        self.assertEqual(roles["roles"][2], "rotation")

    def test_generic_binding_uses_root_chain_and_ik_roles(self):
        joints = [
            {"global_id": 0, "parent_index": None, "translation": (0, 0, 0)},
            {"global_id": 1, "parent_index": 0, "translation": (0, 1, 0)},
            {"global_id": 2, "parent_index": 1, "translation": (0, 1, 0)},
            {"global_id": 5, "parent_index": 1, "translation": (0, 0, 0)},
        ]
        reference = {
            "joints": [
                {"index": 0, "constraint_flags": 0x00},
                {"index": 1, "constraint_flags": 0x00},
                {"index": 2, "constraint_flags": 0x00},
                {"index": 3, "constraint_flags": 0x08},
            ],
            "ik_chain_count": 0,
            "ik_chain_layout_valid": True,
        }
        starts, layout = infer_generic_joint_scalar_starts(
            {"joints": joints}, 20, reference,
        )

        # Root chain 0->1->2 binds joints 1 and 2 to triplets 0 and 1.
        self.assertEqual(starts, {1: 8, 2: 11})
        self.assertEqual(layout["kind"], "generic_root_chain")
        # Joint 3 is an IK effector but sits outside the chain, so it is not
        # bound yet; the chain stays conservative and name-free.
        self.assertEqual(layout["position_joints"], [])
        self.assertEqual(infer_generic_root_prefix(20)["translation_slots"],
                         (2, 3, 4))

    def test_rejects_short_clip_for_root_prefix(self):
        with self.assertRaises(UnsupportedMotion):
            infer_generic_root_prefix(5)

    def test_generic_binding_covers_every_character_family(self):
        """End-to-end: every character with a motion resource exports clips.

        Guards the generic binder across rig families (humanoid, boss, prop),
        not just the verified chr30x humanoids.
        """
        kb = Path("game_files/decompressed/KB/chara")
        if not kb.is_dir():
            self.skipTest("character fixtures are unavailable")
        exported = 0
        for entry in sorted(kb.iterdir()):
            model = entry / entry.name
            if not model.is_file():
                continue
            try:
                info = discover_character_motion(model)
            except UnsupportedMotion:
                continue
            if not info or not info.get("sequence_path"):
                continue
            if info.get("skeleton_segment_offset") is None:
                continue
            motion = Path(info["sequence_path"]).read_bytes()
            reference = decode_motion_skeleton(
                motion[info["skeleton_segment_offset"]:
                       info["first_clip_offset"]]
            )
            skeleton = {"joints": [
                {"index": joint["index"], "global_id": joint["bone_id"],
                 "parent": joint["parent_index"],
                 "translation": joint["translation"],
                 "rotation": joint["rotation"]}
                for joint in reference["joints"]
            ]}
            animations = decode_character_animations(
                info, skeleton, None, True,
                list(range(len(skeleton["joints"]))), "radians", None,
                "xyz", "+++", "local_delta_post", None, "end", "heading",
                False, False, False, "source-row",
            )
            self.assertTrue(
                animations,
                f"{entry.name} produced no animation with the generic binder",
            )
            exported += 1
        self.assertGreater(exported, 0)

    def test_per_clip_scalar_layout_derives_on_chr3x_humanoids(self):
        """Every chr30x clip derives its binding from invariant anchors.

        The channel order differs per clip (root-header and rig-control
        variants), so the count-arithmetic remap cannot express it; the
        anchor derivation must cover each clip structurally.
        """
        kb = Path("game_files/decompressed/KB/chara")
        if not kb.is_dir():
            self.skipTest("character fixtures are unavailable")
        for name in ("chr301", "chr302", "chr303"):
            model = kb / name / name
            info = discover_character_motion(model)
            self.assertTrue(info and info.get("sequence_path"))
            motion = Path(info["sequence_path"]).read_bytes()
            reference = decode_motion_skeleton(
                motion[info["skeleton_segment_offset"]:
                       info["first_clip_offset"]]
            )
            skeleton = {"joints": [
                {"index": joint["index"], "global_id": joint["bone_id"],
                 "parent": joint["parent_index"],
                 "translation": joint["translation"],
                 "rotation": joint["rotation"]}
                for joint in reference["joints"]
            ]}
            decode_character_animations(
                info, skeleton, None, True,
                list(range(len(skeleton["joints"]))), "radians", None,
                "xyz", "+++", "local_delta_post", None, "end", "heading",
                False, False, False, "source-row",
            )
            kinds = {
                (clip.get("per_clip_scalar_layout") or {}).get("kind")
                for clip in info["inspected_clips"]
                if clip.get("scalar_storage_decoded")
            }
            self.assertEqual(
                kinds, {"per_clip_anchors"},
                f"{name}: per-clip anchor derivation did not cover every clip",
            )


    def test_root_translation_accepts_implicit_bind_components(self):
        decoded = decode_scalar_clip(self.scalar_clip(
            [0, 0, 0, 5, 0], 2, {}, struct.pack(">f", 12.0),
        ))
        skeleton = {"joints": [{"translation": (1.0, 2.0, 3.0)}]}

        channel = decode_experimental_root_translation(decoded, skeleton)

        self.assertEqual(channel["values"], [(1.0, 12.0, 3.0)])

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
        # Default root rotation comes from slots 5..7. Here slot 6 carries the
        # 90-degree turn: sin(45) = cos(45) = sqrt(1/2).
        self.assertEqual(transform["rotation_initial"], (0.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(
            transform["rotation_final"][1], 2 ** -0.5,
        )
        self.assertAlmostEqual(
            transform["rotation_final"][3], 2 ** -0.5,
        )

    def test_decodes_ordered_motion_reference_skeleton(self):
        # Two-byte header, one hierarchy entry, IDs, alignment, then 7 floats
        # (local translation + quaternion) per joint.
        segment = bytearray((2, 3, 0, 0, 7, 9, 0, 0))
        segment.extend(struct.pack(">7f", 1, 2, 3, 0, 0, 0, 1))
        segment.extend(struct.pack(">7f", 4, 5, 6, 0, 0, 1, 0))

        skeleton = decode_motion_skeleton(bytes(segment))

        self.assertEqual(skeleton["version"], 3)
        self.assertEqual(skeleton["ik_chain_count"], 3)
        self.assertFalse(skeleton["ik_chain_layout_valid"])
        self.assertEqual(skeleton["bone_ids"], [7, 9])
        self.assertIsNone(skeleton["joints"][0]["parent_index"])
        self.assertEqual(skeleton["joints"][1]["parent_index"], 0)
        self.assertEqual(skeleton["joints"][1]["hierarchy_flags"], 0)
        self.assertEqual(skeleton["joints"][1]["translation"], (4, 5, 6))

    def test_motion_hierarchy_flags_form_ik_chains(self):
        # root + start(3) -> middle(4) -> effector(0x48) -> continuation(2)
        segment = bytearray((5, 1))
        segment.extend(bytes((0, 3, 1, 4, 2, 0x48, 3, 2)))
        segment.extend(bytes((0, 10, 11, 12, 13)))
        segment.extend(b"\0")  # align pose records to four bytes
        for _ in range(5):
            segment.extend(struct.pack(">7f", 0, 0, 0, 0, 0, 0, 1))

        skeleton = decode_motion_skeleton(bytes(segment))

        self.assertTrue(skeleton["ik_chain_layout_valid"])
        self.assertEqual(skeleton["ik_chains"][0]["bone_ids"], [10, 11, 12])
        self.assertEqual(
            skeleton["ik_chains"][0]["continuation_bone_ids"], [13],
        )
        self.assertEqual(skeleton["joints"][3]["ik_role"], "effector")
        self.assertEqual(skeleton["joints"][3]["ik_class_bits"], 0x40)

    def test_decodes_fixed_character_rig_graph_records(self):
        graph = bytearray(0x88 + 0x80)
        graph[:4] = b"\0crg"
        struct.pack_into(">I", graph, 8, 9)
        struct.pack_into(">I", graph, 0x80, 1)
        struct.pack_into(">8I", graph, 0x88, 3, 101, 250, 4, 1, 2, 1, 0)
        struct.pack_into(">f", graph, 0x88 + 8 * 4, -30.0)

        decoded = decode_character_rig_graph(bytes(graph))

        self.assertEqual(decoded["record_count"], 1)
        self.assertEqual(decoded["header_field_08"], 9)
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

    def test_associates_qstm_names_by_order_duration_and_empty_slots(self):
        prefix = b"KB/motionSequence/chr301/"

        def state(name, last_frame, kind=3):
            record = bytearray(prefix + name.encode("ascii") + b"\0qstm")
            marker = record.index(b"qstm")
            record.extend(b"\0" * (marker + 0x88 - len(record)))
            struct.pack_into(">II", record, marker + 0x80, last_frame, kind)
            return bytes(record)

        data = bytearray(0x84)
        data[:4] = b"psmr"
        data.extend(state("move_b", 44))
        data.extend(state("dead_pose", 54, 2))
        data.extend(state("move_f", 44))

        records = decode_motion_state_records(bytes(data))
        names, associations = associate_motion_state_records(
            records, [12, 45, 7, None, 45, 9],
        )

        self.assertEqual(
            names, [None, "move_b", None, "dead_pose", "move_f", None],
        )
        self.assertEqual(
            [entry["confidence"] for entry in associations],
            ["exact_frame_count", "empty_state_slot", "exact_frame_count"],
        )

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

    def test_root_rotation_uses_complete_local_triplet_by_default(self):
        # Slot 0 (heading) and slot 6 (auxiliary) both turn by +90 degrees, as
        # observed in the shared humanoid clips (research/MOTION_ANALYSIS.md).
        # The local Y curve already carries the 90-degree turn. The default
        # must use it once rather than adding scalar 0 a second time.
        clip = self.scalar_clip(
            [6, 0, 1, 1, 1, 1, 6, 1], 2,
            {0: [], 6: []},
            struct.pack(">4f", 0.0, math.pi / 2, 0.0, math.pi / 2),
        )
        skeleton = {"joints": [{"rotation": (0, 0, 0, 1)}]}

        channel = decode_root_rotation(decode_scalar_clip(clip), skeleton)

        self.assertEqual(channel["source_scalar_indices"], [0, 5, 6, 7])
        self.assertEqual(channel["values"][0], (0.0, 0.0, 0.0, 1.0))
        # 90-degree turn from slot 6: half-angle sin = cos = sqrt(1/2).
        half = 2 ** -0.5
        self.assertAlmostEqual(channel["values"][-1][1], half)
        self.assertAlmostEqual(channel["values"][-1][3], half)

    def test_combined_root_rotation_probe_double_counts_the_turn(self):
        # The rejected 'combined' probe adds scalar 0 to the already complete
        # slots 5..7 rotation. When slot 6 repeats the heading this doubles the
        # turn (90 -> 180 degrees). Keep that defect visible as a probe.
        clip = self.scalar_clip(
            [6, 0, 1, 1, 1, 1, 6, 1], 2,
            {0: [], 6: []},
            struct.pack(">4f", 0.0, math.pi / 2, 0.0, math.pi / 2),
        )
        skeleton = {"joints": [{"rotation": (0, 0, 0, 1)}]}

        combined = decode_root_rotation(
            decode_scalar_clip(clip), skeleton, source="combined",
        )
        local = decode_root_rotation(
            decode_scalar_clip(clip), skeleton, source="local",
        )

        # 180-degree turn: w collapses to zero.
        self.assertAlmostEqual(abs(combined["values"][-1][1]), 1.0)
        self.assertAlmostEqual(combined["values"][-1][3], 0.0, places=6)
        # 'local' alone already turns 90 degrees (the Y Euler duplicates slot0).
        half = 2 ** -0.5
        self.assertAlmostEqual(abs(local["values"][-1][1]), half)

    def test_chr301_boredom_exports_animated_root_rotation(self):
        model = Path("game_files/decompressed/KB/chara/chr301/chr301")
        if not model.is_file():
            self.skipTest("chr301 fixture is unavailable")
        info = discover_character_motion(model)
        skeleton = decode_skinned_skeleton(
            model.read_bytes(), info["skeleton_bone_ids"],
        )

        animations = decode_character_animations(
            info, skeleton, [0], experimental_root_motion=True,
        )
        root_rotation = next(
            channel for channel in animations[0]["channels"]
            if channel["joint"] == 0 and channel["path"] == "rotation"
        )

        self.assertEqual(root_rotation["source_scalar_indices"], [0, 5, 6, 7])
        self.assertIn("scalars 5..7", root_rotation["source_representation"])
        # boredom starts and ends neutral but contains a pronounced animated
        # lean/twist in the middle; the former heading-only export was static.
        maximum_vector = max(
            math.sqrt(sum(component * component for component in value[:3]))
            for value in root_rotation["values"]
        )
        self.assertGreater(maximum_vector, 0.15)

        # Scalar 0 is a redundant heading projection on turn clips. The real
        # root Y component in scalar 6 carries the same signed delta, so using
        # slots 5..7 alone retains every 90/180-degree turn exactly once.
        motion = Path(info["sequence_path"]).read_bytes()
        for index, name in enumerate(info["clip_name_slots"]):
            if not name or "turn_" not in name:
                continue
            start, end = info["animation_boundaries"][index:index + 2]
            if end <= start:
                continue
            decoded = decode_scalar_clip(motion[start:end])
            last = decoded["frame_count"] - 1
            heading_delta = (
                _sample_linear(decoded["tracks"][0], last, 0.0)
                - _sample_linear(decoded["tracks"][0], 0, 0.0)
            )
            root_y_delta = (
                _sample_linear(decoded["tracks"][6], last, 0.0)
                - _sample_linear(decoded["tracks"][6], 0, 0.0)
            )
            self.assertAlmostEqual(
                root_y_delta, heading_delta, places=3, msg=name,
            )

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
        self.assertAlmostEqual(channel["values"][0][0], math.sin(math.radians(1)))
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

    def test_local_bind_composition_changes_controller_axis_into_model_axis(self):
        """A stored axis belongs to the joint frame, not model space.

        A +90-degree bind rotation around Y maps the joint's local Z axis onto
        model X. This is the structural situation of the chr30x head joint;
        leaving the Euler delta in model Z is the former sideways-tilt bug.
        """
        angle = math.radians(30.0)
        decoded = decode_scalar_clip(self.scalar_clip(
            [1, 1, 5], 2, {}, struct.pack(">f", angle),
        ))
        half = 2 ** -0.5
        skeleton = {"joints": [
            {"parent": None, "rotation": (0.0, 0.0, 0.0, 1.0)},
            {"parent": 0, "rotation": (0.0, half, 0.0, half)},
        ]}

        local = decode_experimental_joint_rotations(
            decoded, skeleton, [1], scalar_starts={1: 0},
            rotation_model="local_delta_post",
        )[0]["values"][0]
        legacy = decode_experimental_joint_rotations(
            decoded, skeleton, [1], scalar_starts={1: 0},
            rotation_model="global_delta",
        )[0]["values"][0]

        def multiply(a, b):
            x1, y1, z1, w1 = a
            x2, y2, z2, w2 = b
            return (
                w1*x2 + x1*w2 + y1*z2 - z1*y2,
                w1*y2 - x1*z2 + y1*w2 + z1*x2,
                w1*z2 + x1*y2 - y1*x2 + z1*w2,
                w1*w2 - x1*x2 - y1*y2 - z1*z2,
            )

        def inverse(q):
            return (-q[0], -q[1], -q[2], q[3])

        bind = skeleton["joints"][1]["rotation"]
        local_model_delta = multiply(local, inverse(bind))
        legacy_model_delta = multiply(legacy, inverse(bind))
        self.assertGreater(abs(local_model_delta[0]), 0.25)
        self.assertAlmostEqual(local_model_delta[2], 0.0, places=6)
        self.assertGreater(abs(legacy_model_delta[2]), 0.25)
        self.assertAlmostEqual(legacy_model_delta[0], 0.0, places=6)

    def test_unwrapped_euler_is_not_reclassified_by_magnitude(self):
        decoded = decode_scalar_clip(self.scalar_clip(
            [5, 1, 1], 2, {}, struct.pack(">f", 7.0),
        ))
        skeleton = {"joints": [
            {"parent": None, "rotation": (0.0, 0.0, 0.0, 1.0)},
        ]}

        value = decode_experimental_joint_rotations(
            decoded, skeleton, [0], scalar_starts={0: 0},
            rotation_model="local_absolute",
        )[0]["values"][0]

        self.assertGreater(abs(value[0]), 0.3)
        self.assertAlmostEqual(value[3], math.cos(3.5))

    def test_chr301_head_flexion_uses_the_ddm_local_basis(self):
        """Corpus regression for the reported head-sideways symptom."""
        model = Path("game_files/decompressed/KB/chara/chr301/chr301")
        if not model.is_file():
            self.skipTest("chr301 fixture is unavailable")
        info = discover_character_motion(model)
        skeleton = decode_skinned_skeleton(
            model.read_bytes(), info["skeleton_bone_ids"],
        )
        animations = decode_character_animations(
            info, skeleton, [2], experimental_root_motion=True,
            experimental_rotation_joints=[1, 2, 3, 4],
        )
        channels = {
            channel["joint"]: channel
            for channel in animations[0]["channels"]
            if channel["path"] == "rotation"
        }
        self.assertEqual(channels[4]["source_scalar_indices"], [17, 18, 19])
        semantics = {
            group["global_id"]: group["kind"]
            for group in info["humanoid_transform_groups"]
        }
        self.assertEqual(semantics[15], "position")
        self.assertEqual(semantics[37], "unknown_controller")

        def multiply(a, b):
            x1, y1, z1, w1 = a
            x2, y2, z2, w2 = b
            return (
                w1*x2 + x1*w2 + y1*z2 - z1*y2,
                w1*y2 - x1*z2 + y1*w2 + z1*x2,
                w1*z2 + x1*y2 - y1*x2 + z1*w2,
                w1*w2 - x1*x2 - y1*y2 - z1*z2,
            )

        def inverse(q):
            return (-q[0], -q[1], -q[2], q[3])

        head_globals = []
        for frame in (0, -1):
            global_rotation = channels[0]["values"][frame]
            for joint in range(1, 5):
                global_rotation = multiply(
                    global_rotation, channels[joint]["values"][frame],
                )
            head_globals.append(global_rotation)
        delta = multiply(head_globals[1], inverse(head_globals[0]))
        vector_length = math.sqrt(sum(value * value for value in delta[:3]))
        axis = tuple(value / vector_length for value in delta[:3])
        # X is the model-space left/right hinge axis; Z would be a sideways
        # tilt. The now-complete root rotation contributes a large Y component,
        # but the erroneous lateral Z component remains nearly cancelled.
        self.assertGreater(abs(axis[0]), 0.6)
        self.assertLess(abs(axis[2]), 0.1)

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

    def test_cancels_stable_controller_basis_before_bind_composition(self):
        decoded = decode_scalar_clip(self.scalar_clip(
            [5, 1, 1], 2, {}, struct.pack(">f", math.pi / 2),
        ))
        half = 2 ** -0.5
        skeleton = {"joints": [
            {"parent": None, "rotation": (0, 0, half, half)},
        ]}

        channel = decode_experimental_joint_rotations(
            decoded, skeleton, [0], scalar_starts={0: 0},
            rotation_model="global_delta",
            rotation_bases={0: (math.pi / 2, 0.0, 0.0)},
        )[0]

        for actual, expected in zip(
            channel["values"][0], skeleton["joints"][0]["rotation"],
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(
            channel["rotation_basis"], (math.pi / 2, 0.0, 0.0),
        )

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
        self.assertAlmostEqual(channel["values"][0][0], math.sin(math.radians(1)))

        remapped = decode_experimental_joint_rotations(
            decoded, skeleton, [1], group_start=0,
            reference_skeleton=reference, axis_map="zyx",
            angle_units="degrees",
        )[0]
        self.assertEqual(remapped["source_axis_map"], "zyx")
        self.assertAlmostEqual(
            remapped["values"][0][2], math.sin(math.radians(1)),
        )

        handed = decode_experimental_joint_rotations(
            decoded, skeleton, [1], group_start=0,
            reference_skeleton=reference, axis_map="zyx", axis_signs="---",
            angle_units="degrees",
        )[0]
        self.assertEqual(handed["source_axis_signs"], "---")
        self.assertAlmostEqual(
            handed["values"][0][2], -math.sin(math.radians(1)),
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
        self.assertNotIn(5, starts)
        self.assertEqual(layout["unbound_accessory_joint_count"], 1)
        self.assertEqual(starts[1], 8)
        by_id = {bone_id: index for index, bone_id in enumerate(bone_ids)}
        self.assertEqual(
            [starts[by_id[bone_id]] for bone_id in
             (13, 35, 11, 10, 36, 251, 15, 37, 34)],
            list(range(32, 59, 3)),
        )
        self.assertEqual(
            [starts[by_id[bone_id]] for bone_id in
             (43, 65, 41, 40, 66, 250, 45, 67, 64)],
            list(range(71, 98, 3)),
        )
        self.assertEqual(starts[6], 41)
        self.assertEqual(starts[15], 80)
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
        # The two- and four-scalar variants drop trailing rig controls only, so
        # every bound joint keeps its canonical offset.
        for remapped in (early_omitted, middle_omitted):
            self.assertEqual(remapped, starts)
        # The six-scalar variant drops a two-scalar prefix as well, shifting the
        # whole transform list by two scalars.
        self.assertEqual(both_omitted[1], starts[1] - 2)
        self.assertEqual(both_omitted[6], starts[6] - 2)
        self.assertEqual(both_omitted[34], starts[34] - 2)

    def test_remap_keeps_joints_for_two_and_four_scalar_variants(self):
        """Only the six-scalar variant shifts the transform list.

        Verified against chr301/302/303: the two- and four-scalar variants drop
        trailing rig controls, so every bound joint keeps its canonical offset.
        """
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
        starts, _ = infer_humanoid_joint_scalar_starts(skeleton, 206)

        self.assertEqual(remap_humanoid_scalar_starts(skeleton, starts, 206, 206),
                         starts)
        self.assertEqual(remap_humanoid_scalar_starts(skeleton, starts, 204, 206),
                         starts)
        six = remap_humanoid_scalar_starts(skeleton, starts, 200, 206)
        for joint_index, canonical in starts.items():
            self.assertEqual(six[joint_index], canonical - 2)

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

    def test_humanoid_structural_patterns_are_coherent_across_chr30x(self):
        """Freeze the verified chr301/302/303 structural invariants.

        Uses the extracted game files when present (skipped otherwise). The
        probe helpers apply the shared humanoid binding, so this guards the
        whole structural pattern, not just scalar storage.
        """
        kb = Path("game_files/decompressed/KB/chara")
        required = [kb / name / name for name in ("chr301", "chr302", "chr303")]
        if not all(path.is_file() for path in required):
            self.skipTest("chr30x fixtures are unavailable")

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
        import motion_probe as probe

        names = ["chr301", "chr302", "chr303"]
        loaded = {name: probe.load(name) for name in names}
        structures = {
            name: probe.character_structure(*loaded[name]) for name in names
        }

        # Triplet alignment and skeleton sizes are the shared layout gate.
        self.assertEqual(structures["chr301"]["bone_count"], 81)
        self.assertEqual(structures["chr301"]["canonical_scalar_count"], 338)
        self.assertEqual(structures["chr302"]["canonical_scalar_count"], 206)
        self.assertEqual(structures["chr303"]["canonical_scalar_count"], 263)
        for name in names:
            self.assertTrue(structures[name]["triplet_aligned"])
            self.assertEqual(
                structures[name]["triplet_count"],
                (structures[name]["canonical_scalar_count"] - 8) // 3,
            )

        # chr301 alone carries the six accessory controls; the leading three
        # auxiliary triplets always start after the trunk.
        self.assertEqual(structures["chr301"]["auxiliary_triplet_count"], 30)
        self.assertEqual(
            structures["chr301"]["accessory_auxiliary_triplet_count"], 6,
        )
        self.assertEqual(
            structures["chr301"]["unbound_accessory_joint_count"], 39,
        )
        self.assertEqual(
            structures["chr301"]["unbound_accessory_triplet_count"], 45,
        )
        for name in names:
            self.assertEqual(
                structures[name]["leading_auxiliary_starts"], [20, 23, 26],
            )

        # Bound triplets of a shared clip are byte-identical across characters
        # once aligned by action suffix and keyed by bone ID.
        clip = "0010_turn_180_l_a_00"
        curves = {
            name: probe.bound_triplets_named(name, clip, loaded, structures)
            for name in names
        }
        self.assertTrue(curves["chr301"])
        for name in ("chr302", "chr303"):
            shared = set(curves["chr301"]) & set(curves[name])
            self.assertTrue(shared)
            for bone_id in shared:
                self.assertEqual(
                    curves["chr301"][bone_id], curves[name][bone_id],
                    f"{name} bone {bone_id} differs on {clip}",
                )

    def test_short_layout_clips_keep_canonical_joint_offsets(self):
        """Frame-0 regression: two/four-scalar variants must not shift joints.

        On the real chr301 stream the first trunk joint (bone 1) sits at its
        canonical scalar offset in every layout variant except the six-scalar
        one. Reading it two scalars early produced the wrong frame-0 rotations.
        """
        model = Path("game_files/decompressed/KB/chara/chr301/chr301")
        if not model.is_file():
            self.skipTest("chr301 fixture is unavailable")

        info = discover_character_motion(model)
        motion = Path(info["sequence_path"]).read_bytes()
        boundaries = info["animation_boundaries"]
        canonical = max(
            struct.unpack_from(">H", motion, start)[0]
            for start, end in zip(boundaries, boundaries[1:])
            if end - start >= 2
        )
        skeleton = decode_motion_skeleton(
            motion[info["skeleton_segment_offset"]:info["first_clip_offset"]],
        )
        joints = [
            {"global_id": bone_id, "index": index}
            for index, bone_id in enumerate(skeleton["bone_ids"])
        ]
        starts, _ = infer_humanoid_joint_scalar_starts(
            {"joints": joints}, canonical,
        )

        seen = {0: 0, 2: 0, 4: 0}
        for start, end in zip(boundaries, boundaries[1:]):
            if end <= start:
                continue
            decoded = decode_scalar_clip(motion[start:end])
            difference = canonical - decoded["scalar_count"]
            if difference not in (0, 2, 4):
                continue
            remapped = remap_humanoid_scalar_starts(
                {"joints": joints}, starts, decoded["scalar_count"], canonical,
            )
            self.assertEqual(remapped[1], starts[1])
            seen[difference] += 1
        for difference, count in seen.items():
            self.assertGreater(count, 0, f"no clip with difference {difference}")

    def test_per_clip_anchors_match_the_canonical_clip_by_identity(self):
        from tools.conversion.motion_decode import collect_constant_anchors
        animated = [0.5, 0.6, 0.7] * 6          # six mode-6 tracks, 3 keys each
        # Payload follows scalar index order: the leading mode-5 constant
        # (index 3) reads one word first, then the six animated triplets.
        canonic = struct.pack(
            ">21f", 7.5, *animated, 9.25, 4.5,
        )  # consecutive animated runs (payload order = scalar index order)
        canonical = MotionDecodeTests.scalar_clip(
            [1, 2, 0, 5, 0, 6, 6, 6, 6, 6, 6, 0, 5, 3, 0, 5, 0],
            5,
            {5: [2], 6: [2], 7: [2], 8: [2], 9: [2], 10: [2]},
            canonic,
        )
        decoded = decode_scalar_clip(canonical)
        anchors = collect_constant_anchors(decoded)
        # Two pi/2-style carriers collide?  No: mode 2 appears once.  Zero
        # carriers (mode 0/1) must be excluded, and every key unambiguous.
        keys = {key for _, key in anchors}
        self.assertEqual(
            keys,
            {(2, round(math.pi / 2, 4)), (5, 7.5), (5, 4.5),
             (5, 9.25), (3, round(math.pi, 4))},
        )
        starts, info = derive_humanoid_scalar_starts(
            decoded, {1: 5, 2: 8}, anchors,
        )
        self.assertEqual(starts, {1: 5, 2: 8})
        self.assertEqual(info["kind"], "per_clip_anchors")

    def test_per_clip_anchors_track_block_deletions(self):
        # The engine drops whole optional blocks; when a block is deleted
        # between two invariant anchor constants, every bound joint whose
        # canonical start lies after the deletion shifts by the block size,
        # while earlier joints keep their canonical offset. Here a scalar at
        # index 7 sits between joint 1's triplet (4..6) and the anchors
        # 8/9/13 that certify the later shift.
        canonical_modes = [1, 2, 5, 0, 6, 6, 6, 0, 5, 3, 6, 6, 6, 5, 0, 0]
        animated = [0.5, 0.6, 0.7] * 6          # six mode-6 tracks, 3 keys each

        payload = struct.pack(
            ">21f", 7.5, *animated[:9], 9.25, *animated[9:], 4.5,
        )  # payload follows scalar-index order (interleaved constants)
        canonical = MotionDecodeTests.scalar_clip(
            canonical_modes, 5,
            {4: [2], 5: [2], 6: [2], 10: [2], 11: [2], 12: [2]},
            payload,
        )
        decoded = decode_scalar_clip(canonical)
        anchors = collect_constant_anchors(decoded)
        self.assertEqual(
            sorted(anchors),
            sorted([(1, (2, round(math.pi / 2, 4))), (2, (5, 7.5)),
                    (8, (5, 9.25)), (9, (3, round(math.pi, 4))),
                    (13, (5, 4.5))]),
        )
        # The canonical clip itself must derive to the identity binding.
        starts_i, _ = derive_humanoid_scalar_starts(
            decoded, {1: 4, 2: 10}, anchors,
        )
        self.assertEqual(starts_i, {1: 4, 2: 10})
        variant_modes = [m for index, m in enumerate(canonical_modes)
                         if index != 7]
        variant = MotionDecodeTests.scalar_clip(
            variant_modes, 5,
            {4: [2], 5: [2], 6: [2], 9: [2], 10: [2], 11: [2]},
            payload,
        )
        decoded_variant = decode_scalar_clip(variant)

        starts_v, _ = derive_humanoid_scalar_starts(
            decoded_variant, {1: 4, 2: 10}, anchors,
        )
        self.assertEqual(starts_v, {1: 4, 2: 9})

    def test_humanoid_ik_targets_sextet_scan_finds_and_rejects(self):
        from tools.conversion.motion_decode import derive_humanoid_ik_targets
        joints = [{"index": 0, "global_id": 15},
                  {"index": 1, "global_id": 37}]
        skeleton = {"joints": joints}
        scalar_starts = {0: 10, 1: 13}
        decoded = {
            "frame_count": 3, "scalar_count": 24,
            "tracks": [{"mode": 1} for _ in range(24)],
        }
        # Interleaved stride controller shifts the sextet: it opens two
        # scalars into the bone-15 slot (start+2).
        for index, value in enumerate((24.1, 7.3, 22.8, 0.3, -0.5, 0.1), 12):
            decoded["tracks"][index] = {"mode": 7, "values": [value],
                                        "frames": [0]}
        target_specs = derive_humanoid_ik_targets(decoded, skeleton,
                                                  scalar_starts)
        self.assertEqual(
            [(spec["chain"], spec["target_start"],
              spec["orientation_start"]) for spec in target_specs],
            [((11, 13, 15), 12, 15)],
        )
        self.assertEqual(target_specs[0]["frame0_target"],
                         (24.1, 7.3, 22.8))

        # Without an animated sextet the scan refuses rather than guessing.
        empty = {
            "frame_count": 3, "scalar_count": 24,
            "tracks": [{"mode": 1} for _ in range(24)],
        }
        with self.assertRaises(UnsupportedMotion):
            derive_humanoid_ik_targets(empty, skeleton, scalar_starts)

    def test_per_clip_anchors_reject_ambiguous_streams(self):
        # Without unambiguous constants, no derivation: the caller keeps the
        # legacy count arithmetic instead of silently guessing.
        from tools.conversion.motion_decode import collect_constant_anchors
        clip = MotionDecodeTests.scalar_clip(
            [0] * 12, 5, {}, b"",
        )
        decoded = decode_scalar_clip(clip)
        self.assertEqual(collect_constant_anchors(decoded), [])
        with self.assertRaises(UnsupportedMotion):
            derive_humanoid_scalar_starts(decoded, {1: 5}, [])

if __name__ == "__main__":
    unittest.main()
