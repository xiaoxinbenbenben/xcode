from __future__ import annotations

from pathlib import Path

# 这是 agent 自己代码仓库的稳定根目录，只用于源码和内置资源。
AGENT_CODE_ROOT = Path(__file__).resolve().parents[2]


def get_default_workspace_root() -> Path:
    # 默认工作区跟随 CLI 启动时的当前目录，而不是回退到 agent 仓库根。
    return Path.cwd().resolve()


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
