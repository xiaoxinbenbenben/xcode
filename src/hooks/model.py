from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """定义当前 agent cycle 支持的 hook 事件名。"""

    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"


@dataclass(slots=True)
class HookContext:
    """承载 hook 执行时需要读取的运行上下文。"""

    runtime_context: Any | None = None
    user_input: str | None = None
    model: str | None = None
    tool_name: str | None = None
    params_input: dict[str, Any] | None = None
    tool_result: Any | None = None
    final_output: str | None = None
    usage: dict[str, int] | None = None
    status: str | None = None
    stage: str | None = None
    message: str | None = None
    error_type: str | None = None


@dataclass(slots=True)
class HookResult:
    """表达 hook 是否要短路后续流程以及对应响应。"""

    stop: bool = False
    response: Any | None = None


HookCallable = Callable[[HookContext], HookResult | None]
