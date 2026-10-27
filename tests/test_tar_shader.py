import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import math
from pathlib import Path
import random
import tempfile
import unittest

from tools.extraction.pak_extractor import extract_pak
from tools.research.tar_shader_decode import decode_shader
from tools.research.tar_shader_math import (
    run_fragment, run_vertex, shade_tar, tar_coordinates, tar_coverage,
)

PAK = Path('game_files/package/PS3_GAME/USRDIR/finalizedPS3/KB/package/static.pak')


class TarCoverageTests(unittest.TestCase):
    def test_cutoff_fade_and_strict_boundaries(self):
        hp = (.8, .6, 5.0, 999.0)
        self.assertEqual(tar_coverage(.5, hp), (False, 1.0))
        self.assertEqual(tar_coverage(.6, hp), (False, 1.0))
        self.assertAlmostEqual(tar_coverage(.7, hp)[1], .5)
        self.assertFalse(tar_coverage(.8, hp)[0])
        self.assertTrue(tar_coverage(.8001, hp)[0])
        self.assertTrue(tar_coverage(.4, hp, .005)[0])
        self.assertEqual(tar_coverage(.4, hp, .2), (False, 1.0))

    def test_mask_is_clamped_but_fade_is_not_smoothstep_or_clamped(self):
        self.assertEqual(tar_coverage(-1, (1, .5, 1, 0)), (False, 1.0))
        self.assertEqual(tar_coverage(2, (1, .5, 1, 0)), (False, .5))
        self.assertEqual(tar_coverage(.75, (1, .5, 8, 0)), (False, -1.0))


@unittest.skipUnless(PAK.is_file(), 'requires local original static.pak')
class OriginalTarShaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        with ThreadPoolExecutor(max_workers=2) as pool, contextlib.redirect_stdout(io.StringIO()):
            extract_pak(PAK, Path(cls.directory.name), pool, quiet=True)
        root = Path(cls.directory.name) / 'KB/shader'
        cls.programs = [decode_shader((root / f'KbTarModel{number}').read_bytes())
                        for number in range(1, 5)]

    def test_all_passes_have_closed_control_flow_and_unambiguous_uniforms(self):
        self.assertEqual(sum(map(len, self.programs)), 32)
        for passes in self.programs:
            for program in passes:
                self.assertTrue(program['vertex'][-1]['end'])
                self.assertTrue(program['fragment'][-1]['end'])
                slots = {item['slot'] for item in program['fragment']}
                for instruction in program['fragment']:
                    if instruction['opcode'] == 'IFE':
                        self.assertIn(instruction['end_slot'], slots)
                        self.assertEqual(instruction['else_slot'], instruction['end_slot'])
                for parameter in program['parameters']:
                    offsets = parameter.get('fragment_constant_offsets', [])
                    if offsets:
                        self.assertEqual(len(offsets), int(parameter['metadata_word'], 16) & 255)
                        self.assertNotIn(0, offsets)

    @staticmethod
    def sample(unit, coord):
        x, y, z, _ = coord
        if unit == 0:
            return (.8, .6, .4, .75)
        if unit == 1:
            return (.5 + .2 * math.sin(x + y), .6, .8, 1)
        if unit == 2:
            return (.5 + .3 * math.sin(x - y), .3, .4, 1)
        if unit == 3:
            return (.52 + .02 * math.sin(x), .53 + .02 * math.sin(y), .98, 1)
        if unit == 4:
            return (.55 + .02 * math.sin(x), .47 + .02 * math.cos(y), .97, 1)
        if unit == 5:
            return (.2 + .1 * math.sin(x), .3 + .1 * math.sin(y), .4 + .1 * math.sin(z), 1)
        if unit == 6:
            return (.4 + .1 * math.cos(x), .3 + .1 * math.cos(y), .2, 1)
        if unit == 7:
            return (.5 + .4 * math.cos(x + y), 0, 0, 1)
        raise AssertionError(unit)

    def test_full_rgba_equations_match_instruction_evaluation_on_all_32_passes(self):
        rng = random.Random(4201)
        for model, passes in enumerate(self.programs):
            for program in passes:
                for trial in range(24):
                    inputs = {
                        'COL0': (rng.random(), .4, .7, 1),
                        'TEX0': tuple(rng.uniform(-1, 2) for _ in range(4)),
                        'TEX1': (rng.uniform(1, 10), 2, 5, 1),
                        'TEX2': (rng.uniform(-.4, .4), .2, -1, 0),
                        'TEX3': (.2, 1, .1, 0),
                        'TEX4': (.3, rng.uniform(.1, .6), 1, 0),
                        'WPOS': (rng.uniform(0, 1280), rng.uniform(0, 720), 0, 1),
                    }
                    uniforms = {
                        'tarHP': (.9, .6, 1 / .3, 47),
                        'matParam0': (rng.uniform(8, 64), .1, .5, .2),
                        'matParam1': (.5, 0, 1, 1), 'matParam2': (0, .1, .2, 0),
                        'colorSky': (.6, .7, .8, 1), 'colorGround': (.1, .2, .3, 1),
                        'colorDirLight': (.8, .7, .6, 1), 'tarColor': (.02, .03, .04, .6),
                        'specularColor': (.3, .2, .1, 1),
                        'envmapColor': (0, 0, 0, trial % 2),
                        'fogParam': (.05, 10, .03, 0),
                        'fogSelect': tuple(float(i == trial % 3) for i in range(4)),
                        'fogColor': (.1, .1, .2, 0), 'halfLambertParam': (.7, 0, 0, 0),
                    }
                    for light in range(3):
                        inputs['TEX' + str(5 + light)] = (.3 + light, .5, 2 + trial * .2, 0)
                        uniforms['pLightColor' + str(light)] = (.3, .2, .1, 1)
                        uniforms['pLightParam' + str(light)] = (123, .5, 456, 5)
                    base_alpha = (.005, .25, .75, 1.0)[trial % 4]
                    def sample(unit, coord):
                        value = self.sample(unit, coord)
                        return (*value[:3], base_alpha) if unit == 0 else value
                    decoded = run_fragment(program, inputs, uniforms, sample)
                    formula = shade_tar(inputs, uniforms, sample,
                                        point_light_count=model, variant=program['variant'])
                    with self.subTest(model=model+1, variant=program['variant'], trial=trial):
                        self.assertEqual(decoded[0], formula[0])
                        if not decoded[0]:
                            for a, b in zip(decoded[1], formula[1]):
                                self.assertAlmostEqual(a, b, places=11)

    def test_projected_uv_polynomial_matches_actual_vertex_instructions(self):
        for passes in self.programs:
            for program in passes:
                uniforms = {
                    'cameraPos': (0, 0, 400, 1), 'rootPos': (1, 2, 3, 1),
                    'objectBlendOffset': (.7, 0, 0, 0),
                    'dirLightVec': (.2, .3, 1, 0), 'hemisphereAxis': (0, 1, 0, 0),
                    'matParam1': (.5, 0, 1, 1), 'uvOffset': (.1, .2, .3, .4),
                    'pLightPos0': (3, 4, 5, 1), 'pLightPos1': (4, 5, 6, 1),
                    'pLightPos2': (5, 6, 7, 1),
                }
                constants = {}
                for register, name in program['vertex_uniforms'].items():
                    if name == '_localMatrixArray':
                        for bone in range(4):
                            for row in range(3):
                                constants[register + bone*3 + row] = tuple(float(i == row) if i < 3 else bone*(row+1) for i in range(4))
                    elif name == 'matWorldViewProj':
                        for row in range(4):
                            constants[register + row] = tuple(float(i == row) for i in range(4))
                    else:
                        constants[register] = uniforms[name]
                for normal in ((.6,.3,.4), (-.6,.3,.4), (.2,.3,-.8), (-.2,.3,-.8)):
                    attributes = {0:(10,20,30,1), 1:(.4,.3,.2,.1), 7:(0,1,2,3),
                                  2:(*normal,0), 14:(1,0,0,0), 15:(0,0,1,0),
                                  3:(.37,.6,.8,1), 8:(.2,.4,0,0)}
                    result = run_vertex(program, attributes, constants)
                    self.assertAlmostEqual(result[1][0], .37)
                    self.assertAlmostEqual(result[1][3], .7)
                    expected = ((.5,.8) if program['variant'] in (3,6)
                                else tar_coordinates((10,20,30) if program['variant'] < 4 else (9,18,27),
                                                     normal,uniforms['uvOffset']))
                    self.assertAlmostEqual(result[7][2], expected[0])
                    self.assertAlmostEqual(result[7][3], expected[1])
