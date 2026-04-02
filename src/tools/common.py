from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from src.protocol import ToolResponse, error_response

# 这层只放所有本地工具共享的基础能力：
# 工作区边界、统一上下文、统一错误封装、最小统计信息。

# 当前阶段 PROJECT_ROOT 仍然是 agent 自己代码所在的仓库根。
# phase 4 开始，具体工具执行目录会通过 workspace_root / execution_root 覆盖。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOOL_OUTPUT_MAX_LINES = 200
DEFAULT_TOOL_OUTPUT_MAX_BYTES = 12_288
DEFAULT_TOOL_OUTPUT_DIR = "artifacts/tool-output"
DEFAULT_IGNORED_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@dataclass(slots=True)
class WorkspacePath:
    # 一个路径同时保留两种形态：
    # resolved 用于真实访问文件系统，relative_posix 用于返回给 agent。
    resolved: Path
    relative_posix: str


@dataclass(slots=True)
class FileSnapshot:
    # 这是当前阶段最小乐观锁的全部载荷：
    # 用 mtime_ms + size_bytes 表达“我上次读到的文件版本”。
    mtime_ms: int
    size_bytes: int


@dataclass(slots=True)
class ToolOutputLimits:
    # 最小版本先只支持行数和字节两个阈值。
    max_lines: int
    max_bytes: int


@dataclass(slots=True)
class OutputTruncation:
    # 这份结构专门描述“上下文里保留了什么、完整输出被写到了哪里”。
    max_lines: int
    max_bytes: int
    original_lines: int
    original_bytes: int
    kept_lines: int
    kept_bytes: int
    preview_text: str
    full_output_path: str

    def as_dict(self) -> dict[str, int | str]:
        return {
            "max_lines": self.max_lines,
            "max_bytes": self.max_bytes,
            "original_lines": self.original_lines,
            "original_bytes": self.original_bytes,
            "kept_lines": self.kept_lines,
            "kept_bytes": self.kept_bytes,
            "full_output_path": self.full_output_path,
        }


@dataclass(slots=True)
class ToolFailure(Exception):
    # 工具内部先统一抛这个轻量错误，再在最外层转换成协议里的 error 响应。
    code: str
    message: str
    text: str
    data: dict[str, Any] | None = None


def start_timer() -> float:
    return perf_counter()


def elapsed_ms(start_time: float) -> int:
    return max(0, int((perf_counter() - start_time) * 1000))


def build_stats(start_time: float, **extra: int | float | str) -> dict[str, int | float | str]:
    return {"time_ms": elapsed_ms(start_time), **extra}


def build_context(
    *,
    params_input: dict[str, Any],
    cwd: str = ".",
    path_resolved: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    # 大多数工具当前仍固定在项目根目录运行；像 Bash 这类显式支持 directory 的工具可以覆盖 cwd。
    context: dict[str, Any] = {
        "cwd": cwd,
        "params_input": params_input,
    }
    if path_resolved is not None:
        context["path_resolved"] = path_resolved
    context.update(extra)
    return context


def error_from_failure(
    failure: ToolFailure,
    *,
    start_time: float,
    params_input: dict[str, Any],
    cwd: str = ".",
    path_resolved: str | None = None,
    data: dict[str, Any] | None = None,
    **context_extra: Any,
) -> ToolResponse:
    # 所有工具都通过这一层把内部失败映射成统一协议，避免每个工具手拼 error 信封。
    error_data: dict[str, Any] = {}
    if failure.data:
        error_data.update(failure.data)
    if data:
        error_data.update(data)
    return error_response(
        code=failure.code,
        message=failure.message,
        text=failure.text,
        stats=build_stats(start_time),
        context=build_context(
            params_input=params_input,
            cwd=cwd,
            path_resolved=path_resolved,
            **context_extra,
        ),
        data=error_data,
    )


def run_traced_tool(
    runtime_context: Any,
    *,
    tool_name: str,
    params_input: dict[str, Any],
    invoke: Any,
) -> ToolResponse:
    # tracing 只挂在 wrapper 边界：
    # 这样既能统一记录工具事件，又不把日志逻辑散进每个工具主体。
    if runtime_context is not None:
        runtime_context.log_trace_tool_call(
            tool_name=tool_name,
            args=params_input,
        )
    result = invoke()
    if runtime_context is not None:
        runtime_context.log_trace_tool_result(
            tool_name=tool_name,
            result=result,
        )
    return result


async def run_traced_tool_async(
    runtime_context: Any,
    *,
    tool_name: str,
    params_input: dict[str, Any],
    invoke: Any,
) -> ToolResponse:
    # Compact 这类异步工具也走同一套 wrapper tracing 语义，避免同步/异步两套日志格式分叉。
    if runtime_context is not None:
        runtime_context.log_trace_tool_call(
            tool_name=tool_name,
            args=params_input,
        )
    result = await invoke()
    if runtime_context is not None:
        runtime_context.log_trace_tool_result(
            tool_name=tool_name,
            result=result,
        )
    return result


def resolve_workspace_path(path: str, *, workspace_root: Path | None = None) -> WorkspacePath:
    raw_path = path or "."
    active_root = (workspace_root or PROJECT_ROOT).resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = active_root / candidate

    # 统一在这里做一次真实路径归一化和工作区边界校验，避免每个工具各写一套。
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(active_root)
    except ValueError as exc:
        raise ToolFailure(
            code="ACCESS_DENIED",
            message=f"路径 '{raw_path}' 超出项目工作区。",
            text="访问被拒绝：路径必须位于当前项目工作区内。",
        ) from exc

    relative_posix = "." if str(relative) == "." else relative.as_posix()
    return WorkspacePath(resolved=resolved, relative_posix=relative_posix)


def ensure_exists(workspace_path: WorkspacePath) -> None:
    if not workspace_path.resolved.exists():
        raise ToolFailure(
            code="NOT_FOUND",
            message=f"路径 '{workspace_path.relative_posix}' 不存在。",
            text=f"未找到路径 '{workspace_path.relative_posix}'。",
        )


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def matches_ignore_patterns(path_value: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return False
    basename = Path(path_value).name
    return any(
        fnmatch(path_value, pattern) or fnmatch(basename, pattern)
        for pattern in patterns
    )


def should_skip_entry(
    *,
    relative_posix: str,
    include_hidden: bool,
    include_ignored: bool,
    ignore_patterns: list[str] | None = None,
) -> bool:
    # 隐藏项、默认忽略项、调用方自定义 ignore 都在这里统一判定。
    # 这样 LS / Glob / Grep 可以共享同一套过滤语义。
    parts = [part for part in Path(relative_posix).parts if part not in {".", ""}]
    name = Path(relative_posix).name

    if not include_hidden and any(is_hidden_name(part) for part in parts):
        return True
    if not include_ignored and any(part in DEFAULT_IGNORED_NAMES for part in parts):
        return True
    return matches_ignore_patterns(relative_posix, ignore_patterns)


def sort_key_for_entry(path: Path) -> tuple[int, str]:
    # 目录优先能让 agent 更快看清结构，再决定是否继续深入读取。
    is_dir = path.is_dir()
    return (0 if is_dir else 1, path.name.casefold())


def normalize_posix(path: Path, *, workspace_root: Path | None = None) -> str:
    # 返回给 agent 的路径统一用相对当前执行根目录的 POSIX 字符串，避免平台差异污染协议。
    active_root = (workspace_root or PROJECT_ROOT).resolve()
    relative = path.relative_to(active_root)
    return "." if str(relative) == "." else relative.as_posix()


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数。") from exc
    if parsed <= 0:
        raise ValueError(f"{name} 必须是正整数。")
    return parsed


def get_tool_output_limits() -> ToolOutputLimits:
    # 阈值按调用时动态读取，测试和后续配置覆盖都不需要重启进程。
    return ToolOutputLimits(
        max_lines=_read_positive_int_env("TOOL_OUTPUT_MAX_LINES", DEFAULT_TOOL_OUTPUT_MAX_LINES),
        max_bytes=_read_positive_int_env("TOOL_OUTPUT_MAX_BYTES", DEFAULT_TOOL_OUTPUT_MAX_BYTES),
    )


def count_text_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def build_output_preview(text: str, *, max_lines: int, max_bytes: int) -> str:
    # 预览始终走同一套 head 规则，避免每个工具各自决定保留多少内容。
    if not text:
        return ""

    limited_lines = "".join(text.splitlines(keepends=True)[:max_lines])
    encoded = limited_lines.encode("utf-8")
    if len(encoded) <= max_bytes:
        return limited_lines

    # 这里按字节再截一次，确保最终进上下文的预览不会无限增长。
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def get_tool_output_dir() -> WorkspacePath:
    configured_dir = os.environ.get("TOOL_OUTPUT_DIR", DEFAULT_TOOL_OUTPUT_DIR)
    return resolve_workspace_path(configured_dir)


def maybe_truncate_output_text(*, tool_name: str, full_output: str) -> OutputTruncation | None:
    # 这层统一负责“大输出治理”：
    # 判阈值、生成预览、落盘完整输出，并把回查路径返回给工具层。
    if not full_output:
        return None

    limits = get_tool_output_limits()
    original_lines = count_text_lines(full_output)
    original_bytes = len(full_output.encode("utf-8"))
    if original_lines <= limits.max_lines and original_bytes <= limits.max_bytes:
        return None

    preview_text = build_output_preview(
        full_output,
        max_lines=limits.max_lines,
        max_bytes=limits.max_bytes,
    )
    output_dir = get_tool_output_dir()
    output_dir.resolved.mkdir(parents=True, exist_ok=True)

    # 文件名只保留最小可读信息，真正的语义还是由相对路径和工具名共同表达。
    safe_tool_name = re.sub(r"[^A-Za-z0-9_-]+", "-", tool_name).strip("-") or "tool"
    filename = (
        f"tool_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_tool_name}_{uuid4().hex[:8]}.txt"
    )
    output_path = output_dir.resolved / filename
    output_path.write_text(full_output, encoding="utf-8")

    return OutputTruncation(
        max_lines=limits.max_lines,
        max_bytes=limits.max_bytes,
        original_lines=original_lines,
        original_bytes=original_bytes,
        kept_lines=count_text_lines(preview_text),
        kept_bytes=len(preview_text.encode("utf-8")),
        preview_text=preview_text,
        full_output_path=normalize_posix(output_path),
    )


def build_output_truncation_notice(truncation: OutputTruncation) -> str:
    # 提示里直接暴露 Read / Grep 可消费的相对路径，方便模型后续回查完整内容。
    return (
        "\n\n完整输出已写入 "
        f"'{truncation.full_output_path}'，当前只返回预览。"
        " 可用 Read 或 Grep 继续回查完整结果。"
    )


def get_file_snapshot(path: Path) -> FileSnapshot:
    # 用 ns 再转 ms，能比直接读 st_mtime 更稳定，也更接近后续要给模型传递的整数锁值。
    stat_result = path.stat()
    return FileSnapshot(
        mtime_ms=int(stat_result.st_mtime_ns // 1_000_000),
        size_bytes=stat_result.st_size,
    )


def read_workspace_text_file(workspace_path: WorkspacePath) -> tuple[str, str, str | None]:
    # Read / Edit / Write 共用这条文本读取路径，保证二进制检测和解码回退语义一致。
    binary_head = workspace_path.resolved.read_bytes()[:8192]
    if b"\x00" in binary_head:
        raise ToolFailure(
            code="BINARY_FILE",
            message=f"文件 '{workspace_path.relative_posix}' 看起来是二进制文件。",
            text=f"无法按文本读取 '{workspace_path.relative_posix}'，因为它看起来是二进制文件。",
        )

    encoding = "utf-8"
    fallback_encoding: str | None = None
    try:
        content = workspace_path.resolved.read_text(encoding=encoding)
    except UnicodeDecodeError:
        # 读取阶段允许 replace 回退，但写入阶段会根据 fallback 显式拒绝，避免无声损坏原文件。
        content = workspace_path.resolved.read_text(encoding=encoding, errors="replace")
        fallback_encoding = "replace"

    return content, encoding, fallback_encoding


def require_existing_file_lock(
    workspace_path: WorkspacePath,
    *,
    expected_mtime_ms: int | None,
    expected_size_bytes: int | None,
) -> FileSnapshot:
    # 当前没有 session 和隐藏缓存，所以已有文件上的锁值必须显式从 Read 结果里传回来。
    if isinstance(expected_mtime_ms, bool) or isinstance(expected_size_bytes, bool):
        raise ToolFailure(
            code="INVALID_PARAM",
            message="expected_mtime_ms 或 expected_size_bytes 类型非法。",
            text="参数错误：锁字段必须是整数。",
        )
    if not isinstance(expected_mtime_ms, int) or not isinstance(expected_size_bytes, int):
        raise ToolFailure(
            code="INVALID_PARAM",
            message="缺少 expected_mtime_ms 或 expected_size_bytes。",
            text="已有文件必须先用 Read 获取最新的 file_mtime_ms 和 file_size_bytes，再执行写入。",
        )

    try:
        current = get_file_snapshot(workspace_path.resolved)
    except FileNotFoundError as exc:
        raise ToolFailure(
            code="CONFLICT",
            message="File has been removed since it was read.",
            text="文件在读取后已被删除或替换，请先重新读取再重试。",
            data={
                "conflict": {
                    "expected_mtime_ms": expected_mtime_ms,
                    "expected_size_bytes": expected_size_bytes,
                    "current_mtime_ms": None,
                    "current_size_bytes": None,
                }
            },
        ) from exc

    if (
        current.mtime_ms != expected_mtime_ms
        or current.size_bytes != expected_size_bytes
    ):
        raise ToolFailure(
            code="CONFLICT",
            message="File has been modified since it was read.",
            text="文件在读取后已发生变化，请先重新读取再重试。",
            data={
                "conflict": {
                    "expected_mtime_ms": expected_mtime_ms,
                    "expected_size_bytes": expected_size_bytes,
                    "current_mtime_ms": current.mtime_ms,
                    "current_size_bytes": current.size_bytes,
                }
            },
        )

    return current
