import unittest

from src.protocol.tool_response import (
    error_response,
    partial_response,
    success_response,
)


class ToolResponseProtocolTests(unittest.TestCase):
    def test_success_response_returns_standard_envelope(self) -> None:
        result = success_response(
            data={"entries": []},
            text="列出了 0 个条目。",
            stats={"time_ms": 3},
            context={"cwd": ".", "params_input": {"path": "."}},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"], {"entries": []})
        self.assertEqual(result["text"], "列出了 0 个条目。")
        self.assertEqual(result["stats"]["time_ms"], 3)
        self.assertEqual(result["context"]["cwd"], ".")
        self.assertEqual(result["context"]["params_input"], {"path": "."})
        self.assertIsNone(result["error"])

    def test_partial_response_marks_discounted_result(self) -> None:
        result = partial_response(
            data={"matches": [], "truncated": True},
            text="结果已截断。",
            stats={"time_ms": 8, "total_matches": 120},
            context={"cwd": ".", "params_input": {"pattern": "TODO"}},
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["data"]["truncated"])
        self.assertEqual(result["stats"]["total_matches"], 120)
        self.assertIsNone(result["error"])

    def test_error_response_requires_structured_error(self) -> None:
        result = error_response(
            code="NOT_FOUND",
            message="文件不存在。",
            text="读取失败，请检查路径。",
            stats={"time_ms": 1},
            context={"cwd": ".", "params_input": {"path": "src/main.py"}},
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["data"], {})
        self.assertEqual(result["error"], {"code": "NOT_FOUND", "message": "文件不存在。"})

    def test_missing_time_ms_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            success_response(
                data={},
                text="ok",
                stats={},
                context={"cwd": ".", "params_input": {}},
            )

    def test_missing_context_keys_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            partial_response(
                data={},
                text="partial",
                stats={"time_ms": 1},
                context={"cwd": "."},
            )


if __name__ == "__main__":
    unittest.main()
