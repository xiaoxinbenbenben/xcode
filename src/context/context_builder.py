from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from agents import TResponseInputItem

from src.context.compaction import (
    HistorySummary,
    SummaryGenerator,
    get_context_compaction_config,
    prepare_history_for_model,
)
from src.context.file_mentions import preprocess_user_input
from src.tools.skill_loader import SkillLoader, get_default_skill_loader, read_skills_prompt_char_budget

if TYPE_CHECKING:
    from src.runtime.session import CliSessionRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODE_LAW_PATH = PROJECT_ROOT / "code_law.md"
MINIMAL_SYSTEM_PROMPT = """
You are a local code assistant running inside the xx-coding CLI.

Use evidence before answering questions about repository code.
Prefer small, correct, reviewable changes.
Do not assume file contents you have not read.
When modifying an existing file, read it immediately before editing or overwriting it.
""".strip()
TOOL_RULE_TEXT = {
    "LS": "Use LS to inspect directories or file entries.",
    "Glob": "Use Glob to find candidate files by path pattern.",
    "Grep": "Use Grep to search code content and symbols.",
    "Read": "Use Read to inspect file contents with line numbers.",
    "Edit": "Use Edit to replace one unique snippet in an existing text file.",
    "Write": "Use Write to create a new text file or overwrite an existing one.",
    "TodoWrite": "Use TodoWrite to manage multi-step coding tasks; always submit the full list and keep at most one in_progress item.",
    "Bash": "Use Bash only for non-interactive local commands such as tests or build commands; do not use it when LS / Glob / Grep / Read are a better fit.",
    "Compact": "Use Compact when the session is getting too long and you need the system to archive older history into an L3 summary.",
    "TaskCreate": "Use TaskCreate to create a persistent task node for work that must survive compaction or restart.",
    "TaskUpdate": "Use TaskUpdate to change task status, dependencies, owner or result fields.",
    "TaskList": "Use TaskList to inspect the current session task graph at a glance.",
    "TaskGet": "Use TaskGet to inspect one task in detail.",
    "TaskRun": "Use TaskRun to delegate an analysis task to a subagent and get back only a short summary.",
    "BackgroundRun": "Use BackgroundRun for long local commands that should keep running without blocking the current turn.",
    "WorktreeCreate": "Use WorktreeCreate to bind an isolated git worktree to a task before delegated implementation work starts.",
    "WorktreeList": "Use WorktreeList to inspect which tasks currently have bound worktrees.",
    "WorktreeCloseout": "Use WorktreeCloseout after a task is done to keep or remove its bound worktree.",
    "Skill": "Use Skill to load an in-project skill by name when the user mentions it or the task clearly matches that skill.",
    "SpawnTeammate": "Use SpawnTeammate to create a long-lived teammate inside the current session team.",
    "ListTeammates": "Use ListTeammates to inspect current teammate status before assigning more work.",
    "SendMessage": "Use SendMessage to send a short explicit message to team-lead or a teammate.",
    "ShutdownRequest": "Use ShutdownRequest to ask a teammate to stop through the phase-2 request/response protocol.",
    "PlanApproval": "Use PlanApproval to submit a plan for lead review or respond to a teammate plan review request.",
}


@dataclass(frozen=True, slots=True)
class StableContextLayer:
    # L1 只放稳定前缀：主 system prompt 和只覆盖已实现工具的最小规则。
    system_prompt: str
    tool_rules: str


@dataclass(frozen=True, slots=True)
class RepoRuleLayer:
    # L2 是仓库本地规则文档；当前阶段固定读取项目根目录的 code_law.md。
    path: Path | None
    content: str


@dataclass(frozen=True, slots=True)
class RuntimeContextLayer:
    # L3 分开保存“已有历史”和“本轮输入”，避免在 builder 内部把它们混成一大段字符串。
    history_items: list[TResponseInputItem]
    current_turn_items: list[TResponseInputItem]
    mentioned_files: list[str]
    summary: HistorySummary | None
    compaction: dict[str, str | int | bool | None]


@dataclass(frozen=True, slots=True)
class ContextBundle:
    stable: StableContextLayer
    repo_rule: RepoRuleLayer
    runtime: RuntimeContextLayer

    def build_agent_instructions(self) -> str:
        # SDK 最终只接受一份 instructions 字符串，但在适配边界之前仍保留分层结构。
        sections = [
            "<system-prompt>",
            self.stable.system_prompt,
            "</system-prompt>",
            "",
            "<tool-rules>",
            self.stable.tool_rules,
            "</tool-rules>",
        ]
        if self.repo_rule.content:
            sections.extend(
                [
                    "",
                    "<repository-rules>",
                    f"Code Law ({self.repo_rule.path.name if self.repo_rule.path else 'code_law.md'}):",
                    self.repo_rule.content,
                    "</repository-rules>",
                ]
            )
        return "\n".join(sections).strip()

    def build_runner_input(self) -> list[TResponseInputItem]:
        # session 已经负责保存历史，所以每轮只把“当前输入项”交给 Runner。
        return list(self.runtime.current_turn_items)


def _build_skill_catalog_text(loader: SkillLoader) -> str:
    # L1 只放 skills 简表，不把正文长期塞进稳定层。
    skills = loader.list_skills()
    if not skills:
        return ""

    budget = read_skills_prompt_char_budget()
    lines = ["- Available skills:"]
    used_chars = sum(len(line) for line in lines)

    for index, skill in enumerate(skills):
        line = f"  - {skill.name}: {skill.description}"
        projected = used_chars + len(line) + 1
        if projected > budget:
            remaining = len(skills) - index
            lines.append(f"  - ... and {remaining} more skills")
            break
        lines.append(line)
        used_chars = projected

    return "\n".join(lines)


def build_stable_context_layer(tool_names: list[str]) -> StableContextLayer:
    # 只给真实已落地工具生成规则，不提前为未来能力编规则。
    tool_rule_lines = [
        f"- {TOOL_RULE_TEXT[tool_name]}"
        for tool_name in tool_names
        if tool_name in TOOL_RULE_TEXT
    ]
    if "Skill" in tool_names:
        skill_catalog = _build_skill_catalog_text(get_default_skill_loader())
        if skill_catalog:
            tool_rule_lines.append(skill_catalog)
    return StableContextLayer(
        system_prompt=MINIMAL_SYSTEM_PROMPT,
        tool_rules="\n".join(tool_rule_lines),
    )


def build_repo_rule_layer(path: Path = CODE_LAW_PATH) -> RepoRuleLayer:
    if not path.exists():
        return RepoRuleLayer(path=None, content="")
    return RepoRuleLayer(
        path=path,
        content=path.read_text(encoding="utf-8").strip(),
    )


def _build_background_results_item(notifications: list[dict[str, object]]) -> TResponseInputItem:
    # 后台结果只回注入简短摘要，完整日志仍留在任务图或产物文件里回查。
    lines = ["<background-results>"]
    lines.extend(f"- {item['text']}" for item in notifications)
    lines.append("</background-results>")
    return {
        "role": "system",
        "content": "\n".join(lines),
    }


def _build_team_messages_item(messages: list[dict[str, object]]) -> TResponseInputItem:
    # team-lead 只需要看到 teammate 发来的简短消息，不直接注入完整 teammate transcript。
    lines = ["<team-messages>"]
    for item in messages:
        summary = str(item.get("summary") or "").strip()
        content = str(item.get("content") or "").strip()
        header = f"- from {item['from']} to {item['to']} ({item['type']})"
        request_id = str(item.get("request_id") or "").strip()
        request_status = str(item.get("request_status") or "").strip()
        if summary:
            header += f": {summary}"
        if request_id:
            header += f" [request_id={request_id}]"
        if request_status:
            header += f" [status={request_status}]"
        lines.append(header)
        if content:
            lines.append(content)
    lines.append("</team-messages>")
    return {
        "role": "system",
        "content": "\n".join(lines),
    }


async def build_context_bundle(
    *,
    user_input: str,
    session_runtime: CliSessionRuntime | None,
    tool_names: list[str],
    model_name: str,
    summary_generator: SummaryGenerator | None = None,
) -> ContextBundle:
    # 这里统一组装当前轮真正送给模型的 L1/L2/L3。
    # AgentTeam、background result、compaction 都在这一层汇合。
    stable_layer = build_stable_context_layer(tool_names)
    repo_rule_layer = build_repo_rule_layer()
    preprocessed_input = preprocess_user_input(user_input)
    current_turn_items = list(preprocessed_input.current_turn_items)
    history_items: list[TResponseInputItem] = []
    summary: HistorySummary | None = None
    compaction: dict[str, str | int | bool | None] = {
        "token_estimator": "tiktoken",
        "estimated_tokens": 0,
        "micro_compacted": False,
        "tool_result_count": 0,
        "replaced_tool_results": 0,
        "auto_compacted": False,
        "archive_path": None,
    }
    if session_runtime is not None:
        # 先注入 teammate 发给 lead 的消息，再注入 background 结果。
        # 两者都属于“上一轮之外发生的系统事件”，不直接改写原始 session history。
        if session_runtime.context.team_runtime is not None:
            team_messages = session_runtime.context.team_runtime.drain_lead_messages()
            if team_messages:
                current_turn_items = [
                    _build_team_messages_item(team_messages),
                    *current_turn_items,
                ]
        notifications = session_runtime.context.drain_background_notifications()
        if notifications:
            current_turn_items = [
                _build_background_results_item(notifications),
                *current_turn_items,
            ]
        # compaction 仍然只治理普通历史项，不碰 team/task 这些外部持久化状态。
        compaction_config = replace(
            get_context_compaction_config(),
            archive_dir=session_runtime.context.compaction_dir,
        )
        prepared_history = await prepare_history_for_model(
            session=session_runtime.session,
            session_id=session_runtime.session_id,
            model=model_name,
            stable_text=stable_layer.system_prompt + "\n" + stable_layer.tool_rules,
            repo_rule_text=repo_rule_layer.content,
            current_turn_items=current_turn_items,
            existing_summary=session_runtime.context.history_summary,
            summary_generator=summary_generator,
            config=compaction_config,
        )
        history_items = prepared_history.history_items
        summary = prepared_history.summary
        compaction = prepared_history.compaction
        if summary is not None:
            session_runtime.context.remember_history_summary(
                summary,
                archive_path=(
                    str(compaction["archive_path"])
                    if compaction["archive_path"] is not None
                    else session_runtime.context.history_compaction_archive_path
                ),
            )

    return ContextBundle(
        stable=stable_layer,
        repo_rule=repo_rule_layer,
        runtime=RuntimeContextLayer(
            history_items=history_items,
            current_turn_items=current_turn_items,
            mentioned_files=preprocessed_input.mentioned_files,
            summary=summary,
            compaction=compaction,
        ),
    )
