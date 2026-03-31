from __future__ import annotations

import asyncio
import argparse
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
from src.runtime.runner import run_streamed
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
    asyncio.run(
        run_streamed(
            user_input,
            config,
            lambda chunk: print(chunk, end="", flush=True),
            session_runtime=session_runtime,
        )
    )
    print()


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
) -> int:
    session = build_prompt_session()
    try:
        session_runtime = build_cli_session_runtime(
            session_id=session_id,
            new_session=new_session,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Session: {session_runtime.session_name} ({session_runtime.session_id})")
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

    config = load_runtime_config()
    if args.list_sessions:
        sessions = list_saved_sessions()
        if not sessions:
            print("No saved sessions.")
            return 0
        for session in sessions:
            print(f"{session.session_id}\t{session.name}\t{session.last_active_at}")
        return 0

    if args.prompt is not None:
        try:
            session_runtime = build_cli_session_runtime(
                session_id=args.session_id,
                new_session=args.new_session,
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            user_input = args.prompt.strip()
            if not user_input:
                print("No input provided.", file=sys.stderr)
                return 1
            try:
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
