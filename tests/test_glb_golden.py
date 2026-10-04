"""Golden-output regression test for the DDM -> GLB export.

The hash below freezes the byte-exact GLB produced from a small, fully
synthetic multi-section DDM. It complements ``test_ddm_to_3d`` (which checks
the decoded report) and ``test_glb_export`` (which unit-tests the writer) by
guarding the *whole* pipeline against silent changes.

The GLB writer is deterministic: it embeds no timestamp, uses a fixed JSON
separator and preserves dict insertion order, so the SHA-256 is stable across
runs and platforms.

If an intentional change to the GLB layout makes this test fail, re-derive the
hash by running ``build_fixture`` + ``analyze_file`` and update
``GOLDEN_SHA256`` (and ``GOLDEN_SIZE``) accordingly.
"""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

from tools.conversion import ddm_to_3d as ddm


# Frozen hash of the reference export. Update only for intentional changes.
GOLDEN_SHA256 = "8831daa9bb6fb76d2bf68c41034ac3e5a563855e69ace20ca9a2986aa7f8279c"
GOLDEN_SIZE = 3480


def map_section(primitive, positions, indices, material=0):
    """Build one observed multi-section map DDM geometry block."""
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


def build_fixture():
    """Return the deterministic multi-section DDM used for the golden output.

    The two sections use disjoint positions so each reconstructs its own object
    and the fixture exercises multi-section decoding rather than the multipass
    duplicate collapsing (which is covered separately).
    """
    first = map_section(3, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    second = map_section(
        4,
        [(10, 0, 0), (11, 0, 0), (10, 1, 0), (11, 1, 0)],
        [0, 1, 2, 3],
        material=1,
    )
    return ddm.MAGIC + struct.pack('>I', 3) + first + second


def export_glb(fixture, root):
    args = SimpleNamespace(
        vertex_count=None, position_offset=None, index_offset=None,
        index_count=None, no_textures=True, texture_root=None,
        final=False, scale=1.0, object_mode='auto',
        material_mode='pbr', roughness=None,
    )
    source = Path(root) / 'map'
    source.write_bytes(fixture)
    output = Path(root) / 'out'
    with contextlib.redirect_stdout(io.StringIO()):
        ddm.analyze_file(source, output, args)
    return (output / 'map/map.glb')


class GlbGoldenTests(unittest.TestCase):
    def test_reference_export_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as root:
            glb = export_glb(build_fixture(), root)
            payload = glb.read_bytes()

        self.assertEqual(payload[:4], b'glTF')
        self.assertEqual(len(payload), GOLDEN_SIZE)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), GOLDEN_SHA256)

    def test_reference_export_has_expected_structure(self):
        with tempfile.TemporaryDirectory() as root:
            glb = export_glb(build_fixture(), root)
            payload = glb.read_bytes()

        length = struct.unpack_from('<I', payload, 12)[0]
        document = json.loads(payload[20:20 + length])
        # The two disjoint sections reconstruct two separate objects, one mesh
        # each, carrying one DDM material per primitive.
        self.assertEqual(len(document['scenes'][0]['nodes']), 2)
        self.assertEqual(len(document['meshes']), 2)
        self.assertEqual(
            sorted(len(mesh['primitives']) for mesh in document['meshes']), [1, 1]
        )
        total_triangles = sum(
            document['accessors'][primitive['indices']]['count'] // 3
            for mesh in document['meshes']
            for primitive in mesh['primitives']
        )
        self.assertEqual(total_triangles, 3)
        # Two DDM materials become two glTF materials.
        self.assertEqual(len(document['materials']), 2)

    def test_reference_export_is_repeatable(self):
        with tempfile.TemporaryDirectory() as root:
            first = export_glb(build_fixture(), root).read_bytes()
        with tempfile.TemporaryDirectory() as root:
            second = export_glb(build_fixture(), root).read_bytes()
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
