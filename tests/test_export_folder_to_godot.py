from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock

from tools.conversion.export_folder_to_godot import (
    foliage_source_for,
    kb_root_for,
    main,
    output_folder_for,
)
from tools.conversion.ddm.cli import ik_output_flags


class ExportFolderToGodotTests(unittest.TestCase):
    def test_godot_ik_exports_targets_without_offline_bake(self):
        self.assertEqual(ik_output_flags("none"), (False, False))
        self.assertEqual(ik_output_flags("bake"), (True, False))
        self.assertEqual(ik_output_flags("godot"), (False, True))

    def test_output_preserves_path_below_kb(self):
        source = Path("game_files/decompressed/KB/map/map101")
        self.assertEqual(kb_root_for(source), Path("game_files/decompressed/KB"))
        self.assertEqual(
            output_folder_for(source, Path("output/decoded")),
            Path("output/decoded/map/map101"),
        )

    def test_expected_foliage_table_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "map101"
            source.mkdir()
            expected = source / "map101_ins"
            expected.touch()
            self.assertEqual(foliage_source_for(source), expected)

    def test_missing_foliage_table_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "map101"
            source.mkdir()
            self.assertIsNone(foliage_source_for(source))

    def test_pipeline_enables_generic_trs_and_baked_ik(self):
        with tempfile.TemporaryDirectory() as directory:
            kb_root = Path(directory) / "KB"
            source = kb_root / "chara" / "chr300"
            source.mkdir(parents=True)
            stdout = io.StringIO()
            with mock.patch(
                "sys.argv",
                ["export_folder_to_godot.py", str(source), "--dry-run"],
            ), contextlib.redirect_stdout(stdout):
                self.assertEqual(main(), 0)

            command = stdout.getvalue()
            self.assertIn("--animations all", command)
            self.assertIn("--ik bake", command)
            self.assertNotIn("--experimental-", command)

    def test_vscode_animation_launch_uses_generic_decoder(self):
        launch = json.loads(Path(".vscode/launch.json").read_text("utf-8"))
        configuration = next(
            item for item in launch["configurations"]
            if item["name"] == "DDM: export model with animations"
        )
        arguments = configuration["args"]
        self.assertIn("--animations", arguments)
        self.assertIn("--ik", arguments)
        self.assertFalse(any(arg.startswith("--experimental-") for arg in arguments))

        inputs = {item["id"]: item for item in launch["inputs"]}
        self.assertEqual(inputs["ddmAnimations"]["default"], "all")
        self.assertEqual(inputs["ddmIK"]["default"], "bake")

    def test_destination_can_be_a_selected_godot_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'KB/chara/asset'
            source.mkdir(parents=True)
            project = root / 'godot-project'
            project.mkdir()
            (project / 'project.godot').write_text('config_version=5\n')
            with mock.patch('tools.conversion.export_folder_to_godot.run') as execute, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(source), '--godot-project', str(project), '--dry-run']), 0)
            arguments = execute.call_args_list[0].args[0]
            self.assertIn(str(project / 'original_files/decoded/chara'), arguments)


if __name__ == "__main__":
    unittest.main()
