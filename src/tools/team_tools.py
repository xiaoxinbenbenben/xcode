from __future__ import annotations

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tasks.agent_team import (
    claim_next_task,
    request_plan_review,
    request_shutdown,
    respond_plan_review,
    respond_shutdown_request,
    list_teammates,
    send_team_message,
    spawn_teammate,
)
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool,
    start_timer,
)

# 这层只做 team 工具和底层 runtime 的薄适配：
# 参数整理、统一协议包装、trace 记录。


def _public_member_view(member: dict[str, object]) -> dict[str, object]:
    # transcript_path 是 session 内部调试产物路径。
    # 在 workspace 模式下它并不一定可被 Read 直接访问，所以不暴露给模型。
    return {
        key: value
        for key, value in member.items()
        if key != "transcript_path"
    }


def _public_team_view(result: dict[str, object]) -> dict[str, object]:
    # List / Spawn 都统一走这层裁剪，避免模型把内部调试字段当成工作区文件路径。
    data = dict(result)
    member = data.get("member")
    if isinstance(member, dict):
        data["member"] = _public_member_view(member)
    members = data.get("members")
    if isinstance(members, list):
        data["members"] = [
            _public_member_view(item) if isinstance(item, dict) else item
            for item in members
        ]
    return data


def _spawn_teammate(
    *,
    name: str,
    role: str,
    prompt: str,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # 这里先只包装 team runtime 的最小创建能力，不顺手做 task/worktree 绑定。
    start_time = start_timer()
    params_input = {
        "name": name,
        "role": role,
        "prompt": prompt,
    }
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法创建 teammate。",
            )
        result = spawn_teammate(
            runtime_context,
            name=name,
            role=role,
            prompt=prompt,
        )
        public_result = _public_team_view(result)
        member = result["member"]
        return success_response(
            data=public_result,
            text=f"已创建 teammate '{member['name']}'，当前状态：{member['status']}",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _list_teammates(*, runtime_context: ToolRuntimeContext | None = None) -> ToolResponse:
    # ListTeammates 只返回当前 team_state 视图，不去推导额外状态。
    start_time = start_timer()
    params_input: dict[str, object] = {}
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法查看 teammate 列表。",
            )
        result = list_teammates(runtime_context)
        public_result = _public_team_view(result)
        return success_response(
            data=public_result,
            text=f"当前共有 {len(result['members'])} 个 teammate。",
            stats=build_stats(start_time, total=len(result["members"])),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _send_message(
    *,
    to: str,
    content: str,
    summary: str | None = None,
    message_type: str = "message",
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # 显式消息发送是 Phase 1 唯一协作入口，不把 inbox 读取暴露给模型。
    start_time = start_timer()
    params_input = {
        "to": to,
        "content": content,
        "summary": summary,
        "message_type": message_type,
    }
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法发送团队消息。",
            )
        result = send_team_message(
            runtime_context,
            to=to,
            content=content,
            summary=summary,
            message_type=message_type,
        )
        return success_response(
            data={"message": result},
            text=f"已向 '{to}' 发送团队消息。",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _shutdown_request(
    *,
    name: str,
    content: str = "请结束当前 teammate 运行。",
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # ShutdownRequest 是显式的 phase 2 协议入口，返回 request_id 而不是直接等待结束。
    start_time = start_timer()
    params_input = {
        "name": name,
        "content": content,
    }
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法发送关闭请求。",
            )
        result = request_shutdown(
            runtime_context,
            name=name,
            content=content,
        )
        return success_response(
            data=result,
            text=f"已向 teammate '{name}' 发送关闭请求，request_id={result['request_id']}。",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _shutdown_response(
    *,
    request_id: str,
    status: str,
    feedback: str | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # 这个工具只给 teammate 用，用来显式响应 shutdown_request。
    start_time = start_timer()
    params_input = {
        "request_id": request_id,
        "status": status,
        "feedback": feedback,
    }
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法响应关闭请求。",
            )
        result = respond_shutdown_request(
            runtime_context,
            request_id=request_id,
            status=status,
            feedback=feedback,
        )
        return success_response(
            data=result,
            text=f"已响应 shutdown request {request_id}，结果：{status}。",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _plan_approval(
    *,
    mode: str,
    summary: str | None = None,
    content: str | None = None,
    request_id: str | None = None,
    status: str | None = None,
    feedback: str | None = None,
    to: str = "team-lead",
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # 这个工具一体承接 plan review 的 request / response。
    # teammate 用 request，team-lead 用 response。
    start_time = start_timer()
    params_input = {
        "mode": mode,
        "summary": summary,
        "content": content,
        "request_id": request_id,
        "status": status,
        "feedback": feedback,
        "to": to,
    }
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法处理计划审阅请求。",
            )
        if mode == "request":
            if not summary or not content:
                raise ToolFailure(
                    code="INVALID_PARAM",
                    message="plan review request 缺少 summary 或 content。",
                    text="参数错误：发起计划审阅时必须提供 summary 和 content。",
                )
            result = request_plan_review(
                runtime_context,
                summary=summary,
                content=content,
                to=to,
            )
            text = f"已发起计划审阅请求，request_id={result['request_id']}。"
        elif mode == "response":
            if not request_id or not status:
                raise ToolFailure(
                    code="INVALID_PARAM",
                    message="plan review response 缺少 request_id 或 status。",
                    text="参数错误：回应计划审阅时必须提供 request_id 和 status。",
                )
            result = respond_plan_review(
                runtime_context,
                request_id=request_id,
                status=status,
                feedback=feedback,
            )
            text = f"已回应计划审阅请求 {request_id}，结果：{status}。"
        else:
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"未知 mode: {mode}",
                text="参数错误：PlanApproval 的 mode 只能是 request 或 response。",
            )
        return success_response(
            data=result,
            text=text,
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _claim_task(
    *,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # ClaimTask 只给 teammate 用，让 worker 显式确认“下一条工作来自 task board”。
    start_time = start_timer()
    params_input: dict[str, object] = {}
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法认领任务。",
            )
        result = claim_next_task(runtime_context)
        task = result.get("task")
        if task is None:
            text = "当前没有可认领的新任务。"
        elif result.get("claimed"):
            text = f"已认领任务 task_{task['id']}：{task['title']}"
        else:
            text = f"当前仍在处理 task_{task['id']}：{task['title']}"
        return success_response(
            data=result,
            text=text,
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _idle(
    *,
    summary: str | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    # teammate 当前本来就会在每轮结束后回到 idle。
    # 这个工具先只提供一个显式“我现在空闲了”的信号入口。
    start_time = start_timer()
    params_input = {"summary": summary}
    try:
        if runtime_context is None:
            raise ToolFailure(
                code="NO_SESSION",
                message="当前没有可用 session。",
                text="当前没有可用会话，暂时无法切换为空闲状态。",
            )
        if summary:
            send_team_message(
                runtime_context,
                to="team-lead",
                content=summary,
                summary="teammate idle",
                message_type="message",
            )
        return success_response(
            data={"status": "idle"},
            text="当前 teammate 已声明进入 idle。",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                session_id=runtime_context.session_id,
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


def _spawn_teammate_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    name: str,
    role: str,
    prompt: str,
) -> ToolResponse:
    # SDK function tool wrapper 只负责接 RunContextWrapper，再转发给纯函数主体。
    params_input = {
        "name": name,
        "role": role,
        "prompt": prompt,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="SpawnTeammate",
        params_input=params_input,
        invoke=lambda: _spawn_teammate(
            name=name,
            role=role,
            prompt=prompt,
            runtime_context=ctx.context,
        ),
    )


def _list_teammates_tool(ctx: RunContextWrapper[ToolRuntimeContext]) -> ToolResponse:
    return run_traced_tool(
        ctx.context,
        tool_name="ListTeammates",
        params_input={},
        invoke=lambda: _list_teammates(runtime_context=ctx.context),
    )


def _send_message_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    to: str,
    content: str,
    summary: str | None = None,
    message_type: str = "message",
) -> ToolResponse:
    # SendMessage 同时服务 lead 和 teammate，所以这里不硬编码发送者。
    params_input = {
        "to": to,
        "content": content,
        "summary": summary,
        "message_type": message_type,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="SendMessage",
        params_input=params_input,
        invoke=lambda: _send_message(
            to=to,
            content=content,
            summary=summary,
            message_type=message_type,
            runtime_context=ctx.context,
        ),
    )


def _shutdown_request_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    name: str,
    content: str = "请结束当前 teammate 运行。",
) -> ToolResponse:
    params_input = {
        "name": name,
        "content": content,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="ShutdownRequest",
        params_input=params_input,
        invoke=lambda: _shutdown_request(
            name=name,
            content=content,
            runtime_context=ctx.context,
        ),
    )


def _shutdown_response_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    request_id: str,
    status: str,
    feedback: str | None = None,
) -> ToolResponse:
    params_input = {
        "request_id": request_id,
        "status": status,
        "feedback": feedback,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="ShutdownResponse",
        params_input=params_input,
        invoke=lambda: _shutdown_response(
            request_id=request_id,
            status=status,
            feedback=feedback,
            runtime_context=ctx.context,
        ),
    )


def _plan_approval_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    mode: str,
    summary: str | None = None,
    content: str | None = None,
    request_id: str | None = None,
    status: str | None = None,
    feedback: str | None = None,
    to: str = "team-lead",
) -> ToolResponse:
    params_input = {
        "mode": mode,
        "summary": summary,
        "content": content,
        "request_id": request_id,
        "status": status,
        "feedback": feedback,
        "to": to,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="PlanApproval",
        params_input=params_input,
        invoke=lambda: _plan_approval(
            mode=mode,
            summary=summary,
            content=content,
            request_id=request_id,
            status=status,
            feedback=feedback,
            to=to,
            runtime_context=ctx.context,
        ),
    )


def _claim_task_tool(ctx: RunContextWrapper[ToolRuntimeContext]) -> ToolResponse:
    # teammate 的 ClaimTask 不接受外部参数，避免模型自己拼 task_id 破坏认领规则。
    return run_traced_tool(
        ctx.context,
        tool_name="ClaimTask",
        params_input={},
        invoke=lambda: _claim_task(runtime_context=ctx.context),
    )


def _idle_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    summary: str | None = None,
) -> ToolResponse:
    params_input = {"summary": summary}
    return run_traced_tool(
        ctx.context,
        tool_name="Idle",
        params_input=params_input,
        invoke=lambda: _idle(
            summary=summary,
            runtime_context=ctx.context,
        ),
    )


spawn_teammate_tool = function_tool(
    _spawn_teammate_tool,
    name_override="SpawnTeammate",
    description_override="创建一个长寿命 teammate，让它在当前 session 中持续存活。",
)
list_teammates_tool = function_tool(
    _list_teammates_tool,
    name_override="ListTeammates",
    description_override="列出当前 session 下的 teammate 状态。",
)
send_message_tool = function_tool(
    _send_message_tool,
    name_override="SendMessage",
    description_override="向 team-lead 或某个 teammate 发送一条团队消息。",
)
shutdown_request_tool = function_tool(
    _shutdown_request_tool,
    name_override="ShutdownRequest",
    description_override="向 teammate 发送一个带 request_id 的关闭请求。",
)
shutdown_response_tool = function_tool(
    _shutdown_response_tool,
    name_override="ShutdownResponse",
    description_override="响应一个 teammate 关闭请求，返回 approved 或 rejected。",
)
plan_approval_tool = function_tool(
    _plan_approval_tool,
    name_override="PlanApproval",
    description_override="发起或回应一次计划审阅请求。mode=request 时发起，mode=response 时回应。",
)
claim_task_tool = function_tool(
    _claim_task_tool,
    name_override="ClaimTask",
    description_override="让当前 teammate 从 task board 认领一个可执行任务；如果已有任务则返回当前任务。",
)
idle_tool = function_tool(
    _idle_tool,
    name_override="Idle",
    description_override="显式声明当前 teammate 没有更多工作，可以回到空闲状态。",
)

# Team lead 和 teammate 的工具集在 Phase 2 开始分叉。
# root agent 只挂 team-lead 视角需要的工具。
TEAM_TOOLS = [
    spawn_teammate_tool,
    list_teammates_tool,
    send_message_tool,
    shutdown_request_tool,
    plan_approval_tool,
]

__all__ = [
    "TEAM_TOOLS",
    "claim_task_tool",
    "idle_tool",
    "spawn_teammate_tool",
    "list_teammates_tool",
    "send_message_tool",
    "shutdown_request_tool",
    "shutdown_response_tool",
    "plan_approval_tool",
]
