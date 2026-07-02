from __future__ import annotations

from typing import Any

from src.hooks.model import HookContext, HookEvent, HookResult
from src.hooks.registry import HookRegistry
from src.permissions import PermissionDecision, PermissionRequest
from src.protocol import ToolResponse, error_response


def _build_permission_request(context: HookContext) -> PermissionRequest:
    """根据工具 hook 上下文构造权限系统需要的请求。"""
    runtime_context = context.runtime_context
    actor_name = getattr(runtime_context, "actor_name", "team-lead")
    return PermissionRequest(
        tool_name=str(context.tool_name or ""),
        params_input=context.params_input or {},
        actor_name=str(actor_name or "team-lead"),
    )


def _permission_denied_response(
    *,
    tool_name: str,
    params_input: dict[str, Any],
    reason: str,
    code: str,
    decision: str,
    source: str,
) -> ToolResponse:
    """把权限拒绝转换成统一工具响应。"""
    return error_response(
        code=code,
        message=reason,
        text=f"工具调用被权限系统拒绝：{tool_name}。{reason}",
        stats={"time_ms": 0},
        context={"cwd": ".", "params_input": params_input},
        data={
            "tool_name": tool_name,
            "permission": {
                "decision": decision,
                "source": source,
                "reason": reason,
            },
        },
    )


def trace_run_start_hook(context: HookContext) -> None:
    """在用户提交 prompt 时启动本地 trace run。"""
    runtime_context = context.runtime_context
    if runtime_context is None:
        return
    runtime_context.start_trace_run(
        user_input=context.user_input or "",
        model=context.model or "",
    )


def trace_run_stop_hook(context: HookContext) -> None:
    """在一轮 run 停止时记录错误并结束本地 trace run。"""
    runtime_context = context.runtime_context
    if runtime_context is None:
        return
    if context.status in {"error", "cancelled"} and context.message:
        payload: dict[str, object] = {}
        if context.error_type:
            payload["error_type"] = context.error_type
        runtime_context.log_trace_error(
            stage=context.stage or "run",
            message=context.message,
            **payload,
        )
    runtime_context.finish_trace_run(
        final_output=context.final_output or "",
        usage=context.usage,
        status=context.status or "success",
    )


def trace_tool_call_hook(context: HookContext) -> None:
    """在工具执行前记录 tool_call trace。"""
    runtime_context = context.runtime_context
    if runtime_context is None:
        return
    runtime_context.log_trace_tool_call(
        tool_name=str(context.tool_name or "unknown_tool"),
        args=context.params_input or {},
    )


def permission_hook(context: HookContext) -> HookResult | None:
    """在工具执行前运行权限检查，必要时短路工具主体。"""
    runtime_context = context.runtime_context
    if runtime_context is None:
        return None
    permission_engine = getattr(runtime_context, "permission_engine", None)
    if permission_engine is None:
        return None

    request = _build_permission_request(context)
    result = permission_engine.authorize(request)
    if result.decision == PermissionDecision.ALLOW:
        return None

    response = _permission_denied_response(
        tool_name=request.tool_name,
        params_input=request.params_input,
        reason=result.reason,
        code=result.code,
        decision=result.decision.value,
        source=result.source,
    )
    return HookResult(stop=True, response=response)


def trace_tool_result_hook(context: HookContext) -> None:
    """在工具结束后记录 tool_result trace。"""
    runtime_context = context.runtime_context
    if runtime_context is None or context.tool_result is None:
        return
    runtime_context.log_trace_tool_result(
        tool_name=str(context.tool_name or "unknown_tool"),
        result=context.tool_result,
    )


def build_default_hook_registry() -> HookRegistry:
    """构建当前 session 默认启用的内置 hook 注册表。"""
    registry = HookRegistry()
    registry.register(HookEvent.USER_PROMPT_SUBMIT, trace_run_start_hook)
    registry.register(HookEvent.PRE_TOOL_USE, trace_tool_call_hook)
    registry.register(HookEvent.PRE_TOOL_USE, permission_hook)
    registry.register(HookEvent.POST_TOOL_USE, trace_tool_result_hook)
    registry.register(HookEvent.STOP, trace_run_stop_hook)
    return registry
