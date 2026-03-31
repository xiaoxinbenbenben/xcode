from agents import Agent

from src.context import build_repo_rule_layer, build_stable_context_layer
from src.tools.registry import AGENT_TOOLS

def _build_default_instructions() -> str:
    # 这里复用 context builder 的 L1/L2 拼装，避免稳定提示词再次散落回 runtime。
    stable_layer = build_stable_context_layer([tool.name for tool in AGENT_TOOLS])
    repo_rule_layer = build_repo_rule_layer()
    sections = [
        "<system-prompt>",
        stable_layer.system_prompt,
        "</system-prompt>",
        "",
        "<tool-rules>",
        stable_layer.tool_rules,
        "</tool-rules>",
    ]
    if repo_rule_layer.content:
        sections.extend(
            [
                "",
                "<repository-rules>",
                f"Code Law ({repo_rule_layer.path.name if repo_rule_layer.path else 'code_law.md'}):",
                repo_rule_layer.content,
                "</repository-rules>",
            ]
        )
    return "\n".join(sections).strip()


def build_root_agent(*, model: str, instructions: str | None = None) -> Agent:
    """创建 CLI 当前使用的唯一顶层 Agent。"""
    return Agent(
        name="xx-coding",
        instructions=instructions or _build_default_instructions(),
        model=model,
        tools=AGENT_TOOLS,
    )
