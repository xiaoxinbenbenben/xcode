from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, partial_response, success_response
from src.runtime.session import ToolRuntimeContext
from src.tools.common import (
    ToolFailure,
    build_output_preview,
    build_output_truncation_notice,
    build_context,
    build_stats,
    ensure_exists,
    error_from_failure,
    maybe_truncate_output_text,
    resolve_workspace_path,
    run_traced_tool,
    start_timer,
)

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
BLOCKED_INTERACTIVE_COMMANDS = {
    "vim",
    "vi",
    "nano",
    "less",
    "more",
    "top",
    "htop",
    "watch",
    "tmux",
    "screen",
}
BLOCKED_NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "sftp", "ftp"}
BLOCKED_PRIVILEGED_COMMANDS = {
    "sudo",
    "su",
    "doas",
    "mkfs",
    "fdisk",
    "dd",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
}
BLOCKED_READ_ONLY_COMMANDS = {
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "find",
    "rg",
}
SHELL_COMMAND_WRAPPERS = {"command", "builtin", "env", "time", "noglob"}
SHELL_SEPARATORS = {";", "&", "&&", "||", "|", "(", ")"}
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _extract_command_words(command: str) -> list[str]:
    # 这里只做“最小可解释”的 shell 词法扫描：
    # 识别每个命令段的实际命令词，用于阻止明显不该放进 Bash 的命令。
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError as exc:
        raise ToolFailure(
            code="INVALID_PARAM",
            message="command 不是可解析的 shell 命令。",
            text="参数错误：command 不是有效的 shell 命令字符串。",
        ) from exc

    command_words: list[str] = []
    expecting_command = True
    for token in tokens:
        if token in SHELL_SEPARATORS:
            expecting_command = True
            continue
        if not expecting_command:
            continue
        if ENV_ASSIGNMENT_RE.fullmatch(token):
            continue
        if token in SHELL_COMMAND_WRAPPERS:
            continue
        command_words.append(token)
        expecting_command = False
    return command_words


def _validate_command(command: str) -> str:
    if not isinstance(command, str) or not command.strip():
        raise ToolFailure(
            code="INVALID_PARAM",
            message="command 参数非法。",
            text="参数错误：command 不能为空。",
        )

    normalized_command = command.strip()
    normalized_space_command = " ".join(normalized_command.split()).lower()
    if normalized_space_command in {"rm -rf /", "rm -rf /*"}:
        raise ToolFailure(
            code="COMMAND_BLOCKED",
            message="命令被安全规则阻止。",
            text="命令被阻止：不允许执行明显破坏性的删除命令。",
        )

    for word in _extract_command_words(normalized_command):
        lower_word = word.lower()
        # 这一版不支持在 command 里再切目录，避免工作区边界和工具参数语义变得不透明。
        if lower_word == "cd":
            raise ToolFailure(
                code="COMMAND_BLOCKED",
                message="命令被安全规则阻止。",
                text="命令被阻止：请使用 directory 参数指定工作目录，不要在 command 中使用 cd。",
            )
        if lower_word in BLOCKED_INTERACTIVE_COMMANDS:
            raise ToolFailure(
                code="COMMAND_BLOCKED",
                message="命令被安全规则阻止。",
                text="命令被阻止：当前 Bash 工具不支持交互式命令。",
            )
        if lower_word in BLOCKED_NETWORK_COMMANDS:
            raise ToolFailure(
                code="COMMAND_BLOCKED",
                message="命令被安全规则阻止。",
                text="命令被阻止：当前 Bash 工具不允许执行网络命令。",
            )
        if lower_word in BLOCKED_PRIVILEGED_COMMANDS:
            raise ToolFailure(
                code="COMMAND_BLOCKED",
                message="命令被安全规则阻止。",
                text="命令被阻止：当前 Bash 工具不允许执行提权或系统级破坏命令。",
            )
        if lower_word in BLOCKED_READ_ONLY_COMMANDS:
            raise ToolFailure(
                code="COMMAND_BLOCKED",
                message="命令被安全规则阻止。",
                text="命令被阻止：列目录、找文件、搜内容和读文件请优先使用 LS / Glob / Grep / Read。",
            )

    return normalized_command


def _validate_timeout(timeout_ms: int) -> int:
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise ToolFailure(
            code="INVALID_PARAM",
            message="timeout_ms 参数非法。",
            text="参数错误：timeout_ms 必须是整数。",
        )
    if not 1 <= timeout_ms <= MAX_TIMEOUT_MS:
        raise ToolFailure(
            code="INVALID_PARAM",
            message="timeout_ms 超出允许范围。",
            text=f"参数错误：timeout_ms 必须在 1 到 {MAX_TIMEOUT_MS} 之间。",
        )
    return timeout_ms


def _resolve_directory(directory: str, *, runtime_context: ToolRuntimeContext | None):
    workspace_root = runtime_context.execution_root if runtime_context is not None else None
    workspace_path = resolve_workspace_path(directory, workspace_root=workspace_root)
    ensure_exists(workspace_path)
    if not workspace_path.resolved.is_dir():
        raise ToolFailure(
            code="INVALID_PARAM",
            message=f"路径 '{workspace_path.relative_posix}' 不是目录。",
            text="参数错误：directory 必须指向工作区内的目录。",
        )
    return workspace_path


def _build_text(
    *,
    command: str,
    exit_code: int | None,
    time_ms: int,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> str:
    headline = "命令执行成功" if exit_code == 0 and not timed_out else "命令执行未完全成功"
    exit_text = "N/A" if exit_code is None else str(exit_code)
    lines = [f"{headline}：{command}", f"(Exit code {exit_text}. Took {time_ms}ms)"]
    if timed_out:
        lines.append("命令执行超时，以下是已捕获的部分输出。")
    if stdout:
        lines.extend(["", "--- STDOUT ---", stdout])
    if stderr:
        lines.extend(["", "--- STDERR ---", stderr])
    return "\n".join(lines)


def _apply_bash_output_truncation(
    *,
    data: dict[str, Any],
    text: str,
    runtime_context: ToolRuntimeContext | None,
    workspace_root: Path | None,
) -> tuple[dict[str, Any], str, bool]:
    # Bash 最容易产生超长 stdout/stderr，所以在最终封装前统一过一层共享截断机制。
    output_truncation = maybe_truncate_output_text(
        tool_name="Bash",
        full_output=text,
        runtime_context=runtime_context,
        workspace_root=workspace_root,
    )
    if output_truncation is None:
        return data, text, False

    truncated_data = dict(data)
    truncated_data["stdout"] = build_output_preview(
        str(data.get("stdout", "")),
        max_lines=output_truncation.max_lines,
        max_bytes=output_truncation.max_bytes,
    )
    truncated_data["stderr"] = build_output_preview(
        str(data.get("stderr", "")),
        max_lines=output_truncation.max_lines,
        max_bytes=output_truncation.max_bytes,
    )
    truncated_data["output_truncated"] = True
    truncated_data["truncation"] = output_truncation.as_dict()

    # text 只保留统一预览和回查提示，避免 Bash 把整段完整输出重新塞回上下文。
    truncated_text = output_truncation.preview_text + build_output_truncation_notice(output_truncation)
    return truncated_data, truncated_text, True


def run_bash(
    command: str,
    directory: str = ".",
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    runtime_context: ToolRuntimeContext | None = None,
) -> ToolResponse:
    """在项目工作区内执行一个最小非交互 shell 命令。"""
    start_time = start_timer()
    params_input = {
        "command": command,
        "directory": directory,
        "timeout_ms": timeout_ms,
    }

    try:
        normalized_command = _validate_command(command)
        validated_timeout = _validate_timeout(timeout_ms)
        workspace_directory = _resolve_directory(directory, runtime_context=runtime_context)
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
            cwd=".",
            directory_resolved=directory if directory else ".",
        )

    env = dict(os.environ)
    env["MYCODEAGENT"] = "1"

    try:
        # 这里显式用 `/bin/bash -lc`，让工具语义稳定在“单次非交互 bash 命令”上。
        completed = subprocess.run(
            ["/bin/bash", "-lc", normalized_command],
            cwd=workspace_directory.resolved,
            env=env,
            capture_output=True,
            timeout=validated_timeout / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _normalize_output(exc.stdout)
        stderr = _normalize_output(exc.stderr)
        if not stdout and not stderr:
            return error_from_failure(
                ToolFailure(
                    code="TIMEOUT",
                    message="命令执行超时。",
                    text="命令执行超时，且没有产生可复用输出。",
                ),
                start_time=start_time,
                params_input=params_input,
                cwd=workspace_directory.relative_posix,
                directory_resolved=workspace_directory.relative_posix,
                data={
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "command": normalized_command,
                    "directory": workspace_directory.relative_posix,
                    "timed_out": True,
                    "truncated": False,
                },
            )

        stats = build_stats(
            start_time,
            stdout_bytes=len(stdout.encode("utf-8")),
            stderr_bytes=len(stderr.encode("utf-8")),
        )
        data = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": None,
            "command": normalized_command,
            "directory": workspace_directory.relative_posix,
            "timed_out": True,
            "truncated": False,
        }
        text = _build_text(
            command=normalized_command,
            exit_code=None,
            time_ms=int(stats["time_ms"]),
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        )
        data, text, _output_truncated = _apply_bash_output_truncation(
            data=data,
            text=text,
            runtime_context=runtime_context,
            workspace_root=workspace_directory.resolved,
        )
        return partial_response(
            data=data,
            text=text,
            stats=stats,
            context=build_context(
                params_input=params_input,
                cwd=workspace_directory.relative_posix,
                directory_resolved=workspace_directory.relative_posix,
            ),
        )
    except OSError as exc:
        return error_from_failure(
            ToolFailure(
                code="EXECUTION_ERROR",
                message=f"命令执行异常：{exc}",
                text="命令执行失败：本地 shell 进程无法正常启动。",
            ),
            start_time=start_time,
            params_input=params_input,
            cwd=workspace_directory.relative_posix,
            directory_resolved=workspace_directory.relative_posix,
        )

    stdout = _normalize_output(completed.stdout)
    stderr = _normalize_output(completed.stderr)
    stats = build_stats(
        start_time,
        stdout_bytes=len(stdout.encode("utf-8")),
        stderr_bytes=len(stderr.encode("utf-8")),
    )
    data = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": completed.returncode,
        "command": normalized_command,
        "directory": workspace_directory.relative_posix,
        "timed_out": False,
        "truncated": False,
    }
    text = _build_text(
        command=normalized_command,
        exit_code=completed.returncode,
        time_ms=int(stats["time_ms"]),
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )
    data, text, output_truncated = _apply_bash_output_truncation(
        data=data,
        text=text,
        runtime_context=runtime_context,
        workspace_root=workspace_directory.resolved,
    )
    response_builder = (
        success_response
        if completed.returncode == 0 and not output_truncated
        else partial_response
    )
    return response_builder(
        data=data,
        text=text,
        stats=stats,
        context=build_context(
            params_input=params_input,
            cwd=workspace_directory.relative_posix,
            directory_resolved=workspace_directory.relative_posix,
        ),
    )


def _bash_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    command: str,
    directory: str = ".",
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> ToolResponse:
    # Bash 主体仍然保持最小执行语义；wrapper 额外负责 tool tracing。
    params_input = {
        "command": command,
        "directory": directory,
        "timeout_ms": timeout_ms,
    }
    return run_traced_tool(
        ctx.context,
        tool_name="Bash",
        params_input=params_input,
        invoke=lambda: run_bash(
            command=command,
            directory=directory,
            timeout_ms=timeout_ms,
            runtime_context=ctx.context,
        ),
    )


bash_tool = function_tool(
    _bash_tool,
    name_override="Bash",
    description_override="在当前项目工作区内执行一个非交互本地命令。优先用于 pytest、git status、构建和脚本命令，不要用于 ls/cat/grep/find/rg。",
)

BASH_TOOLS = [bash_tool]
