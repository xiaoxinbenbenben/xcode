from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path

from openai import APIConnectionError
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = PROJECT_ROOT / "artifacts" / "prompt_history.txt"

# 保持 `python scripts/cli.py ...` 这种最直接入口可用，不要求先安装成包。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.config import load_runtime_config
from src.runtime.config import RuntimeConfig
from src.runtime.runner import run_events
from src.runtime.session import (
    CliSessionRuntime,
    build_cli_session_runtime,
    list_saved_sessions,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal CLI wrapper around an OpenAI Agents SDK agent."
    )
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--session",
        dest="session_id",
        help="Restore a specific session id.",
    )
    session_group.add_argument(
        "--new-session",
        action="store_true",
        help="Create a new session instead of restoring the latest one.",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List saved sessions and exit.",
    )
    parser.add_argument(
        "--workspace",
        dest="workspace_root",
        help="Bind a new session to a specific workspace root.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Emit runtime events as JSON lines for one-shot prompt mode.",
    )
    parser.add_argument(
        "--print-session-json",
        action="store_true",
        help="Print current session metadata as JSON and exit.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="User request. If omitted, the CLI will open an interactive prompt.",
    )
    return parser.parse_args(argv)


def build_prompt_session() -> PromptSession[str]:
    """终端交互留在 CLI 层，runtime 只关心模型运行。"""
    keys = KeyBindings()

    @keys.add("enter")
    def handle_enter(event) -> None:
        event.current_buffer.validate_and_handle()

    # 很多终端对 Shift+Enter 支持不稳定，这里显式约定 Esc+Enter 换行。
    @keys.add("escape", "enter")
    def handle_escape_enter(event) -> None:
        event.current_buffer.insert_text("\n")

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        multiline=True,
        history=FileHistory(str(HISTORY_PATH)),
        key_bindings=keys,
    )


def stream_reply(
    user_input: str,
    config: RuntimeConfig,
    session_runtime: CliSessionRuntime,
) -> None:
    session_runtime.update_name_from_user_input(user_input)
    print("Agent>")
    asyncio.run(_stream_reply_events(user_input, config, session_runtime))
    print()


async def _stream_reply_events(
    user_input: str,
    config: RuntimeConfig,
    session_runtime: CliSessionRuntime,
) -> None:
    # CLI 这一层现在只负责消费 runtime events，再决定哪些事件真正渲染给用户。
    printed_text = False
    async for event in run_events(
        user_input,
        config,
        session_runtime=session_runtime,
    ):
        printed_text = render_runtime_event(
            event,
            write=lambda text: print(text, end="", flush=True),
            printed_text=printed_text,
        )


def build_session_descriptor(session_runtime: CliSessionRuntime) -> dict[str, str]:
    # TUI 第一版只需要会话 id、名称和 workspace。
    return {
        "session_id": session_runtime.session_id,
        "session_name": session_runtime.session_name,
        "workspace_root": str(session_runtime.context.workspace_root),
    }


async def emit_runtime_events_json(
    user_input: str,
    config: RuntimeConfig,
    session_runtime: CliSessionRuntime,
    *,
    write,
) -> None:
    # JSONL 模式只暴露结构化事件，不混入当前 CLI 的人类可读渲染。
    async for event in run_events(
        user_input,
        config,
        session_runtime=session_runtime,
    ):
        write(json.dumps(event, ensure_ascii=False) + "\n")


def render_runtime_event(
    event: dict[str, object],
    *,
    write,
    printed_text: bool = False,
) -> bool:
    # assistant 文本继续走主输出区，tool_result 默认只显示摘要，不把完整结果直接刷屏。
    event_type = event.get("type")
    payload = event.get("payload")

    if event_type == "assistant_text_delta" and isinstance(payload, dict):
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            write(delta)
            return True

    if event_type == "tool_started" and isinstance(payload, dict):
        tool_name = str(payload.get("tool_name") or "unknown_tool")
        summary = str(payload.get("summary") or tool_name)
        if printed_text:
            write("\n")
        write(f"[Tool] {summary}\n")
        return False

    if event_type == "tool_result" and isinstance(payload, dict):
        tool_name = str(payload.get("tool_name") or "unknown_tool")
        status = str(payload.get("status") or "success")
        summary = str(payload.get("summary") or "工具执行完成。")
        full_output_path = payload.get("full_output_path")
        suffix = ""
        if full_output_path:
            suffix = f" | 回查: {full_output_path}"
        if printed_text:
            write("\n")
        write(f"[ToolResult] {tool_name} [{status}] {summary}{suffix}\n")
        return False

    if event_type == "background_result_arrived" and isinstance(payload, dict):
        text = str(payload.get("text") or "后台任务已完成。")
        if printed_text:
            write("\n")
        write(f"[Background] {text}\n")
        return False

    if event_type == "team_message_arrived" and isinstance(payload, dict):
        from_name = str(payload.get("from") or "unknown")
        to_name = str(payload.get("to") or "unknown")
        message_type = str(payload.get("type") or "message")
        summary = str(payload.get("summary") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        request_status = str(payload.get("request_status") or "").strip()
        suffix_parts = []
        if summary:
            suffix_parts.append(summary)
        if request_id:
            suffix_parts.append(f"request_id={request_id}")
        if request_status:
            suffix_parts.append(f"status={request_status}")
        suffix = f" | {' | '.join(suffix_parts)}" if suffix_parts else ""
        if printed_text:
            write("\n")
        write(f"[TeamMessage] {from_name} -> {to_name} ({message_type}){suffix}\n")
        return False

    if event_type == "teammate_state_changed" and isinstance(payload, dict):
        name = str(payload.get("name") or "unknown")
        previous_status = str(payload.get("previous_status") or "unknown")
        status = str(payload.get("status") or "unknown")
        if printed_text:
            write("\n")
        write(f"[Teammate] {name}: {previous_status} -> {status}\n")
        return False

    return printed_text


def print_connection_error() -> None:
    # 连接失败属于 CLI 边界要兜住的外部错误，这里先转成可读提示，不在此处实现重试。
    print(
        "模型连接失败，请检查网络、OPENAI_BASE_URL 或上游服务状态后重试。",
        file=sys.stderr,
    )


def run_repl(
    config: RuntimeConfig,
    *,
    session_id: str | None = None,
    new_session: bool = False,
    workspace_root: Path | None = None,
) -> int:
    session = build_prompt_session()
    try:
        session_runtime = build_cli_session_runtime(
            session_id=session_id,
            new_session=new_session,
            workspace_root=workspace_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Session: {session_runtime.session_name} ({session_runtime.session_id})")
    print(f"Workspace: {session_runtime.context.workspace_root}")
    print("Enter 发送 | Esc Enter 换行 | Ctrl-D / Ctrl-C 退出")

    try:
        while True:
            try:
                user_input = session.prompt(
                    "You> ",
                    prompt_continuation=lambda width, *_: "... ".rjust(width),
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not user_input:
                continue
            if user_input in {"exit", "quit", "/exit"}:
                return 0

            try:
                # 同一次 REPL 循环始终复用同一个 session runtime，才能保住多轮记忆。
                stream_reply(user_input, config, session_runtime)
            except APIConnectionError:
                print_connection_error()
            except KeyboardInterrupt:
                print()
                return 0
    finally:
        session_runtime.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace_root = Path(args.workspace_root).expanduser() if args.workspace_root else None

    config = load_runtime_config()
    if args.json_events and args.prompt is None:
        print("`--json-events` 只能和单次 prompt 一起使用。", file=sys.stderr)
        return 2
    if args.list_sessions:
        sessions = list_saved_sessions()
        if not sessions:
            print("No saved sessions.")
            return 0
        for session in sessions:
            print(
                f"{session.session_id}\t{session.name}\t"
                f"{session.workspace_root}\t{session.last_active_at}"
            )
        return 0

    if args.print_session_json:
        try:
            session_runtime = build_cli_session_runtime(
                session_id=args.session_id,
                new_session=args.new_session,
                workspace_root=workspace_root,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            print(json.dumps(build_session_descriptor(session_runtime), ensure_ascii=False))
            return 0
        finally:
            session_runtime.close()

    if args.prompt is not None:
        try:
            session_runtime = build_cli_session_runtime(
                session_id=args.session_id,
                new_session=args.new_session,
                workspace_root=workspace_root,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            user_input = args.prompt.strip()
            if not user_input:
                print("No input provided.", file=sys.stderr)
                return 1
            try:
                if args.json_events:
                    session_runtime.update_name_from_user_input(user_input)
                    asyncio.run(
                        emit_runtime_events_json(
                            user_input,
                            config,
                            session_runtime,
                            write=lambda text: print(text, end="", flush=True),
                        )
                    )
                else:
                    stream_reply(user_input, config, session_runtime)
            except APIConnectionError:
                print_connection_error()
                return 1
            return 0
        finally:
            session_runtime.close()

    return run_repl(
        config,
        session_id=args.session_id,
        new_session=args.new_session,
        workspace_root=workspace_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
