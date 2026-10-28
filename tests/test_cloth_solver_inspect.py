"""Check the conversion against the actual bounded PPC scalar routines."""
from pathlib import Path
import unittest
from tools.research.cloth_solver_inspect import (
    ROUTINES, execute_scalar, inspect, metric_stiffness, isolated_link_calibration,
)
from tools.research.tar_cpu_inspect import ElfMemory

ELF = Path('output/research/motion-names/EBOOT.elf')
MODEL = Path('game_files/decompressed/KB/chara/chr100/chr100')


class ClothSolverInspectionTests(unittest.TestCase):
    def test_metric_force_is_invariant_under_model_scale(self):
        for scale in (0.01, 0.1, 1):
            for strain in (-0.1, 0.01, 0.1):
                length = 15 * scale
                k = metric_stiffness(70, length, scale, strain < 0)
                metric_force = k * length * strain
                self.assertAlmostEqual(metric_force, scale * 70 * strain * (0.5 if strain < 0 else 1))
        with self.assertRaises(ValueError):
            metric_stiffness(70, 0)

    @unittest.skipUnless(ELF.exists(), 'requires local decrypted executable')
    def test_piecewise_response_executes_actual_ppc_for_all_coefficients(self):
        memory = ElfMemory(ELF.read_bytes())
        for kind in ROUTINES:
            for k in (1, 50, 70, 100):
                for strain in (-0.5, -0.1, -0.001, 0, 0.001, 0.1, 0.5):
                    actual = execute_scalar(memory, kind, k, strain)
                    self.assertAlmostEqual(actual, k * strain * (0.5 if strain < 0 else 1), places=6)

    @unittest.skipUnless(ELF.exists() and MODEL.exists(), 'requires local game corpus')
    def test_discrete_isolated_link_replay_matches_reference(self):
        report = inspect(ELF, MODEL, MODEL.with_name('chr100.resource_29509cc1'))
        benchmark = report['isolated_link_benchmark']
        self.assertLess(benchmark['converted_max_absolute_error_m'], 1e-8)
        self.assertGreater(benchmark['former_preview_max_absolute_error_m'], 1e-4)
        self.assertAlmostEqual(report['original_longitudinal_extension_correction']['target_length_ratio'], 1.001, places=6)


if __name__ == '__main__':
    unittest.main()
