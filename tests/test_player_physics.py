import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.conversion.ddm.skinned import (
    decode_skinned_geometry, decode_skinned_skeleton, find_skinned_geometry_header,
)
from tools.conversion.godot_export import write_godot_material_assets
from tools.conversion.player_physics import player_spring_chains


class PlayerPhysicsTests(unittest.TestCase):
    def fixture(self):
        ids = [110, 190, 191, 192, 120, 193, 194]
        parents = [None, 0, 1, 2, 0, 0, 5]
        skeleton = {"joints": [dict(global_id=bone, name=f"bone_{bone:03d}",
            parent=parent, translation=(4.0, 0.0, 0.0))
            for bone, parent in zip(ids, parents)]}
        vertices = [dict(joints=(3, 4, 6, 0), weights=(0.5, 0.5, 0, 0))]
        return skeleton, vertices, [dict(triangles=[(0, 0, 0)])]

    def test_profile_uses_weighted_geometry_and_excludes_face_and_zero_weights(self):
        skeleton, vertices, parts = self.fixture()
        result = player_spring_chains("chr101", skeleton, vertices, parts, 0.01)
        self.assertEqual([c["name"] for c in result], ["hair_fringe"])
        self.assertEqual(result[0]["end_length"], 0.04)
        self.assertEqual(result[0]["gravity"], 1.0)
        self.assertEqual(result[0]["gravity_mode"], "per_joint")
        self.assertEqual([j["bone"] for j in result[0]["joint_settings"]], ["bone_190", "bone_191", "bone_192"])
        self.assertEqual(result[0]["root_bone"], "bone_190")
        self.assertEqual(result[0]["end_bone"], "bone_192")
        self.assertEqual(player_spring_chains("chr200", skeleton, vertices, parts), [])
        self.assertEqual(player_spring_chains("chr720", skeleton, vertices, parts), [])
        self.assertEqual(player_spring_chains("chr100", skeleton, vertices, []), [])

    def test_missing_or_reparented_bones_are_not_simulated(self):
        skeleton, vertices, parts = self.fixture()
        skeleton["joints"][3]["parent"] = 0
        self.assertEqual(player_spring_chains("chr100", skeleton, vertices, parts), [])
        skeleton["joints"][3]["global_id"] = 999
        self.assertEqual(player_spring_chains("chr100", skeleton, vertices, parts), [])

    def test_manifest_serializes_profile_and_defaults_to_no_physics(self):
        profile = player_spring_chains("chr100", *self.fixture(), scale=0.01)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            collisions = [{"shape": "sphere", "radius": 0.1}]
            source = {"file": "original.csr", "sha256": "source-digest"}
            write_godot_material_assets(out, [], {}, spring_bone_chains=profile,
                spring_bone_collisions=collisions, spring_collision_source=source)
            path = out / "materials/material_bindings.json"
            self.assertEqual(json.loads(path.read_text())["spring_bone_chains"], profile)
            self.assertEqual(json.loads(path.read_text())["spring_bone_collisions"], collisions)
            self.assertEqual(json.loads(path.read_text())["spring_collision_source"], source)
            write_godot_material_assets(out, [], {})
            self.assertEqual(json.loads(path.read_text())["spring_bone_chains"], [])

    @unittest.skipUnless(Path("game_files/decompressed/KB/chara/chr141/chr141").exists(),
                         "requires local game corpus")
    def test_real_body_profiles_include_hair_and_cape_and_exclude_unused_chains(self):
        for name in ("chr100", "chr101", "chr140", "chr141"):
            with self.subTest(asset=name):
                data = Path(f"game_files/decompressed/KB/chara/{name}/{name}").read_bytes()
                skeleton = decode_skinned_skeleton(data)
                geometry = decode_skinned_geometry(data, find_skinned_geometry_header(data), skeleton)
                profile = player_spring_chains(name, skeleton,
                    geometry["vertices"], geometry["mesh_parts"], 0.01)
                names = {chain["name"] for chain in profile}
                self.assertTrue({"hair_fringe", "hair_palm", "cape_center",
                                 "cape_left", "cape_right"} <= names)
                if name in ("chr140", "chr141"):
                    self.assertFalse(any(n.startswith("cloth_leg") for n in names))
                self.assertTrue(all(0 < c["end_length"] < 1 for c in profile))

    @unittest.skipUnless(os.environ.get("GODOT_BIN"), "set GODOT_BIN for engine validation")
    def test_godot_serialization_and_motion_after_pose_transfer(self):
        skeleton, vertices, parts = self.fixture()
        joints = [dict(joint, rotation=(0, 0, 0, 1)) for joint in skeleton["joints"]]
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="majin-springs-") as directory:
            project = Path(directory)
            (project / "project.godot").write_text(
                'config_version=5\n[application]\nconfig/name="Spring validation"\n')
            for name in ("assign_materials.gd", "player_armor.gd"):
                shutil.copyfile(repo / "godot/utility" / name, project / name)
            (project / "majin_utility").mkdir()
            shutil.copyfile(repo / "godot/utility/spring_collision_update.gd",
                            project / "majin_utility/spring_collision_update.gd")
            shutil.copyfile(repo / "tools/validation/godot_secondary_motion_check.gd",
                            project / "check.gd")
            collisions = [dict(name="Sphere", shape="sphere", bone_a="bone_110",
                point_a=[0, 0, 0], radius=0.02, source_record={}),
                dict(name="Capsule", shape="capsule", bone_a="bone_110", bone_b="bone_120",
                     point_a=[0, 0, 0], point_b=[0, 0, 0], radius=0.01, source_record={})]
            chains = player_spring_chains("chr101", skeleton, vertices, parts, 0.01)
            chains[0]["collision_names"] = ["Sphere", "Capsule"]
            chains[0]["joint_settings"][1].update(gravity=0.6, stiffness=2.0, drag=0.5, radius=0.007)
            legacy = dict(chains[0], name="unaffected_hair", root_bone="bone_193",
                          end_bone="bone_194", collision_names=[], gravity=0.0)
            legacy.pop("gravity_mode")
            legacy.pop("joint_settings")
            chains.append(legacy)
            (project / "spec.json").write_text(json.dumps(dict(joints=joints, collisions=collisions,
                chains=chains)))
            environment = dict(os.environ, XDG_DATA_HOME=str(project / "data"),
                XDG_CONFIG_HOME=str(project / "config"), XDG_CACHE_HOME=str(project / "cache"))
            result = subprocess.run([os.environ["GODOT_BIN"], "--headless", "--editor",
                "--path", str(project), "--script", "check.gd"], env=environment,
                capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("SCRIPT ERROR", output)
            self.assertIn("SPRING_VALIDATION", output)


if __name__ == "__main__":
    unittest.main()
