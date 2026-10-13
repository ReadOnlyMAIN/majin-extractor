"""Phase 3 tests — scalar-clip decoding evidence-gated (REVERSE_MOTION.md).

Skip unless the corpus is present.  The goldens pin the Phase 2.2 probe
results: gim103 is the only package whose 7-scalar channel base survives
the consensus gate, and its exported clips are translation-only because
the rotation reading is refuted corpus-wide (zero unit quaternions).
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.conversion.motion import parse_motion_package
from tools.conversion.motion.curves import (
    carry_chains,
    carry_fidelity,
    decode_curve_block,
    decode_scalar_clip,
    resolve_scalar_channel_base,
)

ROOT = Path(__file__).resolve().parent.parent / \
    "game_files/decompressed/KB"
GIM103 = ROOT / "motionPackage" / "gim103" / "BigEndian" / "gim103"
CHR540 = ROOT / "motionPackage" / "chr540" / "BigEndian" / "chr540"
CHR300 = ROOT / "motionPackage" / "chr300" / "BigEndian" / "chr300"


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

    def test_clips_are_translation_only(self):
        """Rotation channels never resolve on gim103 (Phase 2.2 fact).

        The 7-scalar reading yields zero unit quaternions corpus-wide, so
        no gim103 block may emit a rotation channel: a quaternion built
        from these scalars would be a fabricated pose.
        """
        for block in self.package.curve_blocks:
            clip = decode_scalar_clip(self.package, block, self.base)
            if clip is None:
                continue
            for channel in clip["channels"]:
                self.assertEqual(channel["path"], "translation")


@unittest.skipUnless(CHR540.is_file(), "chr540 corpus absent")
class TestChr540LayoutRefuted(unittest.TestCase):
    """The 7-scalar mapping does not extend to chr540 (Phase 2.2 probes).

    Its 57 unique-owner bind anchors split across bases 205/181/317/…
    (blocks cover several channel sections), and the rotation channels
    built on the plurality base measured quaternion norms 38..2002 —
    garbage the pose oracle must refuse.
    """

    @classmethod
    def setUpClass(cls):
        cls.package = parse_motion_package(CHR540)

    def test_base_fails_the_consensus_gate(self):
        self.assertIsNone(resolve_scalar_channel_base(self.package))

    def test_quartet_blocks_refuse_the_pose_oracle(self):
        """Blocks holding complete rotation quartets must refuse.

        Measured invariant (Phase 2.2): zero unit quaternions exist among
        chr540's dated scalars, so any block whose 7-scalar reading forms
        complete rotation quartets fabricates non-unit quaternions and the
        pose oracle must reject it.
        """
        checked = 0
        for block in self.package.curve_blocks:
            try:
                decoded = decode_curve_block(block)
            except Exception:
                continue
            comps = {}
            for track in decoded.tracks:
                if not track.values:
                    continue
                bone, comp = divmod(track.scalar_index + 205, 7)
                comps.setdefault(bone, set()).add(comp)
            if not any(len(set(range(3, 7)) - owners) == 0
                       for owners in comps.values()):
                continue
            checked += 1
            self.assertIsNone(
                decode_scalar_clip(self.package, block, 205),
                f"block {block.block_index} fabricated quaternion channels")
        self.assertGreater(checked, 0)


@unittest.skipUnless(CHR300.is_file(), "chr300 corpus absent")
class TestCarryChains(unittest.TestCase):
    """Blocks are animation segments chained by the carry oracle."""

    @classmethod
    def setUpClass(cls):
        cls.package = parse_motion_package(CHR300)
        cls.blocks = []
        for block in cls.package.curve_blocks:
            try:
                cls.blocks.append((block, decode_curve_block(block)))
            except Exception:
                pass

    def test_chr300_blocks_form_long_chains(self):
        chains = carry_chains(self.blocks)
        sizes = sorted((len(chain) for chain in chains), reverse=True)
        self.assertGreaterEqual(len(chains), 2)
        self.assertGreaterEqual(sizes[0], 30)
        self.assertEqual(sum(sizes), len(self.blocks))

    def test_intra_chain_carry_is_near_perfect(self):
        chains = carry_chains(self.blocks)
        fidelities = []
        for chain in chains:
            for left, right in zip(chain, chain[1:]):
                hits, comparisons = carry_fidelity(
                    self.blocks[left][1], self.blocks[right][1])
                if comparisons:
                    fidelities.append(hits / comparisons)
        self.assertGreater(sum(fidelities) / len(fidelities), 0.85)
        self.assertGreaterEqual(min(fidelities), 0.5)


if __name__ == "__main__":
    unittest.main()