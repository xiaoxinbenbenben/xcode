import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch, sentinel

from openai import APIConnectionError

from scripts.cli import main


class FakePromptSession:
    def __init__(self, responses: list[str | BaseException]) -> None:
        self._responses = list(responses)

    def prompt(self, *args, **kwargs) -> str:
        value = self._responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeSessionRuntime:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CliTests(unittest.TestCase):
    def test_cli_uses_argument_input(self) -> None:
        session_runtime = FakeSessionRuntime()

        with patch("scripts.cli.load_runtime_config", return_value=sentinel.config) as load_config:
            with patch("scripts.cli.build_cli_session_runtime", return_value=session_runtime):
                with patch("scripts.cli.stream_reply") as stream_reply:
                    exit_code = main(["hello from argv"])

        self.assertEqual(exit_code, 0)
        load_config.assert_called_once_with()
        stream_reply.assert_called_once_with("hello from argv", sentinel.config, session_runtime)
        self.assertTrue(session_runtime.closed)

    def test_cli_enters_repl_until_exit_command(self) -> None:
        session = FakePromptSession(["hello from prompt", "exit"])
        session_runtime = FakeSessionRuntime()

        with patch("scripts.cli.load_runtime_config", return_value=sentinel.config) as load_config:
            with patch("scripts.cli.build_cli_session_runtime", return_value=session_runtime):
                with patch("scripts.cli.build_prompt_session", return_value=session):
                    with patch("scripts.cli.stream_reply") as stream_reply:
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Ctrl-D / Ctrl-C", stdout.getvalue())
        load_config.assert_called_once_with()
        stream_reply.assert_called_once_with("hello from prompt", sentinel.config, session_runtime)
        self.assertTrue(session_runtime.closed)

    def test_cli_exit_command_does_not_stream(self) -> None:
        session = FakePromptSession(["quit"])
        session_runtime = FakeSessionRuntime()

        with patch("scripts.cli.load_runtime_config", return_value=sentinel.config):
            with patch("scripts.cli.build_cli_session_runtime", return_value=session_runtime):
                with patch("scripts.cli.build_prompt_session", return_value=session):
                    with patch("scripts.cli.stream_reply") as stream_reply:
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            exit_code = main([])

        self.assertEqual(exit_code, 0)
        stream_reply.assert_not_called()
        self.assertTrue(session_runtime.closed)

    def test_cli_argument_mode_returns_error_code_on_api_connection_error(self) -> None:
        session_runtime = FakeSessionRuntime()
        stderr = io.StringIO()

        with patch("scripts.cli.load_runtime_config", return_value=sentinel.config):
            with patch("scripts.cli.build_cli_session_runtime", return_value=session_runtime):
                with patch(
                    "scripts.cli.stream_reply",
                    side_effect=APIConnectionError(request=sentinel.request),
                ):
                    with redirect_stderr(stderr):
                        exit_code = main(["hello from argv"])

        self.assertEqual(exit_code, 1)
        self.assertIn("模型连接失败", stderr.getvalue())
        self.assertTrue(session_runtime.closed)

    def test_cli_repl_handles_api_connection_error_and_continues(self) -> None:
        session = FakePromptSession(["hello from prompt", "exit"])
        session_runtime = FakeSessionRuntime()
        stderr = io.StringIO()

        with patch("scripts.cli.load_runtime_config", return_value=sentinel.config):
            with patch("scripts.cli.build_cli_session_runtime", return_value=session_runtime):
                with patch("scripts.cli.build_prompt_session", return_value=session):
                    with patch(
                        "scripts.cli.stream_reply",
                        side_effect=[APIConnectionError(request=sentinel.request)],
                    ):
                        with redirect_stderr(stderr):
                            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("模型连接失败", stderr.getvalue())
        self.assertTrue(session_runtime.closed)

    def test_cli_script_help_runs_as_a_real_script(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "scripts/cli.py", "--help"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Minimal CLI wrapper", result.stdout)


if __name__ == "__main__":
    unittest.main()
