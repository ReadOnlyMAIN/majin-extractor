import struct
import unittest

from tools.research import rsx_fp_disasm


def encode_record(*words):
    decoded = struct.pack("<4I", *words)
    return b"".join(decoded[index:index + 2][::-1] for index in range(0, 16, 2))


class RsxFragmentDisassemblyTests(unittest.TestCase):
    def test_decodes_texture_instruction_and_inline_literal(self):
        # TEX H1, TEX0, texture unit 0.
        tex_destination = (23 << 24) | (0xF << 9) | (4 << 13) | (1 << 7) | (1 << 1)
        input_xy = 1 | (0 << 2) | (0 << 9) | (1 << 11) | (0 << 13) | (0 << 15)
        tex = encode_record(tex_destination, input_xy, 0, 0)

        # MAD R1.xyz, H1, CONST.x, CONST.y; final instruction.
        mad_destination = 1 | (1 << 1) | (0x7 << 9) | (4 << 24)
        h1 = (1 << 2) | (1 << 8) | (0 << 9) | (1 << 11) | (2 << 13) | (3 << 15)
        const_x = 2
        const_y = 2 | (1 << 11) | (1 << 13) | (1 << 15)
        mad = encode_record(mad_destination, h1, const_x, const_y)
        literal = encode_record(
            struct.unpack("<I", struct.pack("<f", 2.0))[0],
            struct.unpack("<I", struct.pack("<f", -1.0))[0], 0, 0,
        )

        instructions = rsx_fp_disasm.disassemble(tex + mad + literal)
        self.assertEqual([item["opcode"] for item in instructions], ["TEX", "MAD"])
        self.assertEqual(instructions[0]["texture_unit"], 0)
        self.assertEqual(instructions[0]["input_attribute"], "TEX0")
        self.assertEqual(instructions[1]["literal"], [2.0, -1.0, 0.0, 0.0])
        self.assertEqual(instructions[1]["literal_slot"], 2)


if __name__ == "__main__":
    unittest.main()
