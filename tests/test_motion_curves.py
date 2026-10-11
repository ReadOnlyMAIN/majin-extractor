"""Phase 3 tests — scalar-clip decoding evidence-gated (REVERSE_MOTION.md).

Skip unless the gim103 corpus is present.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.conversion.motion import parse_motion_package
from tools.conversion.motion.curves import (
    decode_scalar_clip,
    resolve_scalar_channel_base,
)

ROOT = Path(__file__).resolve().parent.parent / \
    "game_files/decompressed/KB"
GIM103 = ROOT / "motionPackage" / "gim103" / "BigEndian" / "gim103"


@unittest.skipUnless(GIM103.is_file(), "gim103 corpus absent")
class TestGim103ScalarClips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = parse_motion_package(GIM103)
        cls.base = resolve_scalar_channel_base(cls.package)

    def test_base_is_the_proven_21(self):
        self.assertEqual(self.base, 21)

    def test_animated_blocks_decode_into_channels(self):
        decoded = {}
        for block in self.package.curve_blocks:
            clip = decode_scalar_clip(self.package, block, self.base)
            if clip is not None:
                decoded[block.block_index] = clip
        self.assertEqual(set(decoded), {0, 1, 2, 5, 6, 7})
        for clip in decoded.values():
            self.assertTrue(clip["channels"])
            self.assertGreater(clip["sample_count"], 1)
            for channel in clip["channels"]:
                times = channel["times"]
                self.assertEqual(len(times), len(channel["values"]))
                self.assertTrue(
                    all(a < b for a, b in zip(times, times[1:])))
                self.assertEqual(channel["interpolation"], "STEP")
                if channel["path"] == "rotation":
                    for value in channel["values"]:
                        norm = sum(c * c for c in value) ** 0.5
                        self.assertGreater(norm, 0.0)
                else:
                    for value in channel["values"]:
                        self.assertEqual(len(value), 3)

    def test_translation_matches_bind_at_the_first_key(self):
        skeleton = self.package.skeleton
        clip = decode_scalar_clip(
            self.package, self.package.curve_blocks[0], self.base)
        for channel in clip["channels"]:
            if channel["path"] == "translation":
                joint = channel["joint"]
                first = channel["values"][0]
                reference = skeleton.translations[joint]
                for component, reference_component in zip(first, reference):
                    self.assertAlmostEqual(
                        component, reference_component, delta=1e-3)


if __name__ == "__main__":
    unittest.main()