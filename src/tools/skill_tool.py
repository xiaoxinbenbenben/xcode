from __future__ import annotations

from agents import RunContextWrapper, function_tool

from src.protocol import ToolResponse, success_response
from src.runtime.session import ToolRuntimeContext
from src.tools.common import (
    ToolFailure,
    build_context,
    build_stats,
    error_from_failure,
    run_traced_tool,
    start_timer,
)
from src.tools.skill_loader import SkillLoader, get_default_skill_loader


def load_skill_content(
    *,
    name: str,
    args: str = "",
    loader: SkillLoader | None = None,
) -> ToolResponse:
    """按名称加载 skill，并把展开后的正文返回给主代理。"""
    start_time = start_timer()
    params_input = {"name": name, "args": args}
    active_loader = loader or get_default_skill_loader()

    try:
        normalized_name = name.strip()
        if not normalized_name:
            raise ToolFailure(
                code="INVALID_PARAM",
                message="name 参数不能为空。",
                text="参数错误：必须提供 skill 名称。",
            )

        skill = active_loader.render_skill(normalized_name, args)
        if skill is None:
            raise ToolFailure(
                code="NOT_FOUND",
                message=f"未找到 skill '{normalized_name}'。",
                text=f"未找到 skill '{normalized_name}'。",
            )

        # base_dir 前缀是 skill 能引用本目录约定和附属资源的最小上下文锚点。
        content = f"Base directory for this skill: {skill.base_dir}\n\n{skill.body}".strip()

        return success_response(
            data={
                "name": skill.name,
                "description": skill.description,
                "path": skill.path,
                "base_dir": skill.base_dir,
                "content": content,
            },
            text=f"已加载 skill {skill.name}。",
            stats=build_stats(start_time),
            context=build_context(
                params_input=params_input,
                path_resolved=skill.path,
            ),
        )
    except ToolFailure as failure:
        return error_from_failure(
            failure,
            start_time=start_time,
            params_input=params_input,
        )


def _skill_tool(
    ctx: RunContextWrapper[ToolRuntimeContext],
    name: str,
    args: str = "",
) -> ToolResponse:
    # Skill 只是一个普通工具：按需把技能正文取回来，不反向控制主架构。
    params_input = {"name": name, "args": args}
    return run_traced_tool(
        ctx.context,
        tool_name="Skill",
        params_input=params_input,
        invoke=lambda: load_skill_content(
            name=name,
            args=args,
        ),
    )


skill_tool = function_tool(
    _skill_tool,
    name_override="Skill",
    description_override="按名称加载项目内的一个 skill，并返回展开后的技能说明。",
)

SKILL_TOOLS = [skill_tool]
