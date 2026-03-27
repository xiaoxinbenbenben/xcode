from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from time import perf_counter
from typing import Any

from src.protocol import ToolResponse, error_response

# 这层只放所有本地工具共享的基础能力：
# 工作区边界、统一上下文、统一错误封装、最小统计信息。

# 当前阶段所有工具都固定以项目根目录为边界，不支持独立 working_dir。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def resolve_workspace_path(path: str) -> WorkspacePath:
    raw_path = path or "."
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate

    # 统一在这里做一次真实路径归一化和工作区边界校验，避免每个工具各写一套。
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(PROJECT_ROOT)
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


def normalize_posix(path: Path) -> str:
    # 返回给 agent 的路径统一用相对项目根目录的 POSIX 字符串，避免平台差异污染协议。
    relative = path.relative_to(PROJECT_ROOT)
    return "." if str(relative) == "." else relative.as_posix()


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
