import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "research"))

from shader_inspect import extract_program_blobs, inspect_shader


class ShaderInspectTests(unittest.TestCase):
    def test_sampler_texture_unit_is_decoded(self):
        data = bytearray(0x240)
        data[:4] = b"0bxf"
        base = 0x80
        data[base:base + 4] = b"fxbf"
        struct.pack_into(">H", data, base + 6, 1)
        struct.pack_into(">I", data, base + 12, 0x180)
        struct.pack_into(">II", data, base + 0x18, 0x100, 0x40)
        struct.pack_into(">I", data, base + 0x44, 0x80)
        struct.pack_into(">I", data, base + 0x50, 0x90)
        struct.pack_into(">II", data, base + 0x64, 0, 16)
        struct.pack_into(">IIII", data, base + 0x80, 0x120, 4 << 22, 0, 0)
        data[base + 0x100:base + 0x10C] = b"TestProgram\0"
        sampler = b"textureSamplerEnvSphere\0"
        data[base + 0x120:base + 0x120 + len(sampler)] = sampler
        data[base + 0x180:base + 0x190] = bytes(range(16))

        report = inspect_shader(bytes(data))

        self.assertEqual(report["programs"][0]["name"], "TestProgram")
        self.assertEqual(report["programs"][0]["samplers"], [{
            "name": "textureSamplerEnvSphere",
            "texture_unit": 4,
        }])
        self.assertEqual(report["programs"][0]["parameters"], [{
            "name": "textureSamplerEnvSphere",
            "metadata_word": "0x01000000",
            "resource_index": 4,
            "value_index": 0,
            "value_offset": 0,
            "kind": "sampler",
            "texture_unit": 4,
        }])
        programs = extract_program_blobs(bytes(data))
        self.assertEqual(programs[0][0], "TestProgram")
        self.assertEqual(programs[0][1], bytes(range(16)))

    def test_fragment_constant_relocations_are_reported(self):
        data = bytearray(0x300)
        data[:4] = b"0bxf"
        base = 0x80
        data[base:base + 4] = b"fxbf"
        struct.pack_into(">H", data, base + 6, 1)
        struct.pack_into(">I", data, base + 12, 0x200)
        struct.pack_into(">II", data, base + 0x18, 0x100, 0x40)
        struct.pack_into(">II", data, base + 0x44, 0x80, 0)
        struct.pack_into(">I", data, base + 0x50, 0x90)
        struct.pack_into(">II", data, base + 0x64, 0, 0x40)
        struct.pack_into(">IIII", data, base + 0x80,
                         0x120, 0x00100401, 0, 0x160)
        data[base + 0x100:base + 0x10C] = b"TestProgram\0"
        data[base + 0x120:base + 0x12B] = b"matParam0\0"
        data[base + 0x160:base + 0x16C] = bytes.fromhex(
            "00100000 00300000 23232323"
        )

        parameter = inspect_shader(bytes(data))["programs"][0]["parameters"][0]

        self.assertEqual(parameter["fragment_constant_offsets"], [0x10, 0x30])


if __name__ == "__main__":
    unittest.main()
