from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents import TResponseInputItem

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_MENTIONS = 5
FILE_MENTION_PATTERN = re.compile(r"(?<![A-Za-z0-9])@([a-zA-Z0-9/._-]+(?:\.[a-zA-Z0-9]+)?)")


@dataclass(frozen=True, slots=True)
class FileMentionPreprocessResult:
    # 预处理器只负责“提醒”和“噪声控制”，不负责真正读取文件内容。
    current_turn_items: list[TResponseInputItem]
    mentioned_files: list[str]


def _resolve_existing_workspace_file(raw_path: str) -> str | None:
    # 当前只接受项目内相对路径，并且只提醒真实存在的文件。
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None

    resolved = (PROJECT_ROOT / candidate).resolve(strict=False)
    try:
        relative = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return relative.as_posix()


def extract_file_mentions(user_input: str, *, max_mentions: int = MAX_FILE_MENTIONS) -> tuple[list[str], int]:
    # 这里按出现顺序去重，再在最后统一做数量上限，避免重复路径反复污染 reminder。
    seen: set[str] = set()
    ordered_mentions: list[str] = []
    for match in FILE_MENTION_PATTERN.finditer(user_input):
        resolved = _resolve_existing_workspace_file(match.group(1))
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        ordered_mentions.append(resolved)

    kept_mentions = ordered_mentions[:max_mentions]
    omitted_count = max(0, len(ordered_mentions) - len(kept_mentions))
    return kept_mentions, omitted_count


def build_file_mention_reminder(mentioned_files: list[str], *, omitted_count: int = 0) -> str:
    # reminder 只保留最小动作信息：用户提到了哪些文件、回答前要先 Read、不要假设内容。
    mention_text = ", ".join(f"@{path}" for path in mentioned_files)
    lines = [
        "<system-reminder>",
        f"The user mentioned these files: {mention_text}.",
        "Read the relevant files with the Read tool before answering.",
        "Do not assume file contents you have not read.",
    ]
    if omitted_count > 0:
        lines.append(f"(and {omitted_count} more)")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def preprocess_user_input(user_input: str) -> FileMentionPreprocessResult:
    # 用户原文保持不变；系统只是在前面额外插入一个最小 reminder item。
    mentioned_files, omitted_count = extract_file_mentions(user_input)
    current_turn_items: list[TResponseInputItem] = []
    if mentioned_files:
        current_turn_items.append(
            {
                "role": "system",
                "content": build_file_mention_reminder(
                    mentioned_files,
                    omitted_count=omitted_count,
                ),
            }
        )
    current_turn_items.append({"role": "user", "content": user_input})
    return FileMentionPreprocessResult(
        current_turn_items=current_turn_items,
        mentioned_files=mentioned_files,
    )
