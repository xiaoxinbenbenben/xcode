from __future__ import annotations

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tasks.background import start_background_command
from src.tasks.subagent import run_subagent_task
from src.tasks.task_graph import update_task
from src.tasks.task_store import create_task, get_task, list_tasks
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool,
    run_traced_tool_async,
    start_timer,
)


def _require_runtime_context(runtime_context: ToolRuntimeContext | None) -> ToolRuntimeContext:
    if runtime_context is None or runtime_context.session is None:
        raise ToolFailure(
            code="NO_SESSION",
            message="当前没有可用 session。",
            text="当前没有可用会话，暂时无法操作任务系统。",
        )
    return runtime_context


def task_create(
    *,
    title: str,
    summary: str,
    kind: str,
    prompt: str | None = None,
    subagent_type: str | None = None,
    model_route: str | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # 任务创建只负责落盘一份最小任务对象，不在这里顺手执行任务。
    start_time = start_timer()
    params_input = {
        "title": title,
        "summary": summary,
        "kind": kind,
        "prompt": prompt,
        "subagent_type": subagent_type,
        "model_route": model_route,
    }
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        task = create_task(
            tasks_dir=active_runtime_context.tasks_dir,
            title=title,
            summary=summary,
            kind=kind,
            prompt=prompt,
            subagent_type=subagent_type,
            model_route=model_route,
        )
        return success_response(
            data={"task": task},
            text=f"已创建任务 task_{task['id']}: {task['title']}",
            stats=build_stats(start_time),
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


def task_update(
    *,
    task_id: int,
    status: str | None = None,
    add_blocked_by: list[int] | None = None,
    add_blocks: list[int] | None = None,
    owner: str | None = None,
    result_summary: str | None = None,
    result_artifact: str | None = None,
    error: str | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    start_time = start_timer()
    params_input = {
        "task_id": task_id,
        "status": status,
        "add_blocked_by": add_blocked_by,
        "add_blocks": add_blocks,
        "owner": owner,
        "result_summary": result_summary,
        "result_artifact": result_artifact,
        "error": error,
    }
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        task = update_task(
            tasks_dir=active_runtime_context.tasks_dir,
            task_id=task_id,
            status=status,
            add_blocked_by=add_blocked_by,
            add_blocks=add_blocks,
            owner=owner,
            result_summary=result_summary,
            result_artifact=result_artifact,
            error=error,
        )
    except FileNotFoundError as exc:
        failure = ToolFailure(
            code="NOT_FOUND",
            message=str(exc),
            text=f"未找到任务 task_{task_id}。",
        )
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            session_id=session_id,
        )
    except ValueError as exc:
        failure = ToolFailure(
            code="INVALID_PARAM",
            message=str(exc),
            text=str(exc),
        )
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
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

    return success_response(
        data={"task": task},
        text=f"已更新任务 task_{task['id']}，当前状态：{task['status']}",
        stats=build_stats(start_time),
        context=build_context(
            params_input=params_input,
            session_id=active_runtime_context.session_id,
        ),
    )


def task_list(*, runtime_context: ToolRuntimeContext | None = None) -> ToolResponse:
    start_time = start_timer()
    params_input: dict[str, object] = {}
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        tasks = list_tasks(active_runtime_context.tasks_dir)
        summaries = [
            {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "owner": task["owner"],
                "blockedBy": task["blockedBy"],
                "blocks": task["blocks"],
            }
            for task in tasks
        ]
        return success_response(
            data={"tasks": summaries},
            text=f"当前共有 {len(tasks)} 个任务。",
            stats=build_stats(start_time, total=len(tasks)),
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


def task_get(*, task_id: int, runtime_context: ToolRuntimeContext | None = None) -> ToolResponse:
    start_time = start_timer()
    params_input = {"task_id": task_id}
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        task = get_task(active_runtime_context.tasks_dir, task_id)
    except FileNotFoundError as exc:
        failure = ToolFailure(
            code="NOT_FOUND",
            message=str(exc),
            text=f"未找到任务 task_{task_id}。",
        )
        session_id = runtime_context.session_id if runtime_context is not None else "detached-session"
        return error_from_failure(
            failure,
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
    return success_response(
        data={"task": task},
        text=f"已读取任务 task_{task['id']}：{task['title']}",
        stats=build_stats(start_time),
        context=build_context(
            params_input=params_input,
            session_id=active_runtime_context.session_id,
        ),
    )


async def task_run(
    *,
    task_id: int | None = None,
    title: str | None = None,
    summary: str | None = None,
    prompt: str | None = None,
    subagent_type: str = "general",
    model_route: str = "light",
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # TaskRun 可以直接运行已有任务，也可以在缺省时先创建一个分析任务。
    start_time = start_timer()
    params_input = {
        "task_id": task_id,
        "title": title,
        "summary": summary,
        "prompt": prompt,
        "subagent_type": subagent_type,
        "model_route": model_route,
    }
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        active_task_id = task_id
        if active_task_id is None:
            if not title or not summary or not prompt:
                raise ToolFailure(
                    code="INVALID_PARAM",
                    message="TaskRun 缺少创建任务所需字段。",
                    text="参数错误：未提供 task_id 时，必须同时提供 title、summary 和 prompt。",
                )
            task = create_task(
                tasks_dir=active_runtime_context.tasks_dir,
                title=title,
                summary=summary,
                kind="analysis",
                prompt=prompt,
                subagent_type=subagent_type,
                model_route=model_route,
            )
            active_task_id = int(task["id"])

        result = await run_subagent_task(
            runtime_context=active_runtime_context,
            task_id=int(active_task_id),
        )
        task = result["task"]
        return success_response(
            data={
                "task_id": task["id"],
                "subagent_type": result["subagent_type"],
                "model_route": result["model_route"],
                "result_summary": result["result_summary"],
            },
            text=f"子代理已完成 task_{task['id']}：{result['result_summary']}",
            stats=build_stats(start_time),
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


def background_run(
    *,
    command: str,
    title: str | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    start_time = start_timer()
    params_input = {
        "command": command,
        "title": title,
    }
    try:
        active_runtime_context = _require_runtime_context(runtime_context)
        result = start_background_command(
            runtime_context=active_runtime_context,
            command=command,
            title=title,
        )
        return success_response(
            data=result,
            text=f"后台任务已启动，task_{result['task_id']} 正在运行。",
            stats=build_stats(start_time),
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


def _task_create_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    title: str,
    summary: str,
    kind: str,
    prompt: str | None = None,
    subagent_type: str | None = None,
    model_route: str | None = None,
) -> ToolResponse:
    params_input = {
        "title": title,
        "summary": summary,
        "kind": kind,
        "prompt": prompt,
        "subagent_type": subagent_type,
        "model_route": model_route,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="TaskCreate",
        params_input=params_input,
        invoke=lambda: task_create(
            title=title,
            summary=summary,
            kind=kind,
            prompt=prompt,
            subagent_type=subagent_type,
            model_route=model_route,
            runtime_context=ctx.context,
        ),
    )


def _task_update_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    task_id: int,
    status: str | None = None,
    add_blocked_by: list[int] | None = None,
    add_blocks: list[int] | None = None,
    owner: str | None = None,
    result_summary: str | None = None,
    result_artifact: str | None = None,
    error: str | None = None,
) -> ToolResponse:
    params_input = {
        "task_id": task_id,
        "status": status,
        "add_blocked_by": add_blocked_by,
        "add_blocks": add_blocks,
        "owner": owner,
        "result_summary": result_summary,
        "result_artifact": result_artifact,
        "error": error,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="TaskUpdate",
        params_input=params_input,
        invoke=lambda: task_update(
            task_id=task_id,
            status=status,
            add_blocked_by=add_blocked_by,
            add_blocks=add_blocks,
            owner=owner,
            result_summary=result_summary,
            result_artifact=result_artifact,
            error=error,
            runtime_context=ctx.context,
        ),
    )


def _task_list_tool(ctx: RunContextWrapper[ToolRuntimeContext]) -> ToolResponse:
    return run_traced_tool(
        ctx.context,
        tool_name="TaskList",
        params_input={},
        invoke=lambda: task_list(runtime_context=ctx.context),
    )


def _task_get_tool(ctx: RunContextWrapper[ToolRuntimeContext], task_id: int) -> ToolResponse:
    params_input = {"task_id": task_id}
    return run_traced_tool(
        ctx.context,
        tool_name="TaskGet",
        params_input=params_input,
        invoke=lambda: task_get(task_id=task_id, runtime_context=ctx.context),
    )


async def _task_run_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    task_id: int | None = None,
    title: str | None = None,
    summary: str | None = None,
    prompt: str | None = None,
    subagent_type: str = "general",
    model_route: str = "light",
) -> ToolResponse:
    params_input = {
        "task_id": task_id,
        "title": title,
        "summary": summary,
        "prompt": prompt,
        "subagent_type": subagent_type,
        "model_route": model_route,
    }
    return await run_traced_tool_async(
        ctx.context,
        tool_name="TaskRun",
        params_input=params_input,
        invoke=lambda: task_run(
            task_id=task_id,
            title=title,
            summary=summary,
            prompt=prompt,
            subagent_type=subagent_type,
            model_route=model_route,
            runtime_context=ctx.context,
        ),
    )


def _background_run_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    command: str,
    title: str | None = None,
) -> ToolResponse:
    params_input = {
        "command": command,
        "title": title,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="BackgroundRun",
        params_input=params_input,
        invoke=lambda: background_run(
            command=command,
            title=title,
            runtime_context=ctx.context,
        ),
    )


task_create_tool = function_tool(
    _task_create_tool,
    name_override="TaskCreate",
    description_override="创建一个持久化任务节点。",
)
task_update_tool = function_tool(
    _task_update_tool,
    name_override="TaskUpdate",
    description_override="更新一个已有任务的状态、依赖或结果。",
)
task_list_tool = function_tool(
    _task_list_tool,
    name_override="TaskList",
    description_override="列出当前 session 下的任务概览。",
)
task_get_tool = function_tool(
    _task_get_tool,
    name_override="TaskGet",
    description_override="读取一个任务的完整详情。",
)
task_run_tool = function_tool(
    _task_run_tool,
    name_override="TaskRun",
    description_override="同步运行一个分析型子代理，并把总结写回任务图。",
)
background_run_tool = function_tool(
    _background_run_tool,
    name_override="BackgroundRun",
    description_override="启动一个后台本地命令任务，并立即返回 task_id。",
)

TASK_TOOLS = [
    task_create_tool,
    task_update_tool,
    task_list_tool,
    task_get_tool,
    task_run_tool,
    background_run_tool,
]
