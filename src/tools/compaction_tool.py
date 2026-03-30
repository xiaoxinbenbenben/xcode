from __future__ import annotations

from agents import RunContextWrapper, function_tool

from src.context.compaction import compact_session_history
from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool_async,
    start_timer,
)


async def compact_history(runtime_context: ToolRuntimeContext | None = None) -> ToolResponse:
    """显式压缩当前会话历史，并把 summary 写回 L3。"""
    start_time = start_timer()
    params_input: dict[str, str] = {}
    if runtime_context is None:
        raise ToolFailure(
            code="NO_SESSION",
            message="当前运行没有可压缩的 session。",
            text="当前没有可压缩的会话历史。",
        )
    active_runtime_context = runtime_context

    try:
        # Compact 必须绑定到当前 CLI session；脱离 session 时无法改写历史。
        if active_runtime_context.session is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前运行没有可压缩的 session。",
                text="当前没有可压缩的会话历史。",
            )
        if not active_runtime_context.current_model:
            raise ToolFailure(
                code="NO_MODEL",
                message="当前运行没有可用模型信息。",
                text="当前没有可用的模型配置，暂时无法执行压缩。",
            )

        compaction_result = await compact_session_history(
            session=active_runtime_context.session,
            session_id=active_runtime_context.session_id,
            model=active_runtime_context.current_model,
            force=True,
        )
        if not compaction_result.compacted or compaction_result.summary is None:
            return success_response(
                data={
                    "compacted": False,
                    "summary": None,
                    "archive_path": None,
                },
                text="当前没有足够的历史可供压缩。",
                stats=build_stats(start_time),
                context=build_context(
                    params_input=params_input,
                    session_id=active_runtime_context.session_id,
                ),
            )

        active_runtime_context.remember_history_summary(
            compaction_result.summary,
            archive_path=compaction_result.archive_path,
        )
        # 工具结果直接把新 summary 暴露给模型，这样同一轮里也能立刻用这份精简信息继续推理。
        return success_response(
            data={
                "compacted": True,
                "summary": compaction_result.summary.as_dict(),
                "archive_path": compaction_result.archive_path,
            },
            text=(
                "已压缩当前会话历史。"
                f" 当前 summary 已写回 L3，完整旧历史归档到 {compaction_result.archive_path}。"
            ),
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=active_runtime_context.session_id,
            ),
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            session_id=active_runtime_context.session_id,
        )


async def _compact_tool(ctx: RunContextWrapper[ToolRuntimeContext]) -> ToolResponse:
    # 显式 Compact 和自动压缩复用同一后端，只是触发方式不同。
    return await run_traced_tool_async(
        ctx.context,
        tool_name="Compact",
        params_input={},
        invoke=lambda: compact_history(runtime_context=ctx.context),
    )


compact_tool = function_tool(
    _compact_tool,
    name_override="Compact",
    description_override="压缩当前会话历史：归档旧消息，生成 L3 summary，并只保留最近消息。",
)

COMPACTION_TOOLS = [compact_tool]
