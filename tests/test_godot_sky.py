import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from tools.conversion.godot import sky_to_godot


def xet_texture(width, height, payload):
    data = bytearray(0x88)
    data[:4] = b"\x00xet"
    data[0x80:0x82] = width.to_bytes(2, "big")
    data[0x82:0x84] = height.to_bytes(2, "big")
    data.extend(payload)
    return bytes(data)


class GodotSkyTests(unittest.TestCase):
    def test_builds_importable_asset_set_from_extracted_resources(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "extracted"
            (source / "KB/texture").mkdir(parents=True)
            (source / "KB/map/star").mkdir(parents=True)
            (source / "KB/shader").mkdir(parents=True)

            # One opaque red DXT1 block for the star fixture.
            dxt1 = b"\x00\xf8\x00\x00" + b"\x00" * 4
            # Four color-first XET DXT5 blocks for an 8x8 green texture.
            dxt5_block = b"\xe0\x07\x00\x00" + b"\x00" * 4
            dxt5_block += b"\xff\x00" + b"\x00" * 6
            dxt5 = dxt5_block * 4
            (source / "KB/map/star/sky_star").write_bytes(xet_texture(4, 4, dxt1))
            (source / "KB/texture/pro_cloud0").write_bytes(xet_texture(8, 8, dxt5))
            (source / "KB/texture/pro_cloud1").write_bytes(xet_texture(8, 8, dxt5))
            (source / "KB/shader/KbProcedualCloud").write_bytes(b"0bxf fixture")

            output = root / "majin_sky"
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = sky_to_godot.write_godot_sky(
                    source, output, Path("assets"),
                )

            self.assertEqual(manifest["textures"]["cloud0"]["format"], "DXT5")
            self.assertTrue(manifest["original_shader"]["found"])
            for name in (
                "textures/pro_cloud0.png", "textures/pro_cloud1.png",
                "textures/sky_star.png", "majin_day_night_sky.gdshader",
                "majin_day_night_sky_material.tres",
                "majin_day_night_sky.tres",
                "majin_day_night_environment.tres", "MajinDayNightSky.gd",
                "majin_day_night_sky_demo.tscn", "sky_manifest.json",
            ):
                self.assertTrue((output / name).is_file(), name)

            material = (output / "majin_day_night_sky_material.tres").read_text()
            self.assertIn("res://assets/majin_sky/textures/pro_cloud0.png", material)
            shader = (output / "majin_day_night_sky.gdshader").read_text()
            self.assertIn("shader_type sky", shader)
            self.assertIn("pcloudSunColor", shader)
            self.assertNotIn("const float PI", shader)
            self.assertNotIn("const float TAU", shader)
            self.assertIn("uniform float star_scale", shader)

    def test_missing_noise_texture_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)
            with self.assertRaisesRegex(FileNotFoundError, "use static.pak"):
                sky_to_godot.load_sky_resources(source)


if __name__ == "__main__":
    unittest.main()
