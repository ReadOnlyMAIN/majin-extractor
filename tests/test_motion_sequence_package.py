"""Unit tests of the motion parsers (corpus goldens, skipped when absent)."""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.conversion.motion import (
    UnsupportedMotion,
    curves,
    parse_motion_package,
    parse_motion_sequence,
)
from tools.conversion.motion.resource import read_header

CORPUS = Path(__file__).resolve().parent.parent / "game_files/decompressed/KB"


def corpus_available():
    return (CORPUS / "motionSequence").is_dir() \
        and (CORPUS / "motionPackage").is_dir()


@unittest.skipUnless(corpus_available(), "KB corpus not available")
class GoldenSequenceTests(unittest.TestCase):
    def test_chr100_golden(self):
        header, records = parse_motion_sequence(CORPUS / "motionSequence/chr100/chr100")
        self.assertEqual(header.version, 2)
        self.assertEqual(len(records), 414)
        self.assertEqual(records[0].short_name, "c100_0010_hold_climb_00")
        self.assertEqual(records[0].full_name,
                         "KB/motionSequence/chr100/" + records[0].short_name)
        self.assertTrue(all(r.qstm is not None for r in records))
        names = [r.short_name for r in records]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(names), 414)

    def test_gim103_golden(self):
        _h, records = parse_motion_sequence(CORPUS / "motionSequence/gim103/gim103")
        self.assertEqual(len(records), 4)

    def test_empty_sequence_is_allowed(self):
        header, records = parse_motion_sequence(
            CORPUS / "motionSequence/chr900/chr900")
        self.assertGreaterEqual(header.version, 1)
        self.assertEqual(records, [])


@unittest.skipUnless(corpus_available(), "KB corpus not available")
class GoldenPackageTests(unittest.TestCase):
    def test_skeleton_matches_ddm_bone_count(self):
        package = parse_motion_package(
            CORPUS / "motionPackage/chr100/BigEndian/chr100")
        self.assertEqual(package.skeleton.bone_count, 146)
        self.assertEqual(package.skeleton.chain_count, 4)
        # 457 logical curve slots, including 90 duplicated/empty offsets.
        self.assertEqual(len(package.curve_blocks), 367)
        skeleton = package.skeleton
        # bind-pose invariants
        for quat in skeleton.rotations:
            norm = sum(v * v for v in quat) ** 0.5
            self.assertAlmostEqual(norm, 1.0, delta=4e-4)
        ids = skeleton.bone_ids
        self.assertEqual(ids[0], 0)
        self.assertEqual(len(set(ids)), 146)
        self.assertTrue(all(0 <= i < 256 for i in ids))

    def test_curve_index_offsets(self):
        for name, slots, words in (("gim103", 8, 16),
                                   ("chr370", 38, 82)):
            package = parse_motion_package(
                CORPUS / f"motionPackage/{name}/BigEndian/{name}")
            index = package.curve_index
            self.assertEqual(index.slot_count, slots)
            self.assertEqual(index.entry_offsets[0], 4 + (slots + 1) * 4)
            self.assertEqual(index.entry_offsets[-1],
                             index.entry_offsets[0] + words * 4)
            self.assertEqual(len(index.words), words)
            self.assertEqual(len(index.groups), slots)
            if name == "gim103":
                self.assertTrue(all(group == [0, 0xFFFFFFFF]
                                    for group in index.groups))

    def test_duplicate_curve_slots_explain_count_mismatch(self):
        package = parse_motion_package(
            CORPUS / "motionPackage/chr540/BigEndian/chr540")
        self.assertEqual(package.curve_index.slot_count, 88)
        self.assertEqual(len(package.curve_blocks), 83)
        self.assertEqual(sum(slot.size == 0 for slot in package.slots), 5)

    def test_final_block_preserves_rig_controls(self):
        chr300 = parse_motion_package(
            CORPUS / "motionPackage/chr300/BigEndian/chr300")
        self.assertEqual(len(chr300.footer), 92)
        self.assertEqual(len(chr300.rig_controls.records), 15)
        self.assertTrue(chr300.rig_controls.marker.endswith(b"\xFF\x00\x00"))
        gim103 = parse_motion_package(
            CORPUS / "motionPackage/gim103/BigEndian/gim103")
        self.assertEqual(gim103.rig_controls.records, [])

    def test_truncated_file_raises(self):
        raw = (CORPUS / "motionPackage/chr100/BigEndian/chr100").read_bytes()
        with self.assertRaises(UnsupportedMotion):
            parse = read_header(raw[:0x40], source="x")
            parse.version  # noqa: B018


@unittest.skipUnless(corpus_available(), "KB corpus not available")
class CurveHeaderTests(unittest.TestCase):
    """Phase 2 prototype: constant-only curves and value-section locale."""

    def test_gim103_constant_curves(self):
        package = parse_motion_package(CORPUS / "motionPackage/gim103/BigEndian/gim103")
        constants = [
            (block.block_index, curves.constant_curve_value(block))
            for block in package.curve_blocks
            if curves.is_constant_curve(block)
        ]
        self.assertEqual(constants, [(3, 0.8415768146514893),
                                     (4, 0.8415768146514893)])
        # The mirrored pair differs by one post-descriptor byte.
        b4, b5 = [
            block for block in package.curve_blocks
            if curves.is_constant_curve(block)
        ]
        diff = [i for i in range(20) if b4.body[i] != b5.body[i]]
        self.assertEqual(len(diff), 1)

    def test_header_is_two_big_endian_u16_counts(self):
        package = parse_motion_package(CORPUS / "motionPackage/chr540/BigEndian/chr540")
        descriptor_count, sample_count = curves.curve_header_counts(
            package.curve_blocks[0])
        self.assertEqual((descriptor_count, sample_count), (1112, 80))
        self.assertEqual(len(curves.descriptor_codes(package.curve_blocks[0])),
                         descriptor_count)

    def test_gim103_descriptor_stream(self):
        package = parse_motion_package(CORPUS / "motionPackage/gim103/BigEndian/gim103")
        block = package.curve_blocks[3]
        self.assertEqual(curves.curve_header_counts(block), (36, 2))
        self.assertEqual(curves.descriptor_codes(block),
                         [0, 0] + [1] * 9 + [5, 0, 0] + [1] * 22)

    def test_gim103_scalar_storage_consumes_every_byte(self):
        package = parse_motion_package(CORPUS / "motionPackage/gim103/BigEndian/gim103")
        decoded = [curves.decode_curve_block(block)
                   for block in package.curve_blocks]
        self.assertEqual([clip.sample_count for clip in decoded],
                         [93, 42, 40, 2, 2, 93, 42, 40])
        constant = decoded[3].tracks[11]
        self.assertEqual(constant.mode, 5)
        self.assertAlmostEqual(constant.values[0], 0.8415768146514893)
    def test_gim103_channel_landmark(self):
        package = parse_motion_package(CORPUS / "motionPackage/gim103/BigEndian/gim103")
        animated = [
            block for block in package.curve_blocks
            if not curves.is_constant_curve(block)
        ]
        self.assertGreaterEqual(len(animated), 6)
        for block in animated:
            self.assertTrue(curves.has_channel_table_landmark(block.body))

    def test_chr100_bind_pose_oracle(self):
        package = parse_motion_package(CORPUS / "motionPackage/chr100/BigEndian/chr100")
        hits, total = curves.bind_pose_hits(package)
        # Most frozen channel values equal a bind-pose translation/quat
        # component of the verified motion skeleton (Phase 2.3 oracle).
        self.assertGreater(total, 100)
        self.assertGreater(hits, 60)

    def test_gim103_bind_pose_bone9(self):
        package = parse_motion_package(CORPUS / "motionPackage/gim103/BigEndian/gim103")
        hits, total = curves.bind_pose_hits(package)
        self.assertGreaterEqual(hits, 2)



if __name__ == "__main__":
    unittest.main()
