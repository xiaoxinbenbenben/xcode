from __future__ import annotations

from typing import Any, Literal, TypedDict


ToolStatus = Literal["success", "partial", "error"]
ToolData = dict[str, Any]
ToolStatsValue = int | float | str
ToolStats = dict[str, ToolStatsValue]
ToolContext = dict[str, Any]


class ToolError(TypedDict):
    code: str
    message: str


class ToolResponse(TypedDict):
    status: ToolStatus
    data: ToolData
    text: str
    stats: ToolStats
    context: ToolContext
    error: ToolError | None


def _validate_common_fields(*, data: ToolData, stats: ToolStats, context: ToolContext) -> None:
    # 协议层只校验顶层信封的最小约束，不介入具体工具 data 的内部形状。
    if not isinstance(data, dict):
        raise ValueError("data 必须是对象。")
    if "time_ms" not in stats:
        raise ValueError("stats.time_ms 是必填字段。")
    if "cwd" not in context or "params_input" not in context:
        raise ValueError("context 必须包含 cwd 和 params_input。")


def make_tool_response(
    *,
    status: ToolStatus,
    data: ToolData,
    text: str,
    stats: ToolStats,
    context: ToolContext,
    error: ToolError | None = None,
) -> ToolResponse:
    _validate_common_fields(data=data, stats=stats, context=context)
    if status == "error" and error is None:
        raise ValueError("status 为 error 时必须提供 error 对象。")
    if status != "error" and error is not None:
        raise ValueError("只有 status 为 error 时才能提供 error 对象。")

    return {
        "status": status,
        "data": dict(data),
        "text": text,
        "stats": dict(stats),
        "context": dict(context),
        "error": dict(error) if error is not None else None,
    }


def success_response(
    *,
    data: ToolData,
    text: str,
    stats: ToolStats,
    context: ToolContext,
) -> ToolResponse:
    return make_tool_response(
        status="success",
        data=data,
        text=text,
        stats=stats,
        context=context,
        error=None,
    )


def partial_response(
    *,
    data: ToolData,
    text: str,
    stats: ToolStats,
    context: ToolContext,
) -> ToolResponse:
    return make_tool_response(
        status="partial",
        data=data,
        text=text,
        stats=stats,
        context=context,
        error=None,
    )


def error_response(
    *,
    code: str,
    message: str,
    text: str,
    stats: ToolStats,
    context: ToolContext,
    data: ToolData | None = None,
) -> ToolResponse:
    # error 响应也保留 data 字段，方便后续携带部分诊断信息，但默认仍返回空对象。
    return make_tool_response(
        status="error",
        data={} if data is None else data,
        text=text,
        stats=stats,
        context=context,
        error={"code": code, "message": message},
    )
