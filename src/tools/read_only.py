from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from agents import RunContextWrapper, function_tool

from src.runtime.session import ToolRuntimeContext
from src.protocol import ToolResponse, partial_response, success_response
from src.tools.common import (
    PROJECT_ROOT,
    ToolFailure,
    build_output_truncation_notice,
    build_context,
    build_stats,
    ensure_exists,
    error_from_failure,
    get_file_snapshot,
    get_tool_output_limits,
    maybe_truncate_output_text,
    normalize_posix,
    read_workspace_text_file,
    resolve_workspace_path,
    should_skip_entry,
    sort_key_for_entry,
    start_timer,
)

# 这里保留四个职责非常明确的只读工具：
# LS 看结构，Glob 找路径，Grep 找内容，Read 读文件。

def _format_listing_line(entry: dict[str, str]) -> str:
    suffix = "/" if entry["type"] == "dir" else "@"
    if entry["type"] == "file":
        suffix = ""
    return f"{entry['path']}{suffix}"


def _make_entry(path: Path) -> dict[str, str]:
    entry_type = "link" if path.is_symlink() else ("dir" if path.is_dir() else "file")
    return {
        "path": normalize_posix(path),
        "type": entry_type,
    }


def _glob_matches(relative_path: str, pattern: str) -> bool:
    # 给 `**/foo` 做零层兼容，这样既能匹配 `a/b/foo`，也能匹配根下的 `foo`。
    candidate = PurePosixPath(relative_path)
    return candidate.match(pattern) or (
        pattern.startswith("**/") and candidate.match(pattern[3:])
    )


def _iter_workspace_files(
    search_root: Path,
    *,
    include_hidden: bool,
    include_ignored: bool,
) -> list[Path]:
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(search_root):
        root_path = Path(current_root)
        # 遍历时先剪枝目录，能让 Glob/Grep 共用同一套工作区过滤语义。
        dir_names[:] = sorted(
            [
                name
                for name in dir_names
                if not should_skip_entry(
                    relative_posix=normalize_posix(root_path / name),
                    include_hidden=include_hidden,
                    include_ignored=include_ignored,
                )
            ],
            key=str.casefold,
        )

        for file_name in sorted(file_names, key=str.casefold):
            file_path = root_path / file_name
            if should_skip_entry(
                relative_posix=normalize_posix(file_path),
                include_hidden=include_hidden,
                include_ignored=include_ignored,
            ):
                continue
            files.append(file_path)
    return files


def _sort_grep_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 先按 mtime 降序，再按路径和行号稳定排序，尽量保留 legacy 里“最近活跃代码优先”的语义。
    def sort_key(match: dict[str, Any]) -> tuple[float, str, int]:
        file_path = PROJECT_ROOT / match["file"]
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (-mtime, match["file"], int(match["line"]))

    return sorted(matches, key=sort_key)


def _grep_with_rg(
    *,
    pattern: str,
    path_resolved: str,
    include: str | None,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    # 优先走 rg，让“按内容找证据”保持足够快；这里只负责执行和解析，不做协议封装。
    command = ["rg", "--line-number", "--no-heading", "--color", "never"]
    if not case_sensitive:
        command.append("-i")
    if include:
        command.extend(["-g", include])
    command.extend([pattern, path_resolved])

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 1:
        return []
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "rg 搜索失败。")

    # 统一转成协议层期待的 matches 结构，后面无论是 rg 还是 Python fallback 都返回同一种形状。
    matches: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        file_part, line_part, text_part = line.split(":", 2)
        matches.append(
            {
                "file": Path(file_part).as_posix(),
                "line": int(line_part),
                "text": text_part,
            }
        )
    return _sort_grep_matches(matches)


def _grep_with_python(
    *,
    regex: re.Pattern[str],
    search_root: Path,
    include: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    # Python 路径只做最小回退实现：保证无 rg 时工具仍可用，但性能和覆盖率可能打折。
    matches: list[dict[str, Any]] = []
    truncated = False

    for file_path in _iter_workspace_files(
        search_root,
        include_hidden=False,
        include_ignored=False,
    ):
        relative_project_path = normalize_posix(file_path)
        relative_search_path = file_path.relative_to(search_root).as_posix()
        if include and not (
            _glob_matches(relative_project_path, include)
            or _glob_matches(relative_search_path, include)
        ):
            continue

        try:
            # fallback 里也跳过明显二进制文件，避免把不可读内容塞进搜索结果。
            if b"\x00" in file_path.read_bytes()[:8192]:
                continue
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for index, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                matches.append(
                    {
                        "file": relative_project_path,
                        "line": index,
                        "text": line,
                    }
                )
                if len(matches) > limit:
                    truncated = True
                    return _sort_grep_matches(matches[:limit]), truncated

    return _sort_grep_matches(matches), truncated


def list_files(
    path: str = ".",
    offset: int = 0,
    limit: int = 100,
    include_hidden: bool = False,
    ignore: list[str] | None = None,
) -> ToolResponse:
    """列出目录或文件条目，适合先看工作区结构。"""
    start_time = start_timer()
    params_input = {
        "path": path,
        "offset": offset,
        "limit": limit,
        "include_hidden": include_hidden,
        "ignore": ignore,
    }

    try:
        if offset < 0 or not 1 <= limit <= 200:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="offset 或 limit 参数非法。",
                text="参数错误：offset 必须 >= 0，limit 必须在 1 到 200 之间。",
            )

        workspace_path = resolve_workspace_path(path)
        ensure_exists(workspace_path)

        # LS 允许目标本身是文件，这样 agent 在拿到具体路径后也能快速确认它的类型。
        if workspace_path.resolved.is_file():
            entries = [_make_entry(workspace_path.resolved)]
        elif workspace_path.resolved.is_dir():
            children = sorted(
                workspace_path.resolved.iterdir(),
                key=sort_key_for_entry,
            )
            entries = [
                _make_entry(child)
                for child in children
                if not should_skip_entry(
                    relative_posix=normalize_posix(child),
                    include_hidden=include_hidden,
                    include_ignored=False,
                    ignore_patterns=ignore,
                )
            ]
        else:
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"路径 '{workspace_path.relative_posix}' 不是常规文件或目录。",
                text="目标路径既不是文件也不是目录，无法列出内容。",
            )

        total_entries = len(entries)
        paged_entries = entries[offset : offset + limit]
        # 分页不是失败，而是“结果可用但有折扣”，所以这里返回 partial。
        truncated = offset > 0 or offset + limit < total_entries
        response_builder = partial_response if truncated else success_response

        entry_lines = "\n".join(_format_listing_line(entry) for entry in paged_entries[:20])
        text = f"在 '{workspace_path.relative_posix}' 中列出了 {len(paged_entries)} 个条目。"
        if truncated:
            text += f" 结果已分页，当前为第 {offset} 到 {offset + len(paged_entries)} 项。"
        if entry_lines:
            text += f"\n\n{entry_lines}"

        return response_builder(
            data={"entries": paged_entries, "truncated": truncated},
            text=text,
            stats=build_stats(start_time, total_entries=total_entries, returned=len(paged_entries)),
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
        )
    except Exception as exc:
        return error_from_failure(
            ToolFailure(
                code="INTERNAL_ERROR",
                message=str(exc),
                text="列目录时发生内部错误。",
            ),
            start_time=start_time,
            params_input=params_input,
        )


def glob_search(
    pattern: str,
    path: str = ".",
    limit: int = 50,
    include_hidden: bool = False,
    include_ignored: bool = False,
) -> ToolResponse:
    """按名称或 glob 模式查找文件路径，不读取文件内容。"""
    start_time = start_timer()
    params_input = {
        "pattern": pattern,
        "path": path,
        "limit": limit,
        "include_hidden": include_hidden,
        "include_ignored": include_ignored,
    }

    try:
        if not pattern or not 1 <= limit <= 200:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="pattern 或 limit 参数非法。",
                text="参数错误：pattern 不能为空，limit 必须在 1 到 200 之间。",
            )

        workspace_path = resolve_workspace_path(path)
        ensure_exists(workspace_path)
        if not workspace_path.resolved.is_dir():
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"路径 '{workspace_path.relative_posix}' 不是目录。",
                text="Glob 只能在目录范围内搜索文件。",
            )

        matched_paths: list[str] = []
        visited = 0
        truncated = False

        for file_path in _iter_workspace_files(
            workspace_path.resolved,
            include_hidden=include_hidden,
            include_ignored=include_ignored,
        ):
            visited += 1
            # Glob 的 pattern 统一相对搜索根目录，而不是相对项目里任意位置。
            relative_search_path = file_path.relative_to(workspace_path.resolved).as_posix()
            if not _glob_matches(relative_search_path, pattern):
                continue
            matched_paths.append(normalize_posix(file_path))
            if len(matched_paths) > limit:
                truncated = True
                matched_paths = matched_paths[:limit]
                break

        response_builder = partial_response if truncated else success_response
        if matched_paths:
            listing = "\n".join(matched_paths[:20])
            text = f"在 '{workspace_path.relative_posix}' 中找到了 {len(matched_paths)} 个匹配 '{pattern}' 的文件。"
            if truncated:
                text += " 结果已截断，请收窄 pattern 或 path。"
            text += f"\n\n{listing}"
        else:
            text = f"在 '{workspace_path.relative_posix}' 中没有找到匹配 '{pattern}' 的文件。"

        return response_builder(
            data={"paths": matched_paths, "truncated": truncated},
            text=text,
            stats=build_stats(start_time, matched=len(matched_paths), visited=visited),
            context=build_context(
                params_input=params_input,
                path_resolved=workspace_path.relative_posix,
                pattern_normalized=pattern,
            ),
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
        )
    except Exception as exc:
        return error_from_failure(
            ToolFailure(
                code="INTERNAL_ERROR",
                message=str(exc),
                text="按名称搜索文件时发生内部错误。",
            ),
            start_time=start_time,
            params_input=params_input,
        )


def grep_search(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    case_sensitive: bool = False,
    limit: int = 100,
) -> ToolResponse:
    """按内容搜索代码，优先用 rg，不可用时回退到 Python。"""
    start_time = start_timer()
    params_input = {
        "pattern": pattern,
        "path": path,
        "include": include,
        "case_sensitive": case_sensitive,
        "limit": limit,
    }

    try:
        if not pattern or not 1 <= limit <= 200:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="pattern 或 limit 参数非法。",
                text="参数错误：pattern 不能为空，limit 必须在 1 到 200 之间。",
            )

        workspace_path = resolve_workspace_path(path)
        ensure_exists(workspace_path)
        if not workspace_path.resolved.is_dir():
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"路径 '{workspace_path.relative_posix}' 不是目录。",
                text="Grep 只能在目录范围内搜索内容。",
            )

        # 正则在真正执行前先编译，尽早把参数错误映射成结构化 INVALID_PARAM。
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        fallback_used = False
        fallback_reason: str | None = None
        truncated = False

        if shutil.which("rg"):
            try:
                matches = _grep_with_rg(
                    pattern=pattern,
                    path_resolved=workspace_path.relative_posix,
                    include=include,
                    case_sensitive=case_sensitive,
                )
            except Exception:
                # rg 失败时不直接报错，而是降级到 Python 搜索，并用 partial 暴露“结果打折”。
                fallback_used = True
                fallback_reason = "rg_failed"
                matches, truncated = _grep_with_python(
                    regex=regex,
                    search_root=workspace_path.resolved,
                    include=include,
                    limit=limit,
                )
        else:
            fallback_used = True
            fallback_reason = "rg_not_found"
            matches, truncated = _grep_with_python(
                regex=regex,
                search_root=workspace_path.resolved,
                include=include,
                limit=limit,
            )

        if len(matches) > limit:
            truncated = True
            matches = matches[:limit]

        # 对 Grep 来说，fallback 和截断都属于“结果可用但有折扣”。
        all_matches = matches
        rendered_matches = "\n".join(
            f"{item['file']}:{item['line']}: {item['text']}"
            for item in all_matches
        )
        output_truncation = maybe_truncate_output_text(
            tool_name="Grep",
            full_output=rendered_matches,
        )
        if output_truncation is not None:
            preview_limit = max(1, get_tool_output_limits().max_lines)
            matches = all_matches[:preview_limit]

        # 对 Grep 来说，fallback、结果截断和统一输出截断都属于“结果可用但有折扣”。
        status_is_partial = truncated or fallback_used or output_truncation is not None
        response_builder = partial_response if status_is_partial else success_response

        data: dict[str, Any] = {
            "matches": matches,
            "truncated": truncated,
        }
        if fallback_used:
            data["fallback_used"] = True
            data["fallback_reason"] = fallback_reason
        if output_truncation is not None:
            data["output_truncated"] = True
            data["truncation"] = output_truncation.as_dict()

        if matches:
            preview = (
                output_truncation.preview_text
                if output_truncation is not None
                else "\n".join(
                    f"{item['file']}:{item['line']}: {item['text']}"
                    for item in matches[:20]
                )
            )
            text = f"在 '{workspace_path.relative_posix}' 中找到了 {len(all_matches)} 条匹配 '{pattern}' 的结果。"
            if fallback_used:
                text += " 当前使用了 Python 回退搜索。"
            if truncated:
                text += " 结果已截断，请收窄 pattern、path 或 include。"
            if output_truncation is not None:
                text += " 当前只返回了预览结果。"
            text += f"\n\n{preview}"
            if output_truncation is not None:
                text += build_output_truncation_notice(output_truncation)
        else:
            text = f"在 '{workspace_path.relative_posix}' 中没有找到匹配 '{pattern}' 的内容。"
            if fallback_used:
                text += " 当前使用了 Python 回退搜索。"

        matched_files = len({item["file"] for item in all_matches})
        return response_builder(
            data=data,
            text=text,
            stats=build_stats(
                start_time,
                matched_lines=len(all_matches),
                matched_files=matched_files,
            ),
            context=build_context(
                params_input=params_input,
                path_resolved=workspace_path.relative_posix,
                pattern=pattern,
            ),
        )
    except re.error as exc:
        return error_from_failure(
            ToolFailure(
                code="INVALID_PARAM",
                message=f"无效正则表达式：{exc}",
                text="参数错误：pattern 不是合法的正则表达式。",
            ),
            start_time=start_time,
            params_input=params_input,
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
        )
    except Exception as exc:
        return error_from_failure(
            ToolFailure(
                code="INTERNAL_ERROR",
                message=str(exc),
                text="按内容搜索代码时发生内部错误。",
            ),
            start_time=start_time,
            params_input=params_input,
        )


def read_file(
    path: str,
    start_line: int = 1,
    limit: int = 500,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    """读取文本文件并返回带行号内容。"""
    start_time = start_timer()
    params_input = {
        "path": path,
        "start_line": start_line,
        "limit": limit,
    }

    try:
        if not path or start_line < 1 or not 1 <= limit <= 2000:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="path、start_line 或 limit 参数非法。",
                text="参数错误：path 不能为空，start_line 必须 >= 1，limit 必须在 1 到 2000 之间。",
            )

        workspace_path = resolve_workspace_path(path)
        ensure_exists(workspace_path)
        if workspace_path.resolved.is_dir():
            raise ToolFailure(
                code="IS_DIRECTORY",
                message=f"路径 '{workspace_path.relative_posix}' 是目录。",
                text=f"'{workspace_path.relative_posix}' 是目录。请先用 LS 查看其内容。",
            )

        # Read 是后续 Edit / Write 的前置步骤，所以这里顺手把锁字段对应的文件快照也读出来。
        raw_content, encoding, fallback_encoding = read_workspace_text_file(workspace_path)
        file_snapshot = get_file_snapshot(workspace_path.resolved)

        lines = raw_content.splitlines()
        total_lines = len(lines)
        if total_lines == 0 and start_line > 1:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="空文件只能从第 1 行开始读取。",
                text="参数错误：空文件只能使用 start_line=1。",
            )
        if total_lines > 0 and start_line > total_lines:
            raise ToolFailure(
                code="INVALID_PARAM",
                message=f"start_line={start_line} 超出文件总行数 {total_lines}。",
                text=f"参数错误：文件总行数为 {total_lines}，start_line 超出范围。",
            )

        start_index = start_line - 1
        selected_lines = lines[start_index : start_index + limit]
        end_line = start_line + len(selected_lines) - 1 if selected_lines else 0
        truncated = start_index + limit < total_lines
        # Read 返回带行号文本，是为了后续编辑类工具能直接拿这些行号定位。
        content = "".join(
            f"{line_number:4d} | {line}\n"
            for line_number, line in enumerate(selected_lines, start=start_line)
        )

        data: dict[str, Any] = {
            "content": content,
            "truncated": truncated,
        }
        if fallback_encoding is not None:
            data["fallback_encoding"] = fallback_encoding
        output_truncation = maybe_truncate_output_text(
            tool_name="Read",
            full_output=content,
        )
        if output_truncation is not None:
            data["content"] = output_truncation.preview_text
            data["output_truncated"] = True
            data["truncation"] = output_truncation.as_dict()

        response_builder = (
            partial_response
            if truncated or fallback_encoding or output_truncation is not None
            else success_response
        )
        if total_lines == 0:
            text = f"读取了 '{workspace_path.relative_posix}'，文件为空。"
        else:
            text = f"读取了 '{workspace_path.relative_posix}' 的 {len(selected_lines)} 行（第 {start_line}-{end_line} 行）。"
            if truncated:
                text += f" 结果已截断，可用 start_line={end_line + 1} 继续读取。"
        if fallback_encoding:
            text += " 文件解码时使用了 replace 回退。"
        if output_truncation is not None:
            text += build_output_truncation_notice(output_truncation)

        # 读取成功后，把该路径的最新文件快照记到 runtime context，
        # 供同一会话里的 Edit / Write 自动补锁字段。
        if runtime_context is not None:
            runtime_context.remember_read_snapshot(
                workspace_path.relative_posix,
                file_mtime_ms=file_snapshot.mtime_ms,
                file_size_bytes=file_snapshot.size_bytes,
            )

        return response_builder(
            data=data,
            text=text,
            stats=build_stats(
                start_time,
                lines_read=len(selected_lines),
                chars_read=len(content),
                total_lines=total_lines,
                file_size_bytes=file_snapshot.size_bytes,
                file_mtime_ms=file_snapshot.mtime_ms,
                encoding=encoding,
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
        )
    except Exception as exc:
        return error_from_failure(
            ToolFailure(
                code="INTERNAL_ERROR",
                message=str(exc),
                text="读取文件时发生内部错误。",
            ),
            start_time=start_time,
            params_input=params_input,
        )


def _read_file_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    path: str,
    start_line: int = 1,
    limit: int = 500,
) -> ToolResponse:
    # SDK session 负责消息历史，这里的 runtime context 只负责本地文件快照。
    return read_file(
        path=path,
        start_line=start_line,
        limit=limit,
        runtime_context=ctx.context,
    )


ls_tool = function_tool(
    list_files,
    name_override="LS",
    description_override="列出目录或文件条目，用于先看工作区结构。",
)
glob_tool = function_tool(
    glob_search,
    name_override="Glob",
    description_override="按名称或 glob 模式查找文件路径。",
)
grep_tool = function_tool(
    grep_search,
    name_override="Grep",
    description_override="按内容搜索代码，返回文件、行号和匹配文本。",
)
read_tool = function_tool(
    _read_file_tool,
    name_override="Read",
    description_override="读取文本文件并返回带行号内容。",
)

# 先用一个显式列表集中导出，agent_factory 只需要关心“挂哪些工具”，不用关心每个工具细节。
READ_ONLY_TOOLS = [ls_tool, glob_tool, grep_tool, read_tool]
