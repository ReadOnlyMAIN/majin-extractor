"""Coverage for unsupported DDM variant detection and reporting."""
import contextlib
import io
import math
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.conversion import ddm_to_3d as ddm
from tools.conversion.ddm.binary import (
    KNOWN_DDM_VERSIONS,
    UnsupportedDDMVariant,
    ddm_variant_name,
)
from tools.conversion.ddm.skinned import decode_skinned_skeleton


def args(**overrides):
    base = dict(
        vertex_count=None, position_offset=None, index_offset=None,
        index_count=None, no_textures=True, texture_root=None,
        final=False, scale=1.0, object_mode='auto', material_mode='pbr',
        roughness=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class VariantNameTests(unittest.TestCase):
    def test_known_version_reports_its_family(self):
        self.assertEqual(ddm_variant_name(3), KNOWN_DDM_VERSIONS[3])

    def test_unknown_version_reports_the_raw_word(self):
        self.assertEqual(ddm_variant_name(9), "unrecognized version 9")

    def test_missing_version_is_unknown(self):
        self.assertEqual(ddm_variant_name(None), "unknown DDM variant")


class UnsupportedDDMVariantTests(unittest.TestCase):
    def test_exception_carries_structured_attributes(self):
        exc = UnsupportedDDMVariant("bad layout", version=3)
        self.assertEqual(exc.version, 3)
        self.assertEqual(exc.variant, ddm_variant_name(3))
        self.assertEqual(exc.reason, "bad layout")
        self.assertIn("bad layout", str(exc))
        self.assertIsInstance(exc, RuntimeError)

    def test_variant_can_be_overridden_for_skinned_layouts(self):
        exc = UnsupportedDDMVariant("bad", variant="skinned character DDM")
        self.assertEqual(exc.variant, "skinned character DDM")
        self.assertIsNone(exc.version)


class AnalyzeFileVariantTests(unittest.TestCase):
    def _analyze(self, payload):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'x'
            source.write_bytes(payload)
            with contextlib.redirect_stdout(io.StringIO()):
                ddm.analyze_file(source, Path(root) / 'out', args())

    def test_undetectable_layout_raises_unsupported_variant(self):
        payload = ddm.MAGIC + struct.pack('>I', 3) + b'\x11' * 256
        with self.assertRaises(UnsupportedDDMVariant) as ctx:
            self._analyze(payload)
        self.assertEqual(ctx.exception.version, 3)
        self.assertEqual(ctx.exception.variant, KNOWN_DDM_VERSIONS[3])

    def test_unknown_version_is_reported_in_the_message(self):
        payload = ddm.MAGIC + struct.pack('>I', 9) + b'\x00' * 256
        with self.assertRaisesRegex(UnsupportedDDMVariant, 'unrecognized version 9'):
            self._analyze(payload)


class CliVariantReportingTests(unittest.TestCase):
    def test_unsupported_line_names_the_variant_and_reason(self):
        payload = ddm.MAGIC + struct.pack('>I', 3) + b'\x11' * 256
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'input'
            source.mkdir()
            (source / 'bad').write_bytes(payload)
            output = root / 'output'
            argv = ['ddm_to_3d.py', str(source), str(output), '--recursive']
            stdout = io.StringIO()
            with mock.patch('sys.argv', argv), contextlib.redirect_stdout(stdout):
                ddm.main()
        text = stdout.getvalue()
        self.assertIn('[UNSUPPORTED]', text)
        self.assertIn('v3 (map/static + skinned character)', text)
        self.assertIn('version=3', text)
        self.assertIn('1 unsupported', text)
        self.assertIn('0 decoded', text)


class SkeletonStorageOrderTests(unittest.TestCase):
    """REVERSE_DDM.md §28: DDM skeleton arrays and motion agreement."""

    CHARA_ROOT = Path('game_files/decompressed/KB/chara')
    # The "2-bone prop" family uses a different skeleton layout (§27.5b).
    PROP_FAMILY = {
        'chr700', 'chr705', 'chr710', 'chr715', 'chr720', 'chr721', 'chr722',
        'chr723', 'chr724', 'chr725', 'chr726', 'chr727', 'chr728', 'chr729',
        'chr740', 'chr741', 'chr742', 'chr743', 'chr744', 'chr745', 'chr746',
        'chr747', 'chr748', 'chr749', 'chr905', 'chr910', 'chr911', 'chr912',
        'chr920', 'chr930', 'chr970', 'chr980', 'chr990', 'chr991', 'chr992',
        'chr940', 'chr941', 'chr942', 'chr950', 'chr951', 'chr952', 'chr960',
    }

    def _bundles(self):
        for directory in sorted(self.CHARA_ROOT.iterdir()):
            main = directory / directory.name
            if main.is_file():
                yield main

    def test_every_rigged_bundle_decodes_a_sane_root(self):
        decoded = 0
        for main in self._bundles():
            data = main.read_bytes()
            if len(data) < 0x100 or data[:4] != b'\x00ddm':
                continue
            if main.stem in self.PROP_FAMILY:
                continue
            skeleton = decode_skinned_skeleton(data)
            root = skeleton['joints'][0]
            self.assertAlmostEqual(
                sum(x * x for x in root['rotation']), 1.0, places=5,
                msg=f'{main.name} root quaternion must be unit',
            )
            self.assertAlmostEqual(
                root['rotation'][0], 0.0, places=6,
                msg=f'{main.name} root must not rotate around X',
            )
            self.assertTrue(
                -1e-6 <= root['translation'][0] <= 1e-6,
                msg=f'{main.name} root x must be 0',
            )
            self.assertTrue(
                0.0 <= root['translation'][1] <= 1000.0,
                msg=f'{main.name} root height out of range',
            )
            decoded += 1
        self.assertGreaterEqual(decoded, 60, 'expected most bundles rigged')

    def test_chr900_extras_node_beyond_motion_scope(self):
        data = (
            self.CHARA_ROOT / 'chr900/chr900'
        ).read_bytes()
        skeleton = decode_skinned_skeleton(data)
        self.assertEqual(skeleton['transform_count'], 5)
        self.assertEqual(
            [j['global_id'] for j in skeleton['joints']], [0, 1, 2, 3, 4],
        )

    def test_chr500_corrected_hierarchy_has_no_parent_cycles(self):
        data = (self.CHARA_ROOT / 'chr500/chr500').read_bytes()
        skeleton = decode_skinned_skeleton(data)
        global_ids = [joint['global_id'] for joint in skeleton['joints']]
        parents = [
            None if joint['parent'] is None else global_ids[joint['parent']]
            for joint in skeleton['joints']
        ]
        loops = 0
        for start in range(len(global_ids)):
            seen = set()
            index = start
            while parents[index] is not None:
                if index in seen:
                    loops += 1
                    break
                seen.add(index)
                index = global_ids.index(parents[index])
        self.assertEqual(loops, 0, 'corrected chr500 hierarchy must be a tree')


if __name__ == '__main__':
    unittest.main()
