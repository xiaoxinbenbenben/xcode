from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from src.runtime.paths import display_path
from src.runtime.session import ToolRuntimeContext
from src.tasks.task_graph import update_task
from src.tasks.task_store import create_task, list_tasks


def _background_output_path(runtime_context: ToolRuntimeContext, task_id: int) -> Path:
    background_dir = runtime_context.session_dir / "background"
    background_dir.mkdir(parents=True, exist_ok=True)
    return background_dir / f"task_{task_id}.log"


def _display_artifact_path(runtime_context: ToolRuntimeContext, path: Path) -> str:
    # 会话目录可能在项目外的临时根目录里，路径显示优先相对 session_root，再回退绝对路径。
    return display_path(
        path,
        runtime_context.session_dir,
        runtime_context.session_root,
        runtime_context.workspace_root,
    )


def _summarize_background_result(*, command: str, exit_code: int) -> str:
    if exit_code == 0:
        return f"{command} completed successfully."
    return f"{command} failed with exit code {exit_code}."


def _execute(runtime_context: ToolRuntimeContext, task_id: int, command: str) -> None:
    # 后台线程里允许阻塞执行命令，但结果必须回写任务图并发通知给主线程。
    result = subprocess.run(
        command,
        shell=True,
        cwd=runtime_context.execution_root.resolve(),
        capture_output=True,
        text=True,
    )
    output_path = _background_output_path(runtime_context, task_id)
    output_path.write_text(
        (
            f"$ {command}\n"
            f"exit_code: {result.returncode}\n\n"
            f"[stdout]\n{result.stdout}\n"
            f"[stderr]\n{result.stderr}\n"
        ),
        encoding="utf-8",
    )

    status = "completed" if result.returncode == 0 else "failed"
    summary = _summarize_background_result(command=command, exit_code=result.returncode)
    task = update_task(
        tasks_dir=runtime_context.tasks_dir,
        task_id=task_id,
        status=status,
        result_summary=summary,
        result_artifact=_display_artifact_path(runtime_context, output_path),
        error=None if status == "completed" else summary,
    )
    runtime_context.enqueue_background_notification(
        task_id=task_id,
        text=f"task_{task['id']} {task['status']}: {task['result_summary']}",
    )


def start_background_command(
    *,
    runtime_context: ToolRuntimeContext,
    command: str,
    title: str | None = None,
) -> dict[str, Any]:
    # background task 先登记任务，再立即返回，不阻塞当前 agent 回合。
    task = create_task(
        tasks_dir=runtime_context.tasks_dir,
        title=(title or command).strip()[:60] or "后台命令",
        summary=command,
        kind="background_command",
        prompt=command,
        owner="background",
        status="running",
    )
    thread = threading.Thread(
        target=_execute,
        args=(runtime_context, int(task["id"]), command),
        daemon=True,
    )
    thread.start()
    return {
        "task_id": task["id"],
        "status": task["status"],
    }


def drain_notifications(runtime_context: ToolRuntimeContext) -> list[dict[str, object]]:
    return runtime_context.drain_background_notifications()


def mark_interrupted_running_tasks(*, tasks_dir: Path) -> int:
    # 进程退出后守护线程会消失，所以恢复 session 时要把旧 running 任务改成失败。
    updated_count = 0
    for task in list_tasks(tasks_dir):
        if task.get("status") != "running":
            continue
        update_task(
            tasks_dir=tasks_dir,
            task_id=int(task["id"]),
            status="failed",
            error="process exited before background command completed",
        )
        updated_count += 1
    return updated_count
