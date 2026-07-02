from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


PermissionScope = Literal["global", "project", "session"]


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(slots=True, frozen=True)
class PermissionRule:
    # 一条规则只表达一个字段匹配和一个裁决，避免第一版就引入复杂 DSL。
    tool_name: str
    field: str
    pattern: str
    decision: PermissionDecision
    scope: PermissionScope = "project"
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class PermissionRequest:
    # request 是工具执行前的申请单；后续 CLI/TUI 都只需要读这一份摘要。
    tool_name: str
    params_input: dict[str, Any]
    actor_name: str = "team-lead"


@dataclass(slots=True, frozen=True)
class PermissionResult:
    # code 用于最终映射到 ToolResponse.error.code；source 方便 trace/debug。
    decision: PermissionDecision
    code: str
    reason: str
    source: str
    rule: PermissionRule | None = None


ApprovalCallback = Callable[[PermissionRequest, PermissionResult], bool]

