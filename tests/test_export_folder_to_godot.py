from pathlib import Path
import tempfile
import unittest

from tools.conversion.export_folder_to_godot import (
    foliage_source_for,
    kb_root_for,
    output_folder_for,
)


class ExportFolderToGodotTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
