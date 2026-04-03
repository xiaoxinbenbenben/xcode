from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

# 这是 agent 自己代码仓库的稳定根目录，只用于源码和内置资源。
AGENT_CODE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_HOME_DIRNAME = ".xx-coding"
DEFAULT_MEMORY_INDEX_FILENAME = "MEMORY.md"
_PROJECT_KEY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def get_default_workspace_root() -> Path:
    # 默认工作区跟随 CLI 启动时的当前目录，而不是回退到 agent 仓库根。
    return Path.cwd().resolve()


def get_app_home_dir() -> Path:
    # 跨 session 的用户级持久化目录统一收敛到 app home，避免继续借 agent 仓库落内部状态。
    return (Path.home() / DEFAULT_APP_HOME_DIRNAME).resolve()


def _read_git_common_dir(workspace_root: Path) -> Path | None:
    # 这里优先用 git common dir 识别“同一仓库”，这样不同 worktree 会自然收敛到同一个项目标识。
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(workspace_root),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    if not output:
        return None
    return Path(output).resolve()


def get_workspace_project_identity_root(workspace_root: Path | None = None) -> Path:
    # project identity 优先绑定 canonical git root；拿不到 git 身份时才回退到 workspace_root。
    active_workspace_root = (workspace_root or get_default_workspace_root()).resolve()
    common_dir = _read_git_common_dir(active_workspace_root)
    if common_dir is None:
        return active_workspace_root
    if common_dir.name == ".git":
        return common_dir.parent.resolve()
    return common_dir.resolve()


def _sanitize_project_key_segment(value: str) -> str:
    sanitized = _PROJECT_KEY_SANITIZE_RE.sub("-", value.strip()).strip("-").lower()
    return sanitized or "workspace"


def get_workspace_project_key(workspace_root: Path | None = None) -> str:
    # key 既要可落盘，也要在同一项目上稳定，所以这里用“可读 slug + 短 hash”的最小组合。
    identity_root = get_workspace_project_identity_root(workspace_root=workspace_root)
    slug = _sanitize_project_key_segment(identity_root.name)
    digest = hashlib.sha256(str(identity_root).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def get_workspace_memory_dir(workspace_root: Path | None = None) -> Path:
    # 长期记忆是 workspace-scoped 的 L2 持久化目录，不属于 session_root / artifacts。
    project_key = get_workspace_project_key(workspace_root=workspace_root)
    return (get_app_home_dir() / "projects" / project_key / "memory").resolve()


def get_workspace_memory_index_path(workspace_root: Path | None = None) -> Path:
    return get_workspace_memory_dir(workspace_root=workspace_root) / DEFAULT_MEMORY_INDEX_FILENAME


def display_path(path: Path, *bases: Path | None) -> str:
    # 展示路径时优先给出相对当前 session / workspace 的短路径，失败再回退绝对路径。
    resolved_path = path.resolve()
    for base in bases:
        if base is None:
            continue
        try:
            return str(resolved_path.relative_to(base.resolve()))
        except ValueError:
            continue
    return str(resolved_path)
