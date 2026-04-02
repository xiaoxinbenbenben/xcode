from __future__ import annotations

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tasks.worktrees import closeout_task_worktree, ensure_task_worktree, list_worktrees
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool,
    start_timer,
)


def _require_runtime_context(runtime_context: ToolRuntimeContext | None) -> ToolRuntimeContext:
    if runtime_context is None or runtime_context.session is None:
        raise ToolFailure(
            code="NO_SESSION",
            message="当前没有可用 session。",
            text="当前没有可用会话，暂时无法操作 worktree。",
        )
    return runtime_context


def _worktree_create(
    *,
    task_id: int,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # WorktreeCreate 只为一个任务绑定隔离目录，不在这里顺手切 teammate 状态。
    start_time = start_timer()
    params_input = {"task_id": task_id}
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        task = ensure_task_worktree(
            runtime_context=active_runtime_context,
            task_id=task_id,
        )
        return success_response(
            data={"task": task},
            text=f"已为 task_{task_id} 绑定 worktree：{task['worktree_name']}",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=active_runtime_context.session_id,
            ),
        )
    except FileNotFoundError as exc:
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            ToolFailure(
                code="NOT_FOUND",
                message=str(exc),
                text=f"未找到任务 task_{task_id}。",
            ),
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )
    except ToolFailure as failure:
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )


def _worktree_list(*, runtime_context: ToolRuntimeContext | None = None) -> ToolResponse:
    # 当前先从任务图反推 worktree 列表，不再额外维护第二份 registry。
    start_time = start_timer()
    params_input: dict[str, object] = {}
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        worktrees = list_worktrees(runtime_context=active_runtime_context)
        return success_response(
            data={"worktrees": worktrees},
            text=f"当前共有 {len(worktrees)} 个任务绑定了 worktree。",
            stats=build_stats(start_time, total=len(worktrees)),
            context=build_context(
                params_input=params_input,
                session_id=active_runtime_context.session_id,
            ),
        )
    except ToolFailure as failure:
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )


def _worktree_closeout(
    *,
    task_id: int,
    action: str,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # closeout 当前只支持 keep/remove，两种动作都围绕 task 绑定 worktree 展开。
    start_time = start_timer()
    params_input = {"task_id": task_id, "action": action}
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        task = closeout_task_worktree(
            runtime_context=active_runtime_context,
            task_id=task_id,
            action=action,
        )
        return success_response(
            data={"task": task},
            text=f"已对 task_{task_id} 执行 worktree closeout：{action}",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=active_runtime_context.session_id,
            ),
        )
    except FileNotFoundError as exc:
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            ToolFailure(
                code="NOT_FOUND",
                message=str(exc),
                text=f"未找到任务 task_{task_id}。",
            ),
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )
    except ToolFailure as failure:
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )


def _worktree_create_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    task_id: int,
) -> ToolResponse:
    return run_traced_tool(
        ctx.context,
        tool_name="WorktreeCreate",
        params_input={"task_id": task_id},
        invoke=lambda: _worktree_create(
            task_id=task_id,
            runtime_context=ctx.context,
        ),
    )


def _worktree_list_tool(ctx: RunContextWrapper[ToolRuntimeContext]) -> ToolResponse:
    return run_traced_tool(
        ctx.context,
        tool_name="WorktreeList",
        params_input={},
        invoke=lambda: _worktree_list(runtime_context=ctx.context),
    )


def _worktree_closeout_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    task_id: int,
    action: str,
) -> ToolResponse:
    return run_traced_tool(
        ctx.context,
        tool_name="WorktreeCloseout",
        params_input={"task_id": task_id, "action": action},
        invoke=lambda: _worktree_closeout(
            task_id=task_id,
            action=action,
            runtime_context=ctx.context,
        ),
    )


worktree_create_tool = function_tool(
    _worktree_create_tool,
    name_override="WorktreeCreate",
    description_override="为某个任务创建或绑定一个隔离 worktree。",
)
worktree_list_tool = function_tool(
    _worktree_list_tool,
    name_override="WorktreeList",
    description_override="列出当前 session 下所有已绑定的 task worktree。",
)
worktree_closeout_tool = function_tool(
    _worktree_closeout_tool,
    name_override="WorktreeCloseout",
    description_override="对某个任务绑定的 worktree 执行 closeout；action 只支持 keep 或 remove。",
)

WORKTREE_TOOLS = [
    worktree_create_tool,
    worktree_list_tool,
    worktree_closeout_tool,
]

__all__ = [
    "WORKTREE_TOOLS",
    "worktree_create_tool",
    "worktree_list_tool",
    "worktree_closeout_tool",
]
