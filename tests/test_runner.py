import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, sentinel

from src.runtime.config import RuntimeConfig
from src.runtime.runner import run_streamed


class FakeDeltaEvent:
    def __init__(self, delta: str) -> None:
        self.delta = delta


class FakeStreamingResult:
    def __init__(self, events: list[object], final_output: str) -> None:
        self._events = events
        self.final_output = final_output
        self.cancelled = False

    async def stream_events(self):
        for event in self._events:
            yield event

    def cancel(self) -> None:
        self.cancelled = True


class RuntimeRunnerTests(unittest.TestCase):
    def test_run_streamed_configures_sdk_and_emits_delta_chunks(self) -> None:
        config = RuntimeConfig(
            api_key="test-key",
            model="gpt-5.2",
            base_url="https://example.com/v1",
        )
        stream_result = FakeStreamingResult(
            events=[
                SimpleNamespace(type="raw_response_event", data=FakeDeltaEvent("he")),
                SimpleNamespace(type="raw_response_event", data=FakeDeltaEvent("llo")),
            ],
            final_output="hello",
        )
        session_runtime = SimpleNamespace(session=sentinel.session, context=sentinel.context)
        context_bundle = SimpleNamespace(
            build_agent_instructions=lambda: "stable instructions",
            build_runner_input=lambda: [{"role": "user", "content": "hello"}],
        )
        chunks: list[str] = []

        with patch("src.runtime.runner.AsyncOpenAI", return_value=sentinel.client) as async_openai:
            with patch("src.runtime.runner.set_default_openai_client") as set_client:
                with patch("src.runtime.runner.set_default_openai_api") as set_api:
                    with patch("src.runtime.runner.set_tracing_disabled") as set_tracing:
                        with patch(
                            "src.runtime.runner.build_context_bundle",
                            return_value=context_bundle,
                        ) as build_bundle:
                            with patch(
                                "src.runtime.runner.build_root_agent",
                                return_value=sentinel.agent,
                            ) as build_agent:
                                with patch(
                                    "src.runtime.runner.Runner.run_streamed",
                                    return_value=stream_result,
                                ) as run_streamed_mock:
                                    with patch(
                                        "src.runtime.runner.ResponseTextDeltaEvent",
                                        FakeDeltaEvent,
                                    ):
                                        output = asyncio.run(
                                            run_streamed(
                                                "hello",
                                                config,
                                                chunks.append,
                                                session_runtime=session_runtime,
                                            )
                                        )

        self.assertEqual(output, "hello")
        self.assertEqual(chunks, ["he", "llo"])
        async_openai.assert_called_once_with(
            base_url="https://example.com/v1",
            api_key="test-key",
        )
        set_client.assert_called_once_with(sentinel.client, use_for_tracing=False)
        set_api.assert_called_once_with("chat_completions")
        set_tracing.assert_called_once_with(True)
        build_bundle.assert_called_once_with(
            user_input="hello",
            session_runtime=session_runtime,
            tool_names=["LS", "Glob", "Grep", "Read", "Edit", "Write", "TodoWrite", "Bash"],
        )
        build_agent.assert_called_once_with(model="gpt-5.2", instructions="stable instructions")
        run_streamed_mock.assert_called_once_with(
            sentinel.agent,
            input=[{"role": "user", "content": "hello"}],
            session=sentinel.session,
            context=sentinel.context,
        )

    def test_run_streamed_falls_back_to_final_output_when_no_delta_arrives(self) -> None:
        config = RuntimeConfig(
            api_key="test-key",
            model="gpt-5.2",
            base_url="https://example.com/v1",
        )
        stream_result = FakeStreamingResult(
            events=[SimpleNamespace(type="run_item_stream_event", data=object())],
            final_output="full answer",
        )
        session_runtime = SimpleNamespace(session=sentinel.session, context=sentinel.context)
        context_bundle = SimpleNamespace(
            build_agent_instructions=lambda: "stable instructions",
            build_runner_input=lambda: [{"role": "user", "content": "hello"}],
        )
        chunks: list[str] = []

        with patch("src.runtime.runner.configure_openai_runtime"):
            with patch("src.runtime.runner.build_context_bundle", return_value=context_bundle):
                with patch("src.runtime.runner.build_root_agent", return_value=sentinel.agent):
                    with patch(
                        "src.runtime.runner.Runner.run_streamed",
                        return_value=stream_result,
                    ):
                        output = asyncio.run(
                            run_streamed(
                                "hello",
                                config,
                                chunks.append,
                                session_runtime=session_runtime,
                            )
                        )

        self.assertEqual(output, "full answer")
        self.assertEqual(chunks, ["full answer"])


if __name__ == "__main__":
    unittest.main()
