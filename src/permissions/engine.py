from __future__ import annotations

import shlex
from collections.abc import Iterable
from fnmatch import fnmatch
from typing import Any

from src.permissions.model import (
    ApprovalCallback,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
    PermissionRule,
    PermissionScope,
)


READ_ONLY_DEFAULT_ALLOW_TOOLS = {
    "LS",
    "Glob",
    "Grep",
    "Read",
    "TaskList",
    "TaskGet",
    "WorktreeList",
    "ListTeammates",
    "Idle",
}
ASK_BY_DEFAULT_TOOLS = {
    "Bash",
    "BackgroundRun",
    "Edit",
    "Write",
    "WorktreeCreate",
    "WorktreeCloseout",
}
PRIVILEGED_COMMAND_WORDS = {
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
SCOPE_PRIORITY: dict[PermissionScope, int] = {
    "global": 0,
    "project": 1,
    "session": 2,
}


def _coerce_decision(value: PermissionDecision | str) -> PermissionDecision:
    if isinstance(value, PermissionDecision):
        return value
    return PermissionDecision(value)


def _shell_words(command: str) -> list[str]:
    # 这里只提取命令词用于权限匹配；失败时返回空列表，让主体工具继续报参数错误。
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    words: list[str] = []
    expecting_command = True
    for token in tokens:
        if token in {";", "&", "&&", "||", "|", "(", ")"}:
            expecting_command = True
            continue
        if not expecting_command:
            continue
        words.append(token)
        expecting_command = False
    return words


def _field_values(request: PermissionRequest, field: str) -> list[str]:
    # command_word 是 Bash/BackgroundRun 的专用便利字段，避免规则作者手写脆弱 glob。
    if field == "tool_name":
        return [request.tool_name]
    if field == "command_word":
        command = request.params_input.get("command")
        return _shell_words(command) if isinstance(command, str) else []
    value = request.params_input.get(field)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _rule_matches(rule: PermissionRule, request: PermissionRequest) -> bool:
    if rule.tool_name not in {"*", request.tool_name} and not fnmatch(request.tool_name, rule.tool_name):
        return False
    if rule.field == "*":
        return True
    return any(fnmatch(value, rule.pattern) for value in _field_values(request, rule.field))


def _best_matching_rule(
    request: PermissionRequest,
    rules: Iterable[PermissionRule],
) -> PermissionRule | None:
    # scope 越靠近当前 session 优先级越高；同一 scope 内后出现的规则覆盖先出现的规则。
    best_rule: PermissionRule | None = None
    best_score = -1
    for index, rule in enumerate(rules):
        if not _rule_matches(rule, request):
            continue
        score = SCOPE_PRIORITY[rule.scope] * 10_000 + index
        if score >= best_score:
            best_rule = rule
            best_score = score
    return best_rule


def _hard_deny(request: PermissionRequest) -> PermissionResult | None:
    if request.tool_name not in {"Bash", "BackgroundRun"}:
        return None

    command = request.params_input.get("command")
    if not isinstance(command, str):
        return None
    # hard deny 只放不可被审批绕过的底线；普通风险命令留给规则和 ask 流程处理。
    normalized_command = " ".join(command.strip().split()).lower()
    if normalized_command in {"rm -rf /", "rm -rf /*"}:
        return PermissionResult(
            decision=PermissionDecision.DENY,
            code="COMMAND_DENIED",
            reason="命令被拒绝：不允许删除系统根目录。",
            source="hard_deny",
        )

    for word in _shell_words(command):
        if word.lower() in PRIVILEGED_COMMAND_WORDS:
            return PermissionResult(
                decision=PermissionDecision.DENY,
                code="COMMAND_DENIED",
                reason=f"命令被拒绝：'{word}' 属于提权或系统级破坏命令。",
                source="hard_deny",
            )
    return None


def _default_decision_for_tool(tool_name: str) -> PermissionDecision:
    if tool_name in READ_ONLY_DEFAULT_ALLOW_TOOLS:
        return PermissionDecision.ALLOW
    if tool_name in ASK_BY_DEFAULT_TOOLS:
        return PermissionDecision.ASK
    return PermissionDecision.ALLOW


class PermissionEngine:
    def __init__(
        self,
        *,
        rules: Iterable[PermissionRule] = (),
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        # rules 先保存在内存里；后续配置文件加载只需要生成同样的 PermissionRule 列表。
        self.rules = list(rules)
        self.approval_callback = approval_callback

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        hard_deny = _hard_deny(request)
        if hard_deny is not None:
            return hard_deny

        rule = _best_matching_rule(request, self.rules)
        if rule is not None:
            decision = _coerce_decision(rule.decision)
            return PermissionResult(
                decision=decision,
                code="COMMAND_DENIED" if decision == PermissionDecision.DENY else "PERMISSION_RULE",
                reason=rule.reason or f"命中 {rule.scope} 权限规则。",
                source=f"rule:{rule.scope}",
                rule=rule,
            )

        decision = _default_decision_for_tool(request.tool_name)
        return PermissionResult(
            decision=decision,
            code="PERMISSION_DEFAULT",
            reason=f"未命中权限规则，使用 {request.tool_name} 的默认策略。",
            source="default",
        )

    def authorize(self, request: PermissionRequest) -> PermissionResult:
        # ask 是唯一会触发用户交互的裁决；没有回调时保守拒绝，避免静默执行高风险工具。
        result = self.evaluate(request)
        if result.decision != PermissionDecision.ASK:
            return result
        if self.approval_callback is None:
            return PermissionResult(
                decision=PermissionDecision.DENY,
                code="COMMAND_DENIED",
                reason="工具调用需要用户审批，但当前运行环境没有审批回调。",
                source="approval_missing",
            )
        if self.approval_callback(request, result):
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                code="USER_APPROVED",
                reason="用户已批准工具调用。",
                source="approval",
            )
        return PermissionResult(
            decision=PermissionDecision.DENY,
            code="COMMAND_DENIED",
            reason="用户拒绝了工具调用。",
            source="approval",
        )
