import tempfile
import unittest
from pathlib import Path

from src.runtime.session import ToolRuntimeContext
from src.tools.todo_write import todo_write


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TodoWriteToolTests(unittest.TestCase):
    def test_todo_write_returns_protocol_envelope_and_recap(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "artifacts") as temp_dir:
            runtime_context = ToolRuntimeContext(
                session_id="todo-test-session",
                todo_persist_dir=Path(temp_dir),
            )

            result = todo_write(
                summary="实现 TodoWrite 工具",
                todos=[
                    {"content": "写 todo spec", "status": "completed"},
                    {"content": "实现 TodoWrite", "status": "in_progress"},
                    {"content": "补测试", "status": "pending"},
                ],
                runtime_context=runtime_context,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["summary"], "实现 TodoWrite 工具")
        self.assertEqual(len(result["data"]["todos"]), 3)
        self.assertIn("[1/3]", result["data"]["recap"])
        self.assertIn("In progress: 实现 TodoWrite", result["data"]["recap"])
        self.assertIn("Pending: 补测试", result["data"]["recap"])
        self.assertFalse(result["data"]["persisted"])
        self.assertIn("TODO:", result["text"])
        self.assertEqual(result["stats"]["total"], 3)
        self.assertEqual(result["stats"]["in_progress"], 1)

    def test_todo_write_rejects_multiple_in_progress_items(self) -> None:
        result = todo_write(
            summary="实现 TodoWrite 工具",
            todos=[
                {"content": "写 todo spec", "status": "in_progress"},
                {"content": "实现 TodoWrite", "status": "in_progress"},
            ],
            runtime_context=ToolRuntimeContext(session_id="todo-test-session"),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "INVALID_PARAM")

    def test_todo_write_overwrites_previous_list_declaratively(self) -> None:
        runtime_context = ToolRuntimeContext(session_id="todo-test-session")

        first = todo_write(
            summary="第一版计划",
            todos=[
                {"content": "任务 A", "status": "in_progress"},
                {"content": "任务 B", "status": "pending"},
            ],
            runtime_context=runtime_context,
        )
        second = todo_write(
            summary="第二版计划",
            todos=[
                {"content": "任务 C", "status": "pending"},
            ],
            runtime_context=runtime_context,
        )

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(second["data"]["summary"], "第二版计划")
        self.assertEqual(second["data"]["todos"], [{"content": "任务 C", "status": "pending"}])
        self.assertIsNotNone(runtime_context.todo_state)
        self.assertEqual(runtime_context.todo_state.summary, "第二版计划")
        self.assertEqual(len(runtime_context.todo_state.todos), 1)

    def test_todo_write_persists_completed_state_once_per_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "artifacts") as temp_dir:
            runtime_context = ToolRuntimeContext(
                session_id="todo-test-session",
                todo_persist_dir=Path(temp_dir),
            )

            first = todo_write(
                summary="完成 TodoWrite",
                todos=[
                    {"content": "写 todo spec", "status": "completed"},
                    {"content": "实现 TodoWrite", "status": "completed"},
                    {"content": "补测试", "status": "cancelled"},
                ],
                runtime_context=runtime_context,
            )
            second = todo_write(
                summary="完成 TodoWrite",
                todos=[
                    {"content": "写 todo spec", "status": "completed"},
                    {"content": "实现 TodoWrite", "status": "completed"},
                    {"content": "补测试", "status": "cancelled"},
                ],
                runtime_context=runtime_context,
            )

            archive_path = Path(first["data"]["archive_path"])
            archive_content = archive_path.read_text(encoding="utf-8")

        self.assertEqual(first["status"], "success")
        self.assertTrue(first["data"]["persisted"])
        self.assertEqual(second["status"], "success")
        self.assertFalse(second["data"]["persisted"])
        self.assertTrue(archive_path.name.startswith("todo-session-todo-test-session"))
        self.assertEqual(archive_content.count("## task"), 1)
        self.assertIn("Summary: 完成 TodoWrite", archive_content)


if __name__ == "__main__":
    unittest.main()
