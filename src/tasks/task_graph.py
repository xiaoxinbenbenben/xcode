from __future__ import annotations

from typing import Any

from src.tasks.task_store import get_task, list_tasks, save_task

VALID_TASK_STATUSES = {
    "pending",
    "running",
    "blocked",
    "completed",
    "cancelled",
    "failed",
}


def _merge_ids(existing: list[int], new_ids: list[int]) -> list[int]:
    merged = {int(item) for item in existing}
    merged.update(int(item) for item in new_ids)
    return sorted(merged)


def _clear_dependency(tasks_dir, completed_task_id: int) -> None:
    # 某个任务完成后，把它从其他任务的 blockedBy 中移除，保持依赖图一致。
    for task in list_tasks(tasks_dir):
        if completed_task_id not in task.get("blockedBy", []):
            continue
        task["blockedBy"] = [
            blocked_id for blocked_id in task["blockedBy"] if blocked_id != completed_task_id
        ]
        save_task(tasks_dir, task)


def update_task(
    *,
    tasks_dir,
    task_id: int,
    status: str | None = None,
    add_blocked_by: list[int] | None = None,
    add_blocks: list[int] | None = None,
    owner: str | None = None,
    result_summary: str | None = None,
    result_artifact: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    task = get_task(tasks_dir, task_id)

    if status is not None:
        if status not in VALID_TASK_STATUSES:
            raise ValueError(f"非法任务状态: {status}")
        task["status"] = status

    if owner is not None:
        task["owner"] = owner
    if result_summary is not None:
        task["result_summary"] = result_summary
    if result_artifact is not None:
        task["result_artifact"] = result_artifact
    if error is not None:
        task["error"] = error

    if add_blocked_by:
        task["blockedBy"] = _merge_ids(task.get("blockedBy", []), add_blocked_by)

    if add_blocks:
        task["blocks"] = _merge_ids(task.get("blocks", []), add_blocks)
        for blocked_task_id in add_blocks:
            blocked_task = get_task(tasks_dir, int(blocked_task_id))
            blocked_task["blockedBy"] = _merge_ids(
                blocked_task.get("blockedBy", []),
                [task_id],
            )
            save_task(tasks_dir, blocked_task)

    task = save_task(tasks_dir, task)
    if status == "completed":
        _clear_dependency(tasks_dir, task_id)
    return task
