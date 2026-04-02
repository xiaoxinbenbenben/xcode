from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def ensure_tasks_dir(tasks_dir: Path) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)


def get_task_path(tasks_dir: Path, task_id: int) -> Path:
    return tasks_dir / f"task_{task_id}.json"


def get_next_task_id(tasks_dir: Path) -> int:
    ensure_tasks_dir(tasks_dir)
    ids: list[int] = []
    for path in tasks_dir.glob("task_*.json"):
        try:
            ids.append(int(path.stem.split("_", maxsplit=1)[1]))
        except (IndexError, ValueError):
            continue
    return max(ids, default=0) + 1


def save_task(tasks_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    ensure_tasks_dir(tasks_dir)
    task_path = get_task_path(tasks_dir, int(task["id"]))
    task["updated_at"] = _utc_now()
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return task


def get_task(tasks_dir: Path, task_id: int) -> dict[str, Any]:
    task_path = get_task_path(tasks_dir, task_id)
    if not task_path.exists():
        raise FileNotFoundError(f"task_{task_id}.json 不存在。")
    return json.loads(task_path.read_text(encoding="utf-8"))


def list_tasks(tasks_dir: Path) -> list[dict[str, Any]]:
    ensure_tasks_dir(tasks_dir)
    tasks = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(tasks_dir.glob("task_*.json"))
    ]
    return sorted(tasks, key=lambda item: int(item["id"]))


def create_task(
    *,
    tasks_dir: Path,
    title: str,
    summary: str,
    kind: str,
    prompt: str | None = None,
    subagent_type: str | None = None,
    model_route: str | None = None,
    require_worktree: bool = False,
    owner: str = "main_agent",
    status: str = "pending",
) -> dict[str, Any]:
    # 任务源数据直接落盘，避免后续被 session 压缩影响。
    ensure_tasks_dir(tasks_dir)
    now = _utc_now()
    task = {
        "id": get_next_task_id(tasks_dir),
        "title": title.strip(),
        "status": status,
        "owner": owner,
        "owner_agent_id": None,
        "kind": kind,
        "summary": summary.strip(),
        "prompt": (prompt or "").strip(),
        "subagent_type": subagent_type,
        "model_route": model_route,
        "require_worktree": require_worktree,
        "blockedBy": [],
        "blocks": [],
        "lease_expires_at": None,
        "worktree_name": None,
        "worktree_path": None,
        "result_summary": None,
        "result_artifact": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    return save_task(tasks_dir, task)
