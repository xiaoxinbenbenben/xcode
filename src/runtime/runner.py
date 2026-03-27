from __future__ import annotations

from collections.abc import Callable

from agents import (
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI
from openai.types.responses import ResponseTextDeltaEvent

from src.context import build_context_bundle
from src.runtime.agent_factory import build_root_agent
from src.runtime.config import RuntimeConfig
from src.runtime.session import CliSessionRuntime
from src.tools import AGENT_TOOLS


def configure_openai_runtime(config: RuntimeConfig) -> None:
    """设置所有 CLI 运行共享的最小 SDK 默认项。"""
    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
    )
    set_default_openai_client(client, use_for_tracing=False)
    # 先与已验证稳定的兼容性探针保持一致，后续再逐步接入 tracing 和 session。
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)


async def run_streamed(
    user_input: str,
    config: RuntimeConfig,
    on_text_delta: Callable[[str], None],
    session_runtime: CliSessionRuntime | None = None,
) -> str:
    """执行一次流式 agent 运行，并把文本增量回传给调用方。"""
    configure_openai_runtime(config)
    context_bundle = await build_context_bundle(
        user_input=user_input,
        session_runtime=session_runtime,
        tool_names=[tool.name for tool in AGENT_TOOLS],
    )
    agent = build_root_agent(
        model=config.model,
        instructions=context_bundle.build_agent_instructions(),
    )
    result = Runner.run_streamed(
        agent,
        input=context_bundle.build_runner_input(),
        session=session_runtime.session if session_runtime is not None else None,
        context=session_runtime.context if session_runtime is not None else None,
    )

    saw_delta = False
    try:
        async for event in result.stream_events():
            # 这里严格对齐官方 streaming 示例，避免误判事件层级。
            if (
                event.type == "raw_response_event"
                and isinstance(event.data, ResponseTextDeltaEvent)
                and event.data.delta
            ):
                on_text_delta(event.data.delta)
                saw_delta = True
    except KeyboardInterrupt:
        result.cancel()
        raise

    if isinstance(result.final_output, str):
        final_output = result.final_output
    elif result.final_output is None:
        final_output = ""
    else:
        final_output = str(result.final_output)
    if not saw_delta and final_output:
        on_text_delta(final_output)
    return final_output
