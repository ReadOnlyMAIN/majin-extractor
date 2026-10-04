"""Coverage for unsupported DDM variant detection and reporting."""
import contextlib
import io
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


if __name__ == '__main__':
    unittest.main()
