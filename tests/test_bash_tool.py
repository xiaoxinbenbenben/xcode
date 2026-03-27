import tempfile
import unittest
from pathlib import Path

from src.tools.bash_tool import run_bash


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BashToolTests(unittest.TestCase):
    def test_run_bash_returns_protocol_envelope_on_success(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temp_dir:
            relative_path = Path(temp_dir).relative_to(PROJECT_ROOT).as_posix()

            result = run_bash(
                command="printf 'hello bash'",
                directory=relative_path,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["stdout"], "hello bash")
        self.assertEqual(result["data"]["stderr"], "")
        self.assertEqual(result["data"]["exit_code"], 0)
        self.assertEqual(result["context"]["directory_resolved"], relative_path)
        self.assertIn("time_ms", result["stats"])

    def test_run_bash_returns_partial_when_exit_code_is_non_zero(self) -> None:
        result = run_bash(
            command="python3 -c \"import sys; sys.stderr.write('boom\\n'); sys.exit(2)\"",
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["exit_code"], 2)
        self.assertIn("boom", result["data"]["stderr"])

    def test_run_bash_returns_partial_when_timed_out_with_partial_output(self) -> None:
        result = run_bash(
            command="printf start; sleep 0.2",
            timeout_ms=10,
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data"]["timed_out"])
        self.assertIsNone(result["data"]["exit_code"])
        self.assertIn("start", result["data"]["stdout"])

    def test_run_bash_returns_error_when_timed_out_without_output(self) -> None:
        result = run_bash(
            command="sleep 0.2",
            timeout_ms=10,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "TIMEOUT")

    def test_run_bash_rejects_directory_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_bash(
                command="printf 'x'",
                directory=temp_dir,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "ACCESS_DENIED")

    def test_run_bash_blocks_read_only_shell_commands(self) -> None:
        result = run_bash(command="ls")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "COMMAND_BLOCKED")


if __name__ == "__main__":
    unittest.main()
