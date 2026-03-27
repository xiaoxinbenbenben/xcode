import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.read_only import (
    glob_search,
    grep_search,
    list_files,
    read_file,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ReadOnlyToolsTests(unittest.TestCase):
    def test_list_files_returns_protocol_envelope(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "alpha.py").write_text("print('hi')\n", encoding="utf-8")
            (temp_path / "nested").mkdir()
            relative_path = temp_path.relative_to(PROJECT_ROOT).as_posix()

            result = list_files(path=relative_path, offset=0, limit=20)

        self.assertEqual(result["status"], "success")
        self.assertIn("entries", result["data"])
        self.assertEqual(result["context"]["cwd"], ".")
        self.assertEqual(result["context"]["path_resolved"], relative_path)
        self.assertIn("time_ms", result["stats"])

    def test_glob_search_finds_matching_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "first.py").write_text("print('a')\n", encoding="utf-8")
            (temp_path / "second.txt").write_text("hello\n", encoding="utf-8")
            relative_path = temp_path.relative_to(PROJECT_ROOT).as_posix()

            result = glob_search(pattern="**/*.py", path=relative_path, limit=20)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["paths"]), 1)
        self.assertTrue(result["data"]["paths"][0].endswith("first.py"))

    def test_grep_search_can_fall_back_to_python(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.py"
            file_path.write_text("print('x')\n# TODO: fix me\n", encoding="utf-8")
            relative_path = temp_path.relative_to(PROJECT_ROOT).as_posix()

            with patch("src.tools.read_only.shutil.which", return_value=None):
                result = grep_search(pattern="TODO", path=relative_path, limit=20)

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data"]["fallback_used"])
        self.assertEqual(result["data"]["matches"][0]["line"], 2)
        self.assertIn("TODO", result["data"]["matches"][0]["text"])

    def test_read_file_returns_numbered_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.txt"
            file_path.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()
            expected_size = file_path.stat().st_size

            result = read_file(path=relative_path, start_line=2, limit=2)

        self.assertEqual(result["status"], "success")
        self.assertIn("   2 | line 2", result["data"]["content"])
        self.assertIn("   3 | line 3", result["data"]["content"])
        self.assertEqual(result["context"]["path_resolved"], relative_path)
        self.assertIn("file_mtime_ms", result["stats"])
        self.assertEqual(result["stats"]["file_size_bytes"], expected_size)

    def test_tools_reject_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = list_files(path=temp_dir)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()
