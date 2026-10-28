"""Source parameter preservation and engine checks for coupled cloth surfaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.conversion.cloth_physics import extract, player_cloth_surfaces
from tools.conversion.ddm.skinned import decode_skinned_skeleton
from tools.conversion.player_physics import PLAYER_SPRING_CHAINS
from tools.conversion.softbody_collision import player_collision_profile
from tools.conversion.godot_export import write_godot_material_assets

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'game_files/decompressed/KB/chara/chr100/chr100'


class ClothPhysicsTests(unittest.TestCase):
    @unittest.skipUnless(MODEL.exists(), 'requires local game corpus')
    def test_source_values_and_cloth_collision_domains(self):
        skeleton = decode_skinned_skeleton(MODEL.read_bytes())
        chains = [dict(end_bone=f'bone_{bones[-1]:03d}') for _, bones in PLAYER_SPRING_CHAINS]
        collisions = player_collision_profile(MODEL, skeleton, 0.01)
        result = player_cloth_surfaces(MODEL, skeleton, chains, collisions, 0.01)
        self.assertEqual([len(c['bone_columns'][0]) for c in result], [5, 8])
        self.assertEqual(result[0]['collision_names'], ['CSR_00', 'CSR_01', 'CSR_15', 'CSR_16', 'CSR_17'])
        self.assertEqual(result[1]['collision_names'], [f'CSR_{i:02d}' for i in range(15)])
        for surface in result:
            self.assertEqual((surface['kt'], surface['ks'], surface['kb']), (70, 50, 50))
            self.assertAlmostEqual(surface['mass'], 0.3)
            self.assertAlmostEqual(surface['update_delta'], 0.005)
            self.assertEqual(len(surface['source_parameters']['additional']['angle_limit']), 256)
            geometry = surface['source_parameters']['derived_rest_geometry']
            self.assertEqual(len(geometry['between_adjacent_columns']), 2 * len(surface['bone_columns'][0]))
        self.assertAlmostEqual(result[0]['air_damping'], 0.2)
        self.assertAlmostEqual(result[1]['air_damping'], 0.15)
        self.assertAlmostEqual(result[1]['particle_radius'], 0.05)
        self.assertEqual(player_cloth_surfaces(MODEL, skeleton, [], collisions, 0.01), [])
        with tempfile.TemporaryDirectory() as directory:
            write_godot_material_assets(Path(directory), [], {}, cloth_surfaces=result)
            manifest = json.loads((Path(directory) / 'materials/material_bindings.json').read_text())
            self.assertEqual(manifest['cloth_surfaces'], result)

    @unittest.skipUnless(MODEL.exists(), 'requires local game corpus')
    def test_truncated_ssr_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            resource = Path(directory) / 'bad.ssr'
            resource.write_bytes(MODEL.with_name('chr100.resource_29509cc1').read_bytes()[:-16])
            with self.assertRaises(ValueError):
                extract(MODEL, resource)

    @unittest.skipUnless(os.environ.get('GODOT_BIN'), 'set GODOT_BIN for engine validation')
    def test_engine_gravity_width_pinning_collisions_and_serialization(self):
        with tempfile.TemporaryDirectory(prefix='majin-cloth-') as directory:
            project = Path(directory)
            (project / 'project.godot').write_text('config_version=5\n')
            shutil.copyfile(ROOT / 'godot/utility/cloth_constraints.gd', project / 'cloth_constraints.gd')
            shutil.copyfile(ROOT / 'tools/validation/godot_cloth_check.gd', project / 'check.gd')
            env = dict(os.environ, XDG_DATA_HOME=str(project / 'data'),
                       XDG_CONFIG_HOME=str(project / 'config'), XDG_CACHE_HOME=str(project / 'cache'))
            result = subprocess.run([os.environ['GODOT_BIN'], '--headless', '--path', directory,
                                     '--script', 'check.gd'], env=env,
                                    capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn('SCRIPT ERROR', output)
            self.assertNotIn('\nERROR:', output)
            self.assertIn('CLOTH_VALIDATION', output)


if __name__ == '__main__':
    unittest.main()
