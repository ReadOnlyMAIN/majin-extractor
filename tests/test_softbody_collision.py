from copy import deepcopy
from pathlib import Path
import struct
import tempfile
import unittest

from tools.conversion.ddm.skinned import decode_skinned_geometry, decode_skinned_skeleton, find_skinned_geometry_header
from tools.conversion.player_physics import player_spring_chains
from tools.conversion.softbody_collision import (
    apply_collision_filters, decode_csr, decode_ssr_collision_masks,
    map_csr_collisions, player_collision_profile,
)


class SoftBodyCollisionTests(unittest.TestCase):
    def payload(self, kind=2):
        data = bytearray(0x110)
        data[:4] = b"\0CSR"
        struct.pack_into(">I", data, 4, 2)
        struct.pack_into(">I", data, 0x80, 1)
        struct.pack_into(">4I", data, 0x98, kind, 1, 2, 4)
        struct.pack_into(">3f", data, 0xB0, 1, 2, 3)
        struct.pack_into(">3f", data, 0xC0, 4, 5, 6)
        struct.pack_into(">2f", data, 0xD0, 10, 15)
        return data

    def skeleton(self):
        return {"joints": [dict(global_id=i, parent=None, name=f"bone_{i:03d}",
            translation=(0, 0, 0), rotation=(0, 0, 0, 1)) for i in (2, 4)]}

    def test_decodes_original_offsets_and_preserves_two_attachment_bones(self):
        records = decode_csr(self.payload())
        self.assertEqual(records[0]["point_a"], [1, 2, 3])
        self.assertEqual(records[0]["bone_b"], 4)
        self.assertEqual(records[0]["radius"], 10)
        profile = map_csr_collisions(records, self.skeleton(), self.skeleton(), 0.01)
        self.assertEqual(profile[0]["bone_a"], "bone_002")
        self.assertEqual(profile[0]["bone_b"], "bone_004")
        self.assertEqual(profile[0]["point_b"], [0.04, 0.05, 0.06])
        self.assertEqual(profile[0]["radius"], 0.1)

    def test_tapered_capsule_uses_smaller_radius_and_sphere_uses_only_a(self):
        sk = self.skeleton()
        tapered = map_csr_collisions(decode_csr(self.payload(3)), sk, sk, 0.01)[0]
        self.assertEqual(tapered["radius"], 0.10)
        sphere_data = self.payload(1)
        struct.pack_into(">I", sphere_data, 0xA4, 999)
        sphere = map_csr_collisions(decode_csr(sphere_data), sk, sk, 0.01)[0]
        self.assertEqual(sphere["shape"], "sphere")
        self.assertNotIn("bone_b", sphere)

    def test_bind_axis_change_maps_points_through_source_global_rest(self):
        source = self.skeleton()
        target = deepcopy(source)
        target["joints"][0]["rotation"] = (0, 0, 1, 0)
        target["joints"][0]["translation"] = (10, 0, 0)
        mapped = map_csr_collisions(decode_csr(self.payload()), source, target, 1)[0]
        self.assertEqual(mapped["point_a"], [9, -2, 3])

    def test_rejects_unproven_layouts_shapes_dimensions_and_missing_bones(self):
        for data in (self.payload()[:-1], self.payload(4), b"bad"):
            with self.assertRaises(ValueError):
                decode_csr(data)
        data = self.payload()
        struct.pack_into(">f", data, 0xD0, float("nan"))
        with self.assertRaises(ValueError):
            decode_csr(data)
        with self.assertRaises(ValueError):
            map_csr_collisions(decode_csr(self.payload()), self.skeleton(), {"joints": []}, 1)
        data = self.payload()
        struct.pack_into(">I", data, 0x9C, 0)
        self.assertEqual(map_csr_collisions(decode_csr(data), self.skeleton(), self.skeleton(), 1), [])

    def test_missing_original_file_does_not_invent_body_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = player_collision_profile(Path(directory) / "chr100/chr100", self.skeleton())
            self.assertEqual(result, {"collisions": [], "groups": [], "source": None})

    def test_ssr_group_masks_are_not_global_collision_enables(self):
        data = bytearray(0x90 + 0x60 + 0x190)
        data[:4] = b"\0SSR"
        struct.pack_into(">I", data, 4, 2)
        struct.pack_into(">2I", data, 0x80, 1, 1)
        struct.pack_into(">I", data, 0x90 + 0x34, 2)
        struct.pack_into(">I", data, 0x90 + 0x5C, 1)
        struct.pack_into(">I", data, 0xF0 + 8, 3)
        struct.pack_into(">3I", data, 0xF0 + 0x3C, 4, 5, 6)
        struct.pack_into(">I", data, 0xF0 + 0x18C, 8)
        groups = decode_ssr_collision_masks(data)
        chains = [dict(name=str(b), root_bone=f"bone_{b:03d}", end_bone=f"bone_{b:03d}")
                  for b in (2, 4)]
        profile = dict(groups=groups, collisions=[dict(name=f"CSR_{i}", source_record={"check_flag": flag})
                                                for i, flag in enumerate((1, 4, 8))])
        filtered = apply_collision_filters(chains, self.skeleton(), profile)
        self.assertEqual(filtered[0]["collision_names"], ["CSR_0"])
        self.assertEqual(filtered[1]["collision_names"], ["CSR_2"])
        with self.assertRaises(ValueError):
            decode_ssr_collision_masks(data[:0x100])

    @unittest.skipUnless(Path("game_files/decompressed/KB/chara/chr100/chr100").is_file(),
                         "requires local game corpus")
    def test_chr100_original_volumes_and_shared_cinematic_bind_mapping(self):
        base = Path("game_files/decompressed/KB/chara")
        path = base / "chr100/chr100.resource_d778dd04"
        records = decode_csr(path.read_bytes())
        self.assertEqual(len(records), 18)
        self.assertEqual([r["kind"] for r in records].count(1), 1)
        self.assertEqual([r["kind"] for r in records].count(3), 1)
        self.assertEqual(records[3]["bone_a"], 4)
        self.assertEqual(records[3]["bone_b"], 101)
        self.assertEqual(records[3]["radius"], 50)
        for name in ("chr100", "chr101", "chr141"):
            model = base / name / name
            profile = player_collision_profile(model, decode_skinned_skeleton(model.read_bytes()), 0.01)
            self.assertEqual(len(profile["collisions"]), 18)
            self.assertEqual(profile["source"]["shared_player_profile"], name != "chr100")
            self.assertEqual(profile["collisions"][3]["radius"], 0.5)

    @unittest.skipUnless(Path("game_files/decompressed/KB/chara/chr100/chr100").is_file(),
                         "requires local game corpus")
    def test_original_masks_restrict_9_to_14_to_cape_and_15_to_left_hip_cloth(self):
        model = Path("game_files/decompressed/KB/chara/chr100/chr100")
        data = model.read_bytes()
        skeleton = decode_skinned_skeleton(data)
        geometry = decode_skinned_geometry(data, find_skinned_geometry_header(data), skeleton)
        profile = player_collision_profile(model, skeleton, 0.01)
        chains = apply_collision_filters(player_spring_chains("chr100", skeleton,
            geometry["vertices"], geometry["mesh_parts"], 0.01), skeleton, profile)
        cape = {"cape_center", "cape_left", "cape_right"}
        hip = {"cloth_back", "cloth_side", "cloth_front"}
        for index in range(9, 15):
            self.assertEqual({c["name"] for c in chains if f"CSR_{index:02d}" in c["collision_names"]}, cape)
        self.assertEqual({c["name"] for c in chains if "CSR_15" in c["collision_names"]}, hip)
        self.assertTrue(all(not c["collision_names"] for c in chains if c["name"].startswith("hair_")))
        self.assertEqual({c["name"] for c in chains if "CSR_03" in c["collision_names"]}, cape)


if __name__ == "__main__":
    unittest.main()
