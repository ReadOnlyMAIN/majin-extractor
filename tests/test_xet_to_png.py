import sys
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "conversion"))

import xet_to_png


class XetLayoutTests(unittest.TestCase):
    def test_largest_mip_starts_at_0x88(self):
        width, height = 256, 128
        data = bytes(0x88 + xet_to_png.dxt1_size(width, height))

        offset, texture_format = xet_to_png.find_offset_and_format(
            data, width, height,
        )

        self.assertEqual(offset, 0x88)
        self.assertEqual(texture_format, "DXT1")

    def test_dxt1_transparent_selector_preserves_alpha(self):
        # c0 <= c1 selects DXT1's transparent mode; all selectors use entry 3.
        block = struct.pack("<HHI", 0, 0xffff, 0xffffffff)
        rgba = xet_to_png.decode_dxt1(block, 4, 4)
        self.assertEqual(set(rgba[3::4]), {0})

    def test_convert_one_does_not_flatten_alpha(self):
        data = bytearray(xet_to_png.TEXTURE_DATA_OFFSET)
        data[:4] = xet_to_png.MAGIC
        struct.pack_into(">2H", data, 0x80, 4, 4)
        # Add a full DXT1 mip chain so format detection selects DXT1.
        data.extend(struct.pack("<HHI", 0, 0xffff, 0xffffffff))
        data.extend(b"\0" * 16)
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "alpha_xet"
            output = Path(root) / "png"
            output.mkdir()
            source.write_bytes(data)
            self.assertTrue(xet_to_png.convert_one(source, output))
            image = Image.open(output / "alpha_xet.png")
            self.assertEqual(image.getchannel("A").getextrema(), (0, 0))

    def test_dxt5_standard_alpha_color_order(self):
        alpha = bytes((255, 0)) + b'\0' * 6
        color = struct.pack('<HHI', 0xf800, 0x07e0, 0)
        rgba = xet_to_png.decode_dxt5_xet(
            alpha + color, 4, 4, color_first=False,
        )
        self.assertEqual(tuple(rgba[:4]), (255, 0, 0, 255))

    def test_xet_storage_flag_selects_dxt5_order(self):
        data = bytearray(0x30)
        self.assertTrue(xet_to_png.dxt5_color_first(data))
        data[0x2f] = 0x80
        self.assertFalse(xet_to_png.dxt5_color_first(data))


if __name__ == "__main__":
    unittest.main()
