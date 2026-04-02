from __future__ import annotations

from pathlib import Path

from agents import RunContextWrapper, function_tool

from src.runtime.session import ToolRuntimeContext
from src.protocol import ToolResponse, success_response
from src.tools.common import (
    ToolFailure,
    WorkspacePath,
    build_context,
    build_stats,
    ensure_exists,
    error_from_failure,
    get_file_snapshot,
    read_workspace_text_file,
    require_existing_file_lock,
    resolve_workspace_path,
    run_traced_tool,
    start_timer,
)

# 这里先只放两类最小写入工具：
# Edit 负责已有文件上的单点替换，Write 负责全量创建或覆盖。


def _ensure_existing_text_file(path: str) -> WorkspacePath:
    workspace_path = resolve_workspace_path(path)
    ensure_exists(workspace_path)
    if workspace_path.resolved.is_dir():
        raise ToolFailure(
            code="IS_DIRECTORY",
            message=f"路径 '{workspace_path.relative_posix}' 是目录。",
            text=f"'{workspace_path.relative_posix}' 是目录，不能直接编辑或写入。",
        )
    return workspace_path


def _load_editable_text(workspace_path: WorkspacePath) -> str:
    # Read 允许 replace 回退继续看上下文，但真正写入前要更保守，避免把损坏字符静默写回磁盘。
    content, _encoding, fallback_encoding = read_workspace_text_file(workspace_path)
    if fallback_encoding is not None:
        raise ToolFailure(
            code="INVALID_PARAM",
            message=f"文件 '{workspace_path.relative_posix}' 不是可安全编辑的 UTF-8 文本。",
            text="安全编辑当前只支持可直接按 UTF-8 读取的文本文件，请先处理编码问题。",
        )
    return content


def _write_text_file(path: Path, content: str) -> int:
    # 新文件允许自动创建父目录；这样 Write 可以直接承担“最小创建文件”能力。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return len(content.encode("utf-8"))


def _resolve_expected_lock(
    workspace_path: WorkspacePath,
    *,
    runtime_context: ToolRuntimeContext | None,
    expected_mtime_ms: int | None,
    expected_size_bytes: int | None,
) -> tuple[int | None, int | None]:
    # 显式参数始终优先；只有调用方没传时，才尝试复用同一路径最近一次 Read 的快照。
    if expected_mtime_ms is not None and expected_size_bytes is not None:
        return expected_mtime_ms, expected_size_bytes
    if runtime_context is None:
        return expected_mtime_ms, expected_size_bytes

    snapshot = runtime_context.get_read_snapshot(workspace_path.relative_posix)
    if snapshot is None:
        return expected_mtime_ms, expected_size_bytes

    return snapshot.file_mtime_ms, snapshot.file_size_bytes


def _remember_written_snapshot(
    workspace_path: WorkspacePath,
    *,
    runtime_context: ToolRuntimeContext | None,
) -> None:
    if runtime_context is None:
        return

    current_snapshot = get_file_snapshot(workspace_path.resolved)
    runtime_context.remember_read_snapshot(
        workspace_path.relative_posix,
        file_mtime_ms=current_snapshot.mtime_ms,
        file_size_bytes=current_snapshot.size_bytes,
    )


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    expected_mtime_ms: int | None = None,
    expected_size_bytes: int | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    """在已有文件中替换一段唯一的旧文本。"""
    start_time = start_timer()
    path_resolved: str | None = None
    params_input = {
        "path": path,
        "old_string": old_string,
        "new_string": new_string,
        "expected_mtime_ms": expected_mtime_ms,
        "expected_size_bytes": expected_size_bytes,
    }

    try:
        if not path or not old_string:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="path 或 old_string 参数非法。",
                text="参数错误：path 和 old_string 不能为空。",
            )

        workspace_root = runtime_context.execution_root if runtime_context is not None else None
        workspace_path = resolve_workspace_path(path, workspace_root=workspace_root)
        ensure_exists(workspace_path)
        if workspace_path.resolved.is_dir():
            raise ToolFailure(
                code="IS_DIRECTORY",
                message=f"路径 '{workspace_path.relative_posix}' 是目录。",
                text=f"'{workspace_path.relative_posix}' 是目录，不能直接编辑或写入。",
            )
        path_resolved = workspace_path.relative_posix
        expected_mtime_ms, expected_size_bytes = _resolve_expected_lock(
            workspace_path,
            runtime_context=runtime_context,
            expected_mtime_ms=expected_mtime_ms,
            expected_size_bytes=expected_size_bytes,
        )
        # 先比对一次锁值，尽早发现“你看到的旧版本”和“磁盘当前版本”不一致。
        require_existing_file_lock(
            workspace_path,
            expected_mtime_ms=expected_mtime_ms,
            expected_size_bytes=expected_size_bytes,
        )
        original_content = _load_editable_text(workspace_path)

        match_count = original_content.count(old_string)
        if match_count == 0:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="old_string 未在目标文件中命中。",
                text="未找到 old_string，请先重新 Read 并确保传入的是文件中的精确片段。",
            )
        if match_count > 1:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="old_string 在目标文件中命中多次。",
                text="old_string 在文件中出现了多次，请增加更多上下文后再重试。",
            )

        updated_content = original_content.replace(old_string, new_string, 1)
        # 真正落盘前再校验一次，尽量缩小“读完到写入之间”的竞争窗口。
        require_existing_file_lock(
            workspace_path,
            expected_mtime_ms=expected_mtime_ms,
            expected_size_bytes=expected_size_bytes,
        )
        bytes_written = _write_text_file(workspace_path.resolved, updated_content)
        _remember_written_snapshot(
            workspace_path,
            runtime_context=runtime_context,
        )

        return success_response(
            data={
                "applied": True,
                "replacements": 1,
            },
            text=f"已更新 '{workspace_path.relative_posix}' 中的 1 处文本片段。",
            stats=build_stats(
                start_time,
                bytes_written=bytes_written,
                replacements=1,
            ),
            context=build_context(
                params_input=params_input,
                path_resolved=workspace_path.relative_posix,
            ),
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )
    except PermissionError as exc:
        return error_from_failure(
            ToolFailure(
                code="PERMISSION_DENIED",
                message=str(exc),
                text="编辑文件时权限不足。",
            ),
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )
    except OSError as exc:
        return error_from_failure(
            ToolFailure(
                code="EXECUTION_ERROR",
                message=str(exc),
                text="编辑文件时发生磁盘读写错误。",
            ),
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )


def write_file(
    path: str,
    content: str,
    expected_mtime_ms: int | None = None,
    expected_size_bytes: int | None = None,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    """写入完整文件内容；已有文件要求先读取并携带锁字段。"""
    start_time = start_timer()
    path_resolved: str | None = None
    params_input = {
        "path": path,
        "content": content,
        "expected_mtime_ms": expected_mtime_ms,
        "expected_size_bytes": expected_size_bytes,
    }

    try:
        if not path:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="path 参数非法。",
                text="参数错误：path 不能为空。",
            )

        workspace_root = runtime_context.execution_root if runtime_context is not None else None
        workspace_path = resolve_workspace_path(path, workspace_root=workspace_root)
        path_resolved = workspace_path.relative_posix
        target_exists = workspace_path.resolved.exists()
        if target_exists and workspace_path.resolved.is_dir():
            raise ToolFailure(
                code="IS_DIRECTORY",
                message=f"路径 '{workspace_path.relative_posix}' 是目录。",
                text=f"'{workspace_path.relative_posix}' 是目录，不能直接写入文件内容。",
            )

        operation = "update" if target_exists else "create"
        expected_mtime_ms, expected_size_bytes = _resolve_expected_lock(
            workspace_path,
            runtime_context=runtime_context,
            expected_mtime_ms=expected_mtime_ms,
            expected_size_bytes=expected_size_bytes,
        )
        if target_exists:
            # 对已有文件，Write 也必须先验证 Read 返回的那组锁值，避免直接全量覆盖。
            require_existing_file_lock(
                workspace_path,
                expected_mtime_ms=expected_mtime_ms,
                expected_size_bytes=expected_size_bytes,
            )
            _load_editable_text(workspace_path)
            require_existing_file_lock(
                workspace_path,
                expected_mtime_ms=expected_mtime_ms,
                expected_size_bytes=expected_size_bytes,
            )

        bytes_written = _write_text_file(workspace_path.resolved, content)
        _remember_written_snapshot(
            workspace_path,
            runtime_context=runtime_context,
        )

        return success_response(
            data={
                "applied": True,
                "operation": operation,
            },
            text=f"已{ '创建' if operation == 'create' else '写入' } '{workspace_path.relative_posix}'。",
            stats=build_stats(
                start_time,
                bytes_written=bytes_written,
                content_length=len(content),
            ),
            context=build_context(
                params_input=params_input,
                path_resolved=workspace_path.relative_posix,
            ),
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )
    except PermissionError as exc:
        return error_from_failure(
            ToolFailure(
                code="PERMISSION_DENIED",
                message=str(exc),
                text="写入文件时权限不足。",
            ),
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )
    except OSError as exc:
        return error_from_failure(
            ToolFailure(
                code="EXECUTION_ERROR",
                message=str(exc),
                text="写入文件时发生磁盘读写错误。",
            ),
            start_time=start_time,
            params_input=params_input,
            path_resolved=path_resolved,
        )


def _edit_file_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    path: str,
    old_string: str,
    new_string: str,
) -> ToolResponse:
    params_input = {
        "path": path,
        "old_string": old_string,
        "new_string": new_string,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="Edit",
        params_input=params_input,
        invoke=lambda: edit_file(
            path=path,
            old_string=old_string,
            new_string=new_string,
            runtime_context=ctx.context,
        ),
    )


def _write_file_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    path: str,
    content: str,
) -> ToolResponse:
    params_input = {
        "path": path,
        "content": content,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="Write",
        params_input=params_input,
        invoke=lambda: write_file(
            path=path,
            content=content,
            runtime_context=ctx.context,
        ),
    )


edit_tool = function_tool(
    _edit_file_tool,
    name_override="Edit",
    description_override="替换已有文件中的一段唯一旧文本；编辑前必须先读取文件。",
)
write_tool = function_tool(
    _write_file_tool,
    name_override="Write",
    description_override="创建新文件或覆盖已有文件；覆盖已有文件前必须先读取文件。",
)

# 先把写入工具集中导出，方便 agent_factory 明确看到“读工具”和“写工具”是两组能力。
FILE_EDIT_TOOLS = [edit_tool, write_tool]
