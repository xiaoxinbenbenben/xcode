import tempfile
import unittest
from pathlib import Path

from src.runtime.session import ToolRuntimeContext
from src.tools.edit_write import edit_file, write_file
from src.tools.read_only import read_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FileEditToolsTests(unittest.TestCase):
    def test_edit_file_replaces_unique_snippet_when_lock_matches(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.py"
            file_path.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            read_result = read_file(path=relative_path)
            result = edit_file(
                path=relative_path,
                old_string="return 'hi'",
                new_string="return 'hello'",
                expected_mtime_ms=int(read_result["stats"]["file_mtime_ms"]),
                expected_size_bytes=int(read_result["stats"]["file_size_bytes"]),
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["data"]["applied"])
            self.assertEqual(result["data"]["replacements"], 1)
            self.assertIn("hello", file_path.read_text(encoding="utf-8"))

    def test_edit_file_can_reuse_runtime_snapshot_without_explicit_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.py"
            file_path.write_text("value = 1\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()
            runtime_context = ToolRuntimeContext()

            read_result = read_file(path=relative_path, runtime_context=runtime_context)
            result = edit_file(
                path=relative_path,
                old_string="value = 1",
                new_string="value = 2",
                runtime_context=runtime_context,
            )

            self.assertEqual(read_result["status"], "success")
            self.assertEqual(result["status"], "success")
            self.assertEqual(file_path.read_text(encoding="utf-8"), "value = 2\n")

    def test_edit_file_returns_conflict_when_file_changed_after_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.py"
            file_path.write_text("value = 1\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            read_result = read_file(path=relative_path)
            file_path.write_text("value = 1000\n", encoding="utf-8")

            result = edit_file(
                path=relative_path,
                old_string="value = 1",
                new_string="value = 2",
                expected_mtime_ms=int(read_result["stats"]["file_mtime_ms"]),
                expected_size_bytes=int(read_result["stats"]["file_size_bytes"]),
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"]["code"], "CONFLICT")
            self.assertIn("conflict", result["data"])

    def test_write_file_requires_lock_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.txt"
            file_path.write_text("old\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            result = write_file(path=relative_path, content="new\n")

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"]["code"], "INVALID_PARAM")

    def test_write_file_updates_existing_file_when_lock_matches(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.txt"
            file_path.write_text("before\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            read_result = read_file(path=relative_path)
            result = write_file(
                path=relative_path,
                content="after\n",
                expected_mtime_ms=int(read_result["stats"]["file_mtime_ms"]),
                expected_size_bytes=int(read_result["stats"]["file_size_bytes"]),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["data"]["operation"], "update")
            self.assertEqual(file_path.read_text(encoding="utf-8"), "after\n")

    def test_write_file_returns_conflict_when_file_changed_after_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.txt"
            file_path.write_text("before\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            read_result = read_file(path=relative_path)
            file_path.write_text("changed by someone else\n", encoding="utf-8")

            result = write_file(
                path=relative_path,
                content="after\n",
                expected_mtime_ms=int(read_result["stats"]["file_mtime_ms"]),
                expected_size_bytes=int(read_result["stats"]["file_size_bytes"]),
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"]["code"], "CONFLICT")
            self.assertIn("conflict", result["data"])

    def test_write_file_can_create_new_file_without_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "nested" / "new_file.txt"
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

            result = write_file(path=relative_path, content="created\n")

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["data"]["operation"], "create")
            self.assertEqual(file_path.read_text(encoding="utf-8"), "created\n")

    def test_write_file_can_reuse_runtime_snapshot_without_explicit_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            file_path = temp_path / "sample.txt"
            file_path.write_text("before\n", encoding="utf-8")
            relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()
            runtime_context = ToolRuntimeContext()

            read_result = read_file(path=relative_path, runtime_context=runtime_context)
            result = write_file(
                path=relative_path,
                content="after\n",
                runtime_context=runtime_context,
            )

            self.assertEqual(read_result["status"], "success")
            self.assertEqual(result["status"], "success")
            self.assertEqual(file_path.read_text(encoding="utf-8"), "after\n")


if __name__ == "__main__":
    unittest.main()
