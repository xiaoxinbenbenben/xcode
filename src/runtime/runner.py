from __future__ import annotations

from collections.abc import Callable

from agents import (
    Runner,
    RunConfig,
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
from src.runtime.tracing import extract_usage_from_raw_event_data
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


def build_session_input_callback(context_bundle):
    # SDK 会把“已有历史”和“本轮新输入”分别传给 callback。
    # 这里显式接收两个参数，只替换历史视图，保留当前轮真实新输入。
    def session_input_callback(_history_items, new_items):
        return [
            *context_bundle.runtime.history_items,
            *new_items,
        ]

    return session_input_callback


async def run_streamed(
    user_input: str,
    config: RuntimeConfig,
    on_text_delta: Callable[[str], None],
    session_runtime: CliSessionRuntime | None = None,
) -> str:
    """执行一次流式 agent 运行，并把文本增量回传给调用方。"""
    configure_openai_runtime(config)
    active_context = session_runtime.context if session_runtime is not None else None
    if session_runtime is not None:
        # tool context 需要知道当前模型名，手动 Compact 时会复用同一模型生成 summary。
        session_runtime.context.current_model = config.model
        session_runtime.context.start_trace_run(
            user_input=user_input,
            model=config.model,
        )
    result = None
    saw_delta = False
    usage: dict[str, int] | None = None
    try:
        context_bundle = await build_context_bundle(
            user_input=user_input,
            session_runtime=session_runtime,
            tool_names=[tool.name for tool in AGENT_TOOLS],
            model_name=config.model,
        )
        if active_context is not None:
            active_context.log_trace_context_build(
                {
                    "history_items": len(context_bundle.runtime.history_items),
                    "current_turn_items": len(context_bundle.runtime.current_turn_items),
                    "mentioned_files": list(context_bundle.runtime.mentioned_files),
                    "summary": (
                        context_bundle.runtime.summary.as_dict()
                        if context_bundle.runtime.summary is not None
                        else None
                    ),
                    "compaction": dict(context_bundle.runtime.compaction),
                }
            )
        agent = build_root_agent(
            model=config.model,
            instructions=context_bundle.build_agent_instructions(),
        )
        run_config = None
        if session_runtime is not None:
            # micro_compact 只影响“本轮送给模型的输入视图”，不直接改写底层 session 原文。
            run_config = RunConfig(
                session_input_callback=build_session_input_callback(context_bundle)
            )
        result = Runner.run_streamed(
            agent,
            input=context_bundle.build_runner_input(),
            session=session_runtime.session if session_runtime is not None else None,
            context=session_runtime.context if session_runtime is not None else None,
            run_config=run_config,
        )

        async for event in result.stream_events():
            # 这里严格对齐官方 streaming 示例，避免误判事件层级。
            if (
                event.type == "raw_response_event"
                and isinstance(event.data, ResponseTextDeltaEvent)
                and event.data.delta
            ):
                on_text_delta(event.data.delta)
                saw_delta = True
            if event.type == "raw_response_event":
                usage = extract_usage_from_raw_event_data(event.data) or usage
    except KeyboardInterrupt:
        if result is not None:
            result.cancel()
        if active_context is not None:
            active_context.log_trace_error(
                stage="run",
                message="用户中断了当前运行。",
            )
            active_context.finish_trace_run(
                final_output="",
                usage=usage,
                status="cancelled",
            )
        raise
    except Exception as exc:
        if active_context is not None:
            active_context.log_trace_error(
                stage="run",
                message=str(exc),
                error_type=exc.__class__.__name__,
            )
            active_context.finish_trace_run(
                final_output="",
                usage=usage,
                status="error",
            )
        raise

    if isinstance(result.final_output, str):
        final_output = result.final_output
    elif result.final_output is None:
        final_output = ""
    else:
        final_output = str(result.final_output)
    if not saw_delta and final_output:
        on_text_delta(final_output)
    if active_context is not None:
        active_context.finish_trace_run(
            final_output=final_output,
            usage=usage,
            status="success",
        )
    return final_output
