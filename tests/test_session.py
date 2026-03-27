import unittest

from src.runtime.session import ToolRuntimeContext, build_cli_session_runtime


class SessionRuntimeTests(unittest.TestCase):
    def test_build_cli_session_runtime_creates_sdk_session_and_context(self) -> None:
        session_runtime = build_cli_session_runtime()
        try:
            self.assertTrue(session_runtime.session_id)
            self.assertTrue(hasattr(session_runtime.session, "get_items"))
            self.assertEqual(len(session_runtime.context.read_snapshots), 0)
            self.assertEqual(session_runtime.context.session_id, session_runtime.session_id)
            self.assertIsNone(session_runtime.context.todo_state)
        finally:
            session_runtime.close()

    def test_tool_runtime_context_keeps_latest_snapshot_per_path_and_evicts_oldest(self) -> None:
        context = ToolRuntimeContext(max_read_snapshots=2)

        context.remember_read_snapshot("src/a.py", file_mtime_ms=1, file_size_bytes=10)
        context.remember_read_snapshot("src/b.py", file_mtime_ms=2, file_size_bytes=20)
        context.remember_read_snapshot("src/a.py", file_mtime_ms=3, file_size_bytes=30)
        context.remember_read_snapshot("src/c.py", file_mtime_ms=4, file_size_bytes=40)

        snapshot_a = context.get_read_snapshot("src/a.py")
        snapshot_b = context.get_read_snapshot("src/b.py")
        snapshot_c = context.get_read_snapshot("src/c.py")

        self.assertIsNotNone(snapshot_a)
        self.assertEqual(snapshot_a.file_mtime_ms, 3)
        self.assertEqual(snapshot_a.file_size_bytes, 30)
        self.assertIsNone(snapshot_b)
        self.assertIsNotNone(snapshot_c)


if __name__ == "__main__":
    unittest.main()
