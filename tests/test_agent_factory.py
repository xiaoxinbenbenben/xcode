import unittest

from agents import Agent

from src.runtime.agent_factory import build_root_agent


class BuildRootAgentTests(unittest.TestCase):
    def test_build_root_agent_returns_agent_with_read_only_tools(self) -> None:
        agent = build_root_agent(model="gpt-5.2")

        self.assertIsInstance(agent, Agent)
        self.assertEqual(agent.name, "xx-coding")
        self.assertEqual(agent.model, "gpt-5.2")
        self.assertIn("local code", agent.instructions.lower())
        self.assertEqual([tool.name for tool in agent.tools], ["LS", "Glob", "Grep", "Read", "Edit", "Write", "TodoWrite", "Bash"])
        self.assertIn("path", agent.tools[0].params_json_schema["properties"])
        self.assertIn("summary", agent.tools[-2].params_json_schema["properties"])
        self.assertIn("command", agent.tools[-1].params_json_schema["properties"])


if __name__ == "__main__":
    unittest.main()
