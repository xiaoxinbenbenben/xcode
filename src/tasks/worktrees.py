from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.runtime.session import ToolRuntimeContext
from src.tasks.task_store import get_task, list_tasks, save_task
from src.tools.common import ToolFailure


def _worktrees_dir(runtime_context: ToolRuntimeContext) -> Path:
    return runtime_context.session_dir / "worktrees"


def _task_worktree_name(task_id: int) -> str:
    return f"task-{task_id}"


def _task_worktree_path(runtime_context: ToolRuntimeContext, task_id: int) -> Path:
    return _worktrees_dir(runtime_context) / f"task_{task_id}"


def _run_git_worktree_command(*, args: list[str], cwd: Path) -> None:
    # phase 4 直接复用 git worktree，不额外包一层抽象调度器。
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ToolFailure(
            code="EXECUTION_ERROR",
            message=completed.stderr.strip() or "git worktree 命令执行失败。",
            text="创建或清理 worktree 时失败，请检查当前仓库状态。",
        )


def ensure_task_worktree(
    *,
    runtime_context: ToolRuntimeContext,
    task_id: int,
) -> dict[str, Any]:
    # 一个任务只绑定一个固定 worktree；如果已存在，就直接复用。
    task = get_task(runtime_context.tasks_dir, task_id)
    worktree_path = _task_worktree_path(runtime_context, task_id)
    worktree_name = _task_worktree_name(task_id)

    if task.get("worktree_path"):
        return task

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git_worktree_command(
        args=["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
        cwd=runtime_context.workspace_root,
    )
    worktree_path.mkdir(parents=True, exist_ok=True)
    resolved_worktree_path = worktree_path.resolve()
    task["worktree_name"] = worktree_name
    task["worktree_path"] = str(resolved_worktree_path)
    return save_task(runtime_context.tasks_dir, task)


def list_worktrees(*, runtime_context: ToolRuntimeContext) -> list[dict[str, Any]]:
    # 先直接从任务图反推 worktree 视图，避免 phase 4 再引入第二份 registry。
    worktrees: list[dict[str, Any]] = []
    for task in list_tasks(runtime_context.tasks_dir):
        worktree_path = task.get("worktree_path")
        if not worktree_path:
            continue
        worktrees.append(
            {
                "task_id": task["id"],
                "task_title": task["title"],
                "worktree_name": task.get("worktree_name"),
                "worktree_path": worktree_path,
                "exists": Path(worktree_path).exists(),
            }
        )
    return worktrees


def closeout_task_worktree(
    *,
    runtime_context: ToolRuntimeContext,
    task_id: int,
    action: str,
) -> dict[str, Any]:
    # closeout 只保留 keep/remove 两种决策，不在 phase 4 引入更复杂的审批状态。
    if action not in {"keep", "remove"}:
        raise ToolFailure(
            code="INVALID_PARAM",
            message=f"非法 closeout 动作: {action}",
            text="参数错误：closeout 只支持 keep 或 remove。",
        )

    task = get_task(runtime_context.tasks_dir, task_id)
    worktree_path = str(task.get("worktree_path") or "").strip()
    if not worktree_path:
        raise ToolFailure(
            code="NOT_FOUND",
            message=f"task_{task_id} 当前没有绑定 worktree。",
            text=f"task_{task_id} 当前没有可 closeout 的 worktree。",
        )

    if action == "remove":
        if Path(worktree_path).exists():
            _run_git_worktree_command(
                args=["git", "worktree", "remove", "--force", worktree_path],
                cwd=runtime_context.workspace_root,
            )
        task["worktree_name"] = None
        task["worktree_path"] = None
        updated = save_task(runtime_context.tasks_dir, task)
        if runtime_context.team_runtime is not None:
            runtime_context.team_runtime.clear_worktree_binding(worktree_path=worktree_path)
        return updated

    return task
