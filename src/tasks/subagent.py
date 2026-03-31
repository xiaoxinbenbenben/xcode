from __future__ import annotations

from agents import Agent, Runner

from src.runtime.session import ToolRuntimeContext
from src.tasks.task_graph import update_task
from src.tasks.task_store import get_task
from src.tools.read_only import READ_ONLY_TOOLS
from src.tools.todo_write import TODO_TOOLS

SUBAGENT_TYPE_PROMPTS = {
    "general": "You are a focused coding subagent. Return a concise result summary.",
    "explore": "You explore the repository and return only the key findings and evidence.",
    "summary": "You summarize repository evidence into a short conclusion.",
    "plan": "You produce a short implementation plan grounded in repository evidence.",
}


def _resolve_model(runtime_context: ToolRuntimeContext, model_route: str | None) -> str:
    if model_route == "light":
        return runtime_context.light_model or runtime_context.main_model or runtime_context.current_model or "gpt-5"
    return runtime_context.main_model or runtime_context.current_model or "gpt-5.2-codex"


def _resolve_tools(*, subagent_type: str | None, model_route: str | None):
    # 子代理默认只读；只有 plan + main 才额外拿到 TodoWrite。
    if subagent_type == "plan" and model_route == "main":
        return [*READ_ONLY_TOOLS, *TODO_TOOLS]
    return list(READ_ONLY_TOOLS)


def _build_subagent_instructions(subagent_type: str | None) -> str:
    role_prompt = SUBAGENT_TYPE_PROMPTS.get(subagent_type or "general", SUBAGENT_TYPE_PROMPTS["general"])
    return (
        f"{role_prompt}\n"
        "Use repository evidence before concluding.\n"
        "Do not edit files.\n"
        "Return only a short result summary for the parent agent."
    )


async def run_subagent_task(
    *,
    runtime_context: ToolRuntimeContext,
    task_id: int,
) -> dict[str, object]:
    # TaskRun 的职责是同步跑完一个分析子代理，再把摘要写回任务图。
    task = get_task(runtime_context.tasks_dir, task_id)
    updated_task = update_task(
        tasks_dir=runtime_context.tasks_dir,
        task_id=task_id,
        status="running",
        owner=f"subagent:{task.get('subagent_type') or 'general'}",
        error=None,
    )
    model = _resolve_model(runtime_context, updated_task.get("model_route"))
    tools = _resolve_tools(
        subagent_type=updated_task.get("subagent_type"),
        model_route=updated_task.get("model_route"),
    )
    agent = Agent(
        name=f"task-{task_id}-subagent",
        instructions=_build_subagent_instructions(updated_task.get("subagent_type")),
        model=model,
        tools=tools,
    )
    prompt = updated_task.get("prompt") or updated_task.get("summary") or updated_task.get("title")
    try:
        result = await Runner.run(
            agent,
            input=str(prompt),
            context=runtime_context,
        )
    except Exception as exc:
        failed_task = update_task(
            tasks_dir=runtime_context.tasks_dir,
            task_id=task_id,
            status="failed",
            error=str(exc),
        )
        raise RuntimeError(f"子代理任务失败: task_{task_id}") from exc

    final_output = result.final_output if isinstance(result.final_output, str) else str(result.final_output)
    completed_task = update_task(
        tasks_dir=runtime_context.tasks_dir,
        task_id=task_id,
        status="completed",
        result_summary=final_output,
        error=None,
    )
    return {
        "task": completed_task,
        "subagent_type": completed_task.get("subagent_type"),
        "model_route": completed_task.get("model_route"),
        "result_summary": final_output,
        "tool_names": [tool.name for tool in tools],
        "model": model,
    }
