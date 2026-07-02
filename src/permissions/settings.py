from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from src.permissions.engine import PermissionEngine
from src.permissions.model import ApprovalCallback, PermissionDecision, PermissionRule, PermissionScope
from src.runtime.paths import DEFAULT_APP_HOME_DIRNAME, get_app_home_dir


DEFAULT_SETTINGS_FILENAME = "settings.json"


def get_global_settings_path() -> Path:
    """获取global settings path，供 权限配置 流程复用。"""
    return get_app_home_dir() / DEFAULT_SETTINGS_FILENAME


def get_project_settings_path(*, workspace_root: Path) -> Path:
    """获取project settings path，供 权限配置 流程复用。"""
    return workspace_root.resolve() / DEFAULT_APP_HOME_DIRNAME / DEFAULT_SETTINGS_FILENAME


def _load_rules_from_settings(path: Path, *, scope: PermissionScope) -> list[PermissionRule]:
    """加载rules from settings，供 权限配置 流程复用。"""
    if not path.exists():
        return []

    # settings 先只读取 permissions.rules；其它配置块留给各自模块独立解析。
    raw = json.loads(path.read_text(encoding="utf-8"))
    permissions = raw.get("permissions") or {}
    rules = permissions.get("rules") or []
    loaded_rules: list[PermissionRule] = []
    for item in rules:
        loaded_rules.append(
            PermissionRule(
                tool_name=str(item["tool_name"]),
                field=str(item["field"]),
                pattern=str(item["pattern"]),
                decision=PermissionDecision(str(item["decision"])),
                scope=scope,
                reason=str(item["reason"]) if item.get("reason") is not None else None,
            )
        )
    return loaded_rules


def load_permission_rules(
    *,
    global_settings_path: Path | None = None,
    project_settings_path: Path | None = None,
    workspace_root: Path | None = None,
) -> list[PermissionRule]:
    # 规则顺序保持 global -> project；engine 会按 scope 处理覆盖关系。
    """加载permission rules，供 权限配置 流程复用。"""
    active_global_path = global_settings_path or get_global_settings_path()
    if project_settings_path is None:
        if workspace_root is None:
            active_project_path = None
        else:
            active_project_path = get_project_settings_path(workspace_root=workspace_root)
    else:
        active_project_path = project_settings_path

    rules = _load_rules_from_settings(active_global_path, scope="global")
    if active_project_path is not None:
        rules.extend(_load_rules_from_settings(active_project_path, scope="project"))
    return rules


def build_permission_engine(
    *,
    workspace_root: Path,
    approval_callback: ApprovalCallback | None = None,
    session_rules: Iterable[PermissionRule] = (),
) -> PermissionEngine:
    # session rules 由运行时临时注入，天然排在 global/project 之后。
    """构建permission engine，供 权限配置 流程复用。"""
    rules = [
        *load_permission_rules(workspace_root=workspace_root),
        *session_rules,
    ]
    return PermissionEngine(
        rules=rules,
        approval_callback=approval_callback,
    )

