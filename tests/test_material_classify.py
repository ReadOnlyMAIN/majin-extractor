"""Coverage for material texture-role classification and its provenance."""
import unittest

from tools.conversion.ddm.materials import (
    classify_material_textures,
    texture_suffix_role,
)


def texture(reference, *, width=64, height=64, content=None, conversion=True):
    """Build a texture dict shaped like resolve_material_textures produces."""
    item = {"slot": 0, "reference": reference, "role": "unresolved"}
    if not conversion:
        item["conversion"] = None
        return item
    item["conversion"] = {
        "width": width,
        "height": height,
        "content": content or {},
    }
    return item


def content(blue=0.0, red=0.0, grayscale=0.0):
    return {
        "blue_normal_ratio": blue,
        "red_mask_ratio": red,
        "grayscale_ratio": grayscale,
    }


class SuffixRoleTests(unittest.TestCase):
    def test_color_suffix_is_a_diffuse_hint(self):
        self.assertEqual(texture_suffix_role("chr300_c01"), "diffuse")
        self.assertEqual(texture_suffix_role("area1/ground_c"), "diffuse")

    def test_normal_suffix_is_a_normal_hint(self):
        self.assertEqual(texture_suffix_role("chr300_n01"), "normal")
        self.assertEqual(texture_suffix_role("chr300_n"), "normal")

    def test_matcap_suffix_is_a_matcap_hint(self):
        self.assertEqual(texture_suffix_role("chr300_f"), "matcap")
        self.assertEqual(texture_suffix_role("chr300_f02"), "matcap")

    def test_utility_suffix_is_a_utility_hint(self):
        self.assertEqual(texture_suffix_role("chr300_u01"), "utility_mask")

    def test_unknown_suffix_has_no_hint(self):
        self.assertIsNone(texture_suffix_role("chr300_x01"))
        self.assertIsNone(texture_suffix_role("atlas"))

    def test_windows_separators_are_normalised(self):
        self.assertEqual(texture_suffix_role("chara\\chr300\\chr300_n01"), "normal")


class ClassifyMaterialTexturesTests(unittest.TestCase):
    def test_color_suffix_wins_diffuse_over_larger_auxiliary(self):
        small = texture("chr300_c01", width=16, height=16, content=content())
        large = texture("chr300_extra", width=512, height=512, content=content())
        textures = [small, large]
        classify_material_textures(textures)
        self.assertEqual(small["role"], "diffuse")
        self.assertEqual(small["role_source"], "suffix+size")
        self.assertEqual(large["role"], "auxiliary")

    def test_largest_auxiliary_is_the_size_fallback_diffuse(self):
        small = texture("a", width=16, height=16, content=content())
        large = texture("b", width=256, height=256, content=content())
        textures = [small, large]
        classify_material_textures(textures)
        self.assertEqual(large["role"], "diffuse")
        self.assertEqual(large["role_source"], "size_fallback")
        self.assertEqual(small["role"], "auxiliary")

    def test_normal_suffix_and_blue_content_agree(self):
        item = texture("chr300_n01", content=content(blue=0.9))
        classify_material_textures([item])
        self.assertEqual(item["role"], "normal")
        self.assertEqual(item["role_source"], "suffix+content")

    def test_blue_content_confirms_normal_without_suffix(self):
        item = texture("chr300_weird", content=content(blue=0.85))
        classify_material_textures([item])
        self.assertEqual(item["role"], "normal")
        self.assertEqual(item["role_source"], "content")

    def test_normal_suffix_wins_when_content_is_borderline(self):
        # A genuine normal map that is not overwhelmingly blue must still be a
        # normal map because the suffix is a strong naming hint.
        item = texture("chr300_n01", content=content(blue=0.2))
        classify_material_textures([item])
        self.assertEqual(item["role"], "normal")
        self.assertEqual(item["role_source"], "suffix")

    def test_matcap_suffix_never_becomes_albedo(self):
        big_flat = texture("chr300_f", width=512, height=512, content=content())
        color = texture("chr300_c01", width=16, height=16, content=content())
        textures = [big_flat, color]
        classify_material_textures(textures)
        self.assertEqual(big_flat["role"], "matcap")
        self.assertEqual(big_flat["role_source"], "suffix")
        self.assertEqual(color["role"], "diffuse")

    def test_red_content_with_suffix_is_a_utility_mask(self):
        item = texture("chr300_u01", content=content(red=0.8))
        classify_material_textures([item])
        self.assertEqual(item["role"], "utility_mask")
        self.assertEqual(item["role_source"], "content+suffix")

    def test_red_content_without_suffix_is_a_specular_mask(self):
        item = texture("chr300_mask", content=content(red=0.75))
        classify_material_textures([item])
        self.assertEqual(item["role"], "specular_mask")
        self.assertEqual(item["role_source"], "content")

    def test_utility_suffix_without_red_content_keeps_the_hint(self):
        item = texture("chr300_u01", content=content(red=0.1))
        classify_material_textures([item])
        self.assertEqual(item["role"], "utility_mask")
        self.assertEqual(item["role_source"], "suffix")

    def test_unresolved_texture_has_no_role_source_beyond_unresolved(self):
        item = texture("chr300_c01", conversion=False)
        classify_material_textures([item])
        self.assertEqual(item["role"], "unresolved")
        self.assertEqual(item["role_source"], "unresolved")

    def test_named_diffuse_not_selected_is_demoted_to_auxiliary(self):
        chosen = texture("chr300_c01", width=256, height=256, content=content())
        other = texture("chr300_c02", width=16, height=16, content=content())
        textures = [chosen, other]
        classify_material_textures(textures)
        self.assertEqual(chosen["role"], "diffuse")
        self.assertEqual(other["role"], "auxiliary")
        self.assertEqual(other["role_source"], "demoted")


if __name__ == "__main__":
    unittest.main()
