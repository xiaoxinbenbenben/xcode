import asyncio
import unittest

from src.context.context_builder import (
    build_context_bundle,
    build_repo_rule_layer,
    build_stable_context_layer,
)
from src.runtime.session import build_cli_session_runtime


IMPLEMENTED_TOOL_NAMES = ["LS", "Glob", "Grep", "Read", "Edit", "Write", "TodoWrite", "Bash"]


class ContextBuilderTests(unittest.TestCase):
    def test_build_stable_context_layer_only_covers_real_tools(self) -> None:
        stable_layer = build_stable_context_layer(IMPLEMENTED_TOOL_NAMES)

        self.assertIn("local code assistant", stable_layer.system_prompt.lower())
        self.assertIn("LS", stable_layer.tool_rules)
        self.assertIn("TodoWrite", stable_layer.tool_rules)
        self.assertIn("Bash", stable_layer.tool_rules)
        self.assertNotIn("Tracing", stable_layer.tool_rules)

    def test_build_repo_rule_layer_reads_code_law(self) -> None:
        repo_rule_layer = build_repo_rule_layer()

        self.assertIsNotNone(repo_rule_layer.path)
        self.assertEqual(repo_rule_layer.path.name, "code_law.md")
        self.assertIn("先用真实工具拿证据", repo_rule_layer.content)

    def test_build_context_bundle_keeps_layers_separate(self) -> None:
        session_runtime = build_cli_session_runtime()
        try:
            asyncio.run(
                session_runtime.session.add_items(
                    [
                        {"role": "user", "content": "旧问题"},
                        {"role": "assistant", "content": "旧回答"},
                    ]
                )
            )

            bundle = asyncio.run(
                build_context_bundle(
                    user_input="新问题",
                    session_runtime=session_runtime,
                    tool_names=IMPLEMENTED_TOOL_NAMES,
                )
            )
        finally:
            session_runtime.close()

        self.assertEqual(bundle.runtime.history_items[0]["content"], "旧问题")
        self.assertEqual(bundle.runtime.history_items[1]["content"], "旧回答")
        self.assertEqual(bundle.runtime.current_turn_items, [{"role": "user", "content": "新问题"}])
        self.assertEqual(bundle.build_runner_input(), [{"role": "user", "content": "新问题"}])
        self.assertIn("Code Law", bundle.build_agent_instructions())


if __name__ == "__main__":
    unittest.main()
