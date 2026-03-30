from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha1
from typing import Literal, TypedDict

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool,
    start_timer,
)

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]
VALID_TODO_STATUSES: tuple[TodoStatus, ...] = (
    "pending",
    "in_progress",
    "completed",
    "cancelled",
)
TERMINAL_TODO_STATUSES = {"completed", "cancelled"}
MAX_TODO_COUNT = 10
MAX_TODO_CONTENT_LENGTH = 60


class TodoInputItem(TypedDict):
    content: str
    status: TodoStatus


def _normalize_summary(summary: str) -> str:
    if not isinstance(summary, str) or not summary.strip():
        raise ToolFailure(
            code="INVALID_PARAM",
            message="summary 参数非法。",
            text="参数错误：summary 不能为空。",
        )
    return summary.strip()


def _normalize_todos(todos: list[TodoInputItem]) -> list[dict[str, str]]:
    if not isinstance(todos, list) or not todos:
        raise ToolFailure(
            code="INVALID_PARAM",
            message="todos 参数非法。",
            text="参数错误：todos 必须是非空列表。",
        )
    if len(todos) > MAX_TODO_COUNT:
        raise ToolFailure(
            code="INVALID_PARAM",
            message="todo 数量超过上限。",
            text=f"参数错误：todos 最多只能包含 {MAX_TODO_COUNT} 项。",
        )

    normalized: list[dict[str, str]] = []
    in_progress_count = 0

    # 这里显式逐项校验，避免模型把非法状态或空内容悄悄塞进当前计划。
    for index, item in enumerate(todos, start=1):
        if not isinstance(item, dict):
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"第 {index} 个 todo 不是对象。",
                text="参数错误：todos 中的每一项都必须是对象。",
            )

        content = item.get("content")
        status = item.get("status")
        if not isinstance(content, str) or not content.strip():
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"第 {index} 个 todo 的 content 非法。",
                text="参数错误：每个 todo 的 content 都必须是非空字符串。",
            )

        content = content.strip()
        if len(content) > MAX_TODO_CONTENT_LENGTH:
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"第 {index} 个 todo 的 content 过长。",
                text=f"参数错误：单条 todo 最长只能是 {MAX_TODO_CONTENT_LENGTH} 个字符。",
            )
        if status not in VALID_TODO_STATUSES:
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"第 {index} 个 todo 的 status 非法。",
                text="参数错误：todo.status 必须是 pending / in_progress / completed / cancelled。",
            )

        if status == "in_progress":
            in_progress_count += 1

        normalized.append({"content": content, "status": status})

    if in_progress_count > 1:
        raise ToolFailure(
            code="INVALID_PARAM",
            message="同时存在多个 in_progress todo。",
            text="参数错误：同一时刻最多只能有一个 in_progress 任务。",
        )

    return normalized


def _count_statuses(todos: list[dict[str, str]]) -> dict[str, int]:
    counts = {
        "total": len(todos),
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
        "cancelled": 0,
    }
    for item in todos:
        counts[item["status"]] += 1
    return counts


def _build_recap(todos: list[dict[str, str]]) -> str:
    counts = _count_statuses(todos)
    done_count = counts["completed"] + counts["cancelled"]
    prefix = f"[{done_count}/{counts['total']}]"

    in_progress_items = [item["content"] for item in todos if item["status"] == "in_progress"]
    pending_items = [item["content"] for item in todos if item["status"] == "pending"]
    cancelled_items = [item["content"] for item in todos if item["status"] == "cancelled"]

    parts = [prefix]
    if in_progress_items:
        parts.append(f"In progress: {in_progress_items[0]}.")
    if pending_items:
        parts.append(f"Pending: {'; '.join(pending_items[:3])}.")
    if cancelled_items:
        parts.append(f"Cancelled: {'; '.join(cancelled_items[:2])}.")

    # 若当前没有进行中和待处理项，就返回一个短的完成态总结，避免 recap 变成空壳。
    if not in_progress_items and not pending_items:
        if counts["cancelled"] == counts["total"]:
            parts.append("All todos cancelled.")
        elif counts["completed"] == counts["total"]:
            parts.append("All todos completed.")
        else:
            parts.append("All todos resolved.")

    return " ".join(parts)


def _status_marker(status: str) -> str:
    return {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
        "cancelled": "[-]",
    }[status]

def _build_user_text(
    summary: str,
    todos: list[dict[str, str]],
    *,
    persisted: bool,
    archive_path: str | None,
) -> str:
    lines = [f"TODO: {summary}"]
    lines.extend(f"{_status_marker(item['status'])} {item['content']}" for item in todos)
    if persisted and archive_path:
        lines.append(f"Archived: {archive_path}")
    return "\n".join(lines)


def _is_terminal_todo_list(todos: list[dict[str, str]]) -> bool:
    return all(item["status"] in TERMINAL_TODO_STATUSES for item in todos)


def _build_completion_fingerprint(summary: str, todos: list[dict[str, str]]) -> str:
    # 这里用 summary + 当前完整列表做 fingerprint，重复提交同一完成态时不再重复归档。
    payload = json.dumps(
        {"summary": summary, "todos": todos},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha1(payload.encode("utf-8")).hexdigest()


def _build_markdown_block(
    *,
    block_index: int,
    timestamp: str,
    summary: str,
    recap: str,
    todos: list[dict[str, str]],
) -> str:
    completed_items = [item["content"] for item in todos if item["status"] == "completed"]
    cancelled_items = [item["content"] for item in todos if item["status"] == "cancelled"]

    lines = [
        f"## task{block_index}-{timestamp}",
        "",
        f"Summary: {summary}",
        f"Recap: {recap}",
        "",
    ]
    if completed_items:
        lines.append("### Completed")
        lines.extend(f"- [x] {content}" for content in completed_items)
        lines.append("")
    if cancelled_items:
        lines.append("### Cancelled")
        lines.extend(f"- [ ] ~~{content}~~" for content in cancelled_items)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _persist_completed_todos(
    summary: str,
    todos: list[dict[str, str]],
    recap: str,
    runtime_context: ToolRuntimeContext,
) -> tuple[bool, str | None]:
    archive_path = runtime_context.get_todo_archive_path()
    fingerprint = _build_completion_fingerprint(summary, todos)

    if runtime_context.last_persisted_todo_fingerprint == fingerprint:
        return False, str(archive_path)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    block = _build_markdown_block(
        block_index=runtime_context.todo_completed_block_count + 1,
        timestamp=timestamp,
        summary=summary,
        recap=recap,
        todos=todos,
    )

    # 同一 session 的归档文件会追加多个完成块，因此只在非空文件前补一个空行。
    prefix = ""
    if archive_path.exists() and archive_path.stat().st_size > 0:
        prefix = "\n"
    archive_path.write_text(
        (archive_path.read_text(encoding="utf-8") if archive_path.exists() else "") + prefix + block,
        encoding="utf-8",
    )
    runtime_context.mark_todo_persisted(fingerprint, archive_path)
    return True, str(archive_path)


def todo_write(
    summary: str,
    todos: list[TodoInputItem],
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    """用当前完整列表覆盖 todo 状态，并在完成时归档。"""
    start_time = start_timer()
    params_input = {
        "summary": summary,
        "todos": todos,
    }
    active_runtime_context = runtime_context or ToolRuntimeContext()

    try:
        normalized_summary = _normalize_summary(summary)
        normalized_todos = _normalize_todos(todos)
        recap = _build_recap(normalized_todos)
        active_runtime_context.set_todo_state(normalized_summary, normalized_todos, recap)

        persisted = False
        archive_path: str | None = None
        if _is_terminal_todo_list(normalized_todos):
            persisted, archive_path = _persist_completed_todos(
                normalized_summary,
                normalized_todos,
                recap,
                active_runtime_context,
            )
        else:
            active_runtime_context.clear_todo_persist_fingerprint()
            if active_runtime_context.todo_archive_path is not None:
                archive_path = str(active_runtime_context.todo_archive_path)

        counts = _count_statuses(normalized_todos)
        return success_response(
            data={
                "summary": normalized_summary,
                "todos": normalized_todos,
                "recap": recap,
                "persisted": persisted,
                "archive_path": archive_path,
            },
            text=_build_user_text(
                normalized_summary,
                normalized_todos,
                persisted=persisted,
                archive_path=archive_path,
            ),
            stats=build_stats(start_time, **counts),
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


def _todo_write_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    summary: str,
    todos: list[TodoInputItem],
) -> ToolResponse:
    # SDK session 负责对话历史，这里的 runtime context 负责 todo 状态与 tool tracing。
    params_input = {
        "summary": summary,
        "todos": todos,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="TodoWrite",
        params_input=params_input,
        invoke=lambda: todo_write(
            summary=summary,
            todos=todos,
            runtime_context=ctx.context,
        ),
    )


todo_write_tool = function_tool(
    _todo_write_tool,
    name_override="TodoWrite",
    description_override="覆盖当前完整 todo 列表，用于多步骤 coding 任务的计划跟踪。",
)

TODO_TOOLS = [todo_write_tool]
