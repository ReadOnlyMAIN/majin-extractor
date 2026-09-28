import struct
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.extraction.pak_extractor import (
    CRG_RESOURCE_TYPE,
    DDM_RESOURCE_TYPE,
    extract_pak,
    indexed_resources,
)


def raw_deflate(payload):
    encoder = zlib.compressobj(level=9, wbits=-15)
    return encoder.compress(payload) + encoder.flush()


def make_pak(resources):
    table_size = len(resources) * 0x120
    header = bytearray(0x88 + table_size)
    header[:4] = b"\x00kap"
    struct.pack_into(">II", header, 0x80, table_size, len(resources))
    bodies = []
    for index, resource in enumerate(resources):
        name, resource_type, payload, auxiliary_size, *options = resource
        encoding = options[0] if options else 1
        record = 0x88 + index * 0x120
        encoded = payload if encoding == 0 else raw_deflate(payload)
        stored = 128 + len(encoded)
        struct.pack_into(">II", header, record, len(payload), resource_type)
        encoded_name = name.encode("ascii")
        header[record + 16:record + 16 + len(encoded_name)] = encoded_name
        struct.pack_into(">4I", header, record + 272,
                         stored, auxiliary_size, 1, 0)
        block = bytearray(128)
        struct.pack_into(">II", block, 0, encoding, len(encoded))
        bodies.extend((block, encoded))
    return bytes(header) + b"".join(bodies)


class PakExtractorTests(unittest.TestCase):
    def test_indexed_resources_accepts_non_repeated_auxiliary_size(self):
        payload = b"\x00xet" + bytes(range(128))
        archive = make_pak([
            ("KB/chara/chr100/chr100_c02", 0x71B7E1B6, payload, 37),
        ])

        resources = indexed_resources(archive)

        self.assertEqual(resources[0][0], "KB/chara/chr100/chr100_c02")
        self.assertEqual(resources[0][2], payload)

    def test_indexed_resources_accepts_uncompressed_blocks(self):
        nested_pak = b"\x00kap" + bytes(range(128))
        archive = make_pak([
            ("KB/flash/package/fspe_jpn", 0x6F59420B,
             nested_pak, 73, 0),
        ])

        resources = indexed_resources(archive)

        self.assertEqual(resources[0][2], nested_pak)

    def test_indexed_resources_accepts_system_paths(self):
        shader = b"0bxf" + bytes(range(64))
        archive = make_pak([
            ("system/shader/HxMaterial", 0xA8AF8E0A,
             shader, len(shader)),
        ])

        resources = indexed_resources(archive)

        self.assertEqual(resources[0][0], "system/shader/HxMaterial")
        self.assertEqual(resources[0][2], shader)

    def test_duplicate_character_resources_preserve_ddm_and_crg(self):
        name = "KB/chara/chr300/chr300"
        ddm = b"\x00ddm" + bytes(range(64))
        crg = b"\x00crg" + bytes(range(48))
        archive = make_pak([
            (name, DDM_RESOURCE_TYPE, ddm, len(ddm)),
            (name, CRG_RESOURCE_TYPE, crg, len(crg)),
        ])

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            pak = root / "chr300.pak"
            output = root / "out"
            pak.write_bytes(archive)
            with ThreadPoolExecutor(max_workers=1) as executor:
                count = extract_pak(pak, output, executor, quiet=True)

            self.assertEqual(count, 2)
            self.assertEqual((output / name).read_bytes(), ddm)
            self.assertEqual((output / (name + ".crg")).read_bytes(), crg)


if __name__ == "__main__":
    unittest.main()
