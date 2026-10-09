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

    def test_pipeline_enables_godot_character_ik_export(self):
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
            self.assertIn("--experimental-root-motion", command)
            self.assertIn("--experimental-rotation-joints all", command)
            self.assertIn(
                "--experimental-rotation-model local_delta_post", command,
            )
            self.assertNotIn(
                "--experimental-rotation-model global_delta", command,
            )
            self.assertIn(
                "--experimental-root-rotation-source local", command,
            )
            self.assertNotIn("--experimental-rotation-reference-clip", command)
            self.assertIn("--experimental-humanoid-ik-mode godot", command)
            self.assertIn("--experimental-ik-target-orientation none", command)

    def test_vscode_animation_launch_uses_corrected_fk_defaults(self):
        launch = json.loads(Path(".vscode/launch.json").read_text("utf-8"))
        configuration = next(
            item for item in launch["configurations"]
            if item["name"] == "DDM: experimental animation"
        )
        arguments = configuration["args"]
        self.assertIn("--experimental-rotation-model", arguments)
        model = arguments[arguments.index("--experimental-rotation-model") + 1]
        self.assertEqual(model, "local_delta_post")
        self.assertIn("--experimental-root-rotation-source", arguments)
        source = arguments[
            arguments.index("--experimental-root-rotation-source") + 1
        ]
        self.assertEqual(source, "local")

        inputs = {item["id"]: item for item in launch["inputs"]}
        self.assertEqual(inputs["ddmRotationJoints"]["default"], "all")
        self.assertEqual(inputs["ddmRotationUnits"]["default"], "radians")


if __name__ == "__main__":
    unittest.main()
