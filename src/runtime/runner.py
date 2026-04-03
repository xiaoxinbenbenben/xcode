from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from agents import (
    Runner,
    RunConfig,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.items import ToolCallItem, ToolCallOutputItem
from openai import AsyncOpenAI
from openai.types.responses import ResponseTextDeltaEvent

from src.context import build_context_bundle
from src.runtime.agent_factory import build_root_agent
from src.runtime.config import RuntimeConfig
from src.runtime.events import RuntimeEventBuilder, summarize_tool_call, summarize_tool_result
from src.runtime.session import CliSessionRuntime
from src.runtime.tracing import extract_usage_from_raw_event_data
from src.tools.registry import AGENT_TOOLS


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


async def run_events(
    user_input: str,
    config: RuntimeConfig,
    session_runtime: CliSessionRuntime | None = None,
) -> AsyncIterator[dict[str, object]]:
    """执行一次流式 agent 运行，并统一产出结构化 runtime 事件。"""
    configure_openai_runtime(config)
    active_context = session_runtime.context if session_runtime is not None else None
    run_id = (
        active_context.start_trace_run(user_input=user_input, model=config.model)
        if active_context is not None
        else f"run-{uuid4().hex[:12]}"
    )
    session_id = session_runtime.session_id if session_runtime is not None else "detached-session"
    event_builder = RuntimeEventBuilder(run_id=run_id or f"run-{uuid4().hex[:12]}", session_id=session_id)
    if session_runtime is not None:
        # tool context 需要知道当前模型名，手动 Compact 时会复用同一模型生成 summary。
        session_runtime.context.current_model = config.model
        session_runtime.context.main_model = config.model
        session_runtime.context.light_model = config.light_model
    yield event_builder.build(
        "run_started",
        {
            "user_input": user_input,
            "model": config.model,
        },
    )
    result = None
    saw_delta = False
    saw_completed_text = False
    usage: dict[str, int] | None = None
    pending_tool_names: list[str] = []
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
        yield event_builder.build(
            "context_built",
            {
                "history_items": len(context_bundle.runtime.history_items),
                "current_turn_items": len(context_bundle.runtime.current_turn_items),
                "mentioned_files": list(context_bundle.runtime.mentioned_files),
                "compaction": dict(context_bundle.runtime.compaction),
            },
        )
        # 这些系统事件都已经在 build_context_bundle 里被 drain。
        # 这里把它们显式发成 runtime events，供 CLI/TUI 单独展示。
        for item in context_bundle.runtime.background_results:
            yield event_builder.build("background_result_arrived", dict(item))
        for item in context_bundle.runtime.team_messages:
            yield event_builder.build("team_message_arrived", dict(item))
        for item in context_bundle.runtime.teammate_state_changes:
            yield event_builder.build("teammate_state_changed", dict(item))
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
                saw_delta = True
                yield event_builder.build(
                    "assistant_text_delta",
                    {"delta": event.data.delta},
                )
            if event.type == "raw_response_event":
                usage = extract_usage_from_raw_event_data(event.data) or usage
            if event.type == "run_item_stream_event" and isinstance(event.item, ToolCallItem):
                if event.name == "tool_called":
                    payload = summarize_tool_call(event.item)
                    pending_tool_names.append(str(payload["tool_name"]))
                    yield event_builder.build("tool_intent", payload)
                    yield event_builder.build("tool_started", payload)
            if event.type == "run_item_stream_event" and isinstance(event.item, ToolCallOutputItem):
                if event.name == "tool_output":
                    payload = summarize_tool_result(event.item)
                    if payload["tool_name"] == "unknown_tool" and pending_tool_names:
                        payload["tool_name"] = pending_tool_names.pop(0)
                    yield event_builder.build("tool_result", payload)
                    yield event_builder.build(
                        "tool_finished",
                        {
                            "tool_name": payload["tool_name"],
                            "status": payload["status"],
                            "summary": payload["summary"],
                        },
                    )
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
        yield event_builder.build(
            "run_failed",
            {"status": "cancelled", "message": "用户中断了当前运行。"},
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
        yield event_builder.build(
            "run_failed",
            {
                "status": "error",
                "message": str(exc),
                "error_type": exc.__class__.__name__,
            },
        )
        raise

    if isinstance(result.final_output, str):
        final_output = result.final_output
    elif result.final_output is None:
        final_output = ""
    else:
        final_output = str(result.final_output)
    if not saw_delta and final_output:
        yield event_builder.build(
            "assistant_text_delta",
            {"delta": final_output},
        )
        saw_delta = True
    if final_output:
        yield event_builder.build(
            "assistant_text_completed",
            {"text": final_output},
        )
        saw_completed_text = True
    if active_context is not None:
        active_context.finish_trace_run(
            final_output=final_output,
            usage=usage,
            status="success",
        )
    yield event_builder.build(
        "run_finished",
        {
            "final_output": final_output,
            "usage": usage or {},
            "text_completed": saw_completed_text,
        },
    )


async def run_streamed(
    user_input: str,
    config: RuntimeConfig,
    on_text_delta: Callable[[str], None],
    session_runtime: CliSessionRuntime | None = None,
) -> str:
    """兼容旧入口：内部改成消费 runtime 事件，只把文本增量透给调用方。"""
    final_output = ""
    async for event in run_events(
        user_input,
        config,
        session_runtime=session_runtime,
    ):
        if event["type"] == "assistant_text_delta":
            payload = event["payload"]
            if isinstance(payload, dict):
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    on_text_delta(delta)
        if event["type"] == "run_finished":
            payload = event["payload"]
            if isinstance(payload, dict):
                final_output = str(payload.get("final_output") or "")
    return final_output
