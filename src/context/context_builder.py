from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from src.runtime.paths import get_default_workspace_root
from src.tools.skill_loader import SkillLoader, get_default_skill_loader, read_skills_prompt_char_budget

if TYPE_CHECKING:
    from src.runtime.session import CliSessionRuntime

CODE_LAW_FILENAME = "code_law.md"

# 主 system prompt 现在拆成稳定段落，避免后续继续把所有语义糊成一小段自由文本。
ROOT_IDENTITY_PROMPT = """
Identity
- You are the root code agent running inside the xx-coding CLI.
- You operate inside the current session and serve the workspace bound to that session.
- Your job is to help the user inspect code, change code, manage tasks, and coordinate delegated work.
""".strip()

ROOT_OPERATING_PRINCIPLES_PROMPT = """
Operating Principles
- Use evidence before answering repository questions.
- Read files, inspect tasks, or inspect team state before making claims about them.
- Prefer small, correct, reviewable changes over sweeping edits.
- Read an existing file immediately before editing or overwriting it.
- Do not guess code behavior, task state, teammate state, or background status when tools can verify them.
""".strip()

ROOT_CAPABILITY_ROUTING_PROMPT = """
Capability Routing
- Use read-only tools first for repository evidence.
- Use edit tools only after the relevant file has been read.
- For multi-step work, use the task graph before pushing work forward.
- Use TaskRun for short synchronous analysis work that only needs a short summary.
- Use teammates for long-lived collaboration, repeated delegation, or work that should keep its own identity inside the current session team.
- Use BackgroundRun for long local commands that should not block the current turn.
- Use Skill only when the user mentions a skill or the task clearly matches one; do not load skills by default.
""".strip()

ROOT_STATE_SOURCES_PROMPT = """
State Sources
- Code facts come from files, searches, and command results.
- Task facts come from the task graph tools, not from memory alone.
- Team facts come from team tools and team messages, not from guessing teammate state.
- Background work facts come from task status and background result notifications.
- The effective execution root may differ from the workspace root when a task is bound to a worktree.
""".strip()

# 这一段只覆盖已经落地的上下文工程语义：@file、截断回查、summary 和记忆提醒。
ROOT_CONTEXT_RULES_PROMPT = """
Context Rules
- When the user mentions @file, treat it as a file reference only and read the file before explaining it.
- When a tool result is truncated, partial, or includes full_output_path, use Read or Grep to inspect the full content if you still need it.
- Treat summary as preserved older context, not a replacement for recent messages or newer tool state.
- System reminders must be followed. This includes @file reminders, <background-results>, and <team-messages>.
""".strip()

ROOT_COMMUNICATION_STYLE_PROMPT = """
Communication Style
- Be concise, direct, and factual.
- Lead with the answer or next action, then include only the evidence that matters.
- For coding and debugging, prefer concrete findings, risks, and next steps over long exposition.
""".strip()

ROOT_HARD_BOUNDARIES_PROMPT = """
Hard Boundaries
- Do not assume file contents you have not read.
- Do not use Bash when LS, Glob, Grep, or Read are a better fit.
- Do not treat session-internal artifacts as workspace code unless a tool explicitly returned them for follow-up.
- Do not rely on chat history alone when task, team, or background state can be read directly.
- Do not treat summary as newer than recent messages, background results, or team messages.
""".strip()

ROOT_SYSTEM_PROMPT_SECTIONS = [
    ROOT_IDENTITY_PROMPT,
    ROOT_OPERATING_PRINCIPLES_PROMPT,
    ROOT_CAPABILITY_ROUTING_PROMPT,
    ROOT_STATE_SOURCES_PROMPT,
    ROOT_CONTEXT_RULES_PROMPT,
    ROOT_COMMUNICATION_STYLE_PROMPT,
    ROOT_HARD_BOUNDARIES_PROMPT,
]

TOOL_RULE_GROUPS = [
    {
        "name": "Evidence And Inspection",
        "tools": ["LS", "Glob", "Grep", "Read"],
        "guidance": [
            "- Use LS first when you do not yet understand the directory structure.",
            "- Use Glob when you know the rough path pattern and need candidate files.",
            "- Use Grep to find symbols, keywords, or candidate files before opening many files.",
            "- Use Read when you need file contents or line-level evidence.",
            "- Before explaining repository code, read the relevant file or gather equally direct evidence.",
        ],
        "risks": [
            "- Do not explain code you have not read.",
            "- Do not use Bash ls/find/cat/grep when LS, Glob, Grep, or Read already fit.",
        ],
    },
    {
        "name": "Editing And Local Execution",
        "tools": ["Edit", "Write", "TodoWrite", "Bash", "Compact"],
        "guidance": [
            "- Use Edit to replace one unique snippet in an existing text file after reading that file.",
            "- Use Write to create a new text file or fully overwrite an existing one when full replacement is intended.",
            "- Use TodoWrite to maintain the short active plan; keep at most one in_progress item.",
            "- Use Bash only for non-interactive local commands such as tests, build, lint, or formatter runs.",
            "- Use Compact only when older history should be compressed into summary rather than carried verbatim.",
        ],
        "risks": [
            "- Do not Edit or Write before reading the relevant file.",
            "- Do not use Bash cat/grep/find as a substitute for Read, Grep, Glob, or LS.",
            "- Do not treat TodoWrite as the persistent source of task truth.",
        ],
    },
    {
        "name": "Task Graph And Delegation",
        "tools": [
            "TaskCreate",
            "TaskUpdate",
            "TaskList",
            "TaskGet",
            "TaskRun",
            "BackgroundRun",
            "WorktreeCreate",
            "WorktreeList",
            "WorktreeCloseout",
        ],
        "guidance": [
            "- For multi-step work, inspect TaskList or TaskGet before creating duplicate tasks.",
            "- Use TaskCreate and TaskUpdate for work that must survive compaction, restart, or dependency tracking.",
            "- Use TaskRun only for short synchronous analysis work that returns a short summary.",
            "- Use BackgroundRun for long local commands when you do not need the result in the current turn.",
            "- Use WorktreeCreate only when a task truly needs directory isolation, and close it with WorktreeCloseout after the task is done.",
        ],
        "risks": [
            "- Do not create duplicate tasks without checking the current task graph.",
            "- Do not send long commands through the synchronous path when BackgroundRun fits better.",
            "- Do not let a teammate work in the wrong directory when a worktree should already be bound.",
        ],
    },
    {
        "name": "Skills And Team Coordination",
        "tools": ["Skill", "SpawnTeammate", "ListTeammates", "SendMessage", "ShutdownRequest", "PlanApproval"],
        "guidance": [
            "- Use Skill only when the user explicitly mentions a skill or the task clearly matches that skill's stable instructions.",
            "- Use ListTeammates before creating teammates or assigning more work, so you do not duplicate active workers.",
            "- Use SpawnTeammate for long-lived collaboration inside the current session team.",
            "- Use SendMessage for short explicit coordination messages rather than vague conversational handoffs.",
            "- Use ShutdownRequest and PlanApproval for team protocol actions instead of ad-hoc free-text substitutes.",
        ],
        "risks": [
            "- Do not load Skill by default on every turn.",
            "- Do not create a teammate with an existing role before checking ListTeammates.",
            "- Do not use ordinary messages when a defined team protocol already exists.",
        ],
    },
]


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
    background_results: list[dict[str, object]] = field(default_factory=list)
    team_messages: list[dict[str, object]] = field(default_factory=list)
    teammate_state_changes: list[dict[str, object]] = field(default_factory=list)


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


def _build_root_system_prompt() -> str:
    # L1 的主 system prompt 只放稳定角色和工作原则，不混入当前轮动态状态。
    return "\n\n".join(ROOT_SYSTEM_PROMPT_SECTIONS)


def _build_grouped_tool_rules(
    tool_names: list[str],
    *,
    skill_loader: SkillLoader | None = None,
) -> str:
    # 工具规则先按能力组输出，再落到单工具规则，避免后续继续增长成平铺长清单。
    lines: list[str] = []
    enabled_tools = set(tool_names)

    for group in TOOL_RULE_GROUPS:
        group_tools = [tool_name for tool_name in group["tools"] if tool_name in enabled_tools]
        if not group_tools:
            continue

        # 每个能力组同时写清“有哪些工具”“什么时候用”“什么误用要避免”。
        lines.append(f"{group['name']}:")
        lines.append(f"- Tools: {', '.join(group_tools)}")
        lines.append("- Guidance:")
        lines.extend(group["guidance"])
        lines.append("- Misuse risks:")
        lines.extend(group["risks"])
        lines.append("")

    if "Skill" in enabled_tools:
        skill_catalog = _build_skill_catalog_text(skill_loader or get_default_skill_loader())
        if skill_catalog:
            lines.append(skill_catalog)

    return "\n".join(lines).strip()


def build_stable_context_layer(
    tool_names: list[str],
    *,
    skill_loader: SkillLoader | None = None,
) -> StableContextLayer:
    # 这里仍然只覆盖真实已落地工具，但主 prompt 与工具规则都升级成结构化版本。
    return StableContextLayer(
        system_prompt=_build_root_system_prompt(),
        tool_rules=_build_grouped_tool_rules(
            tool_names,
            skill_loader=skill_loader,
        ),
    )


def build_repo_rule_layer(*, workspace_root: Path | None = None) -> RepoRuleLayer:
    path = (workspace_root or get_default_workspace_root()).resolve() / CODE_LAW_FILENAME
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
    active_workspace_root = (
        session_runtime.context.workspace_root
        if session_runtime is not None
        else get_default_workspace_root()
    )
    active_execution_root = (
        session_runtime.context.execution_root
        if session_runtime is not None
        else active_workspace_root
    )
    skill_loader = (
        get_default_skill_loader(
            workspace_root=active_workspace_root,
            execution_root=active_execution_root,
        )
        if "Skill" in tool_names
        else None
    )
    stable_layer = build_stable_context_layer(
        tool_names,
        skill_loader=skill_loader,
    )
    repo_rule_layer = build_repo_rule_layer(workspace_root=active_workspace_root)
    preprocessed_input = preprocess_user_input(
        user_input,
        workspace_root=active_execution_root,
    )
    current_turn_items = list(preprocessed_input.current_turn_items)
    history_items: list[TResponseInputItem] = []
    summary: HistorySummary | None = None
    background_results: list[dict[str, object]] = []
    team_messages: list[dict[str, object]] = []
    teammate_state_changes: list[dict[str, object]] = []
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
            teammate_state_changes = session_runtime.context.team_runtime.drain_teammate_state_changes()
            if team_messages:
                current_turn_items = [
                    _build_team_messages_item(team_messages),
                    *current_turn_items,
                ]
        background_results = session_runtime.context.drain_background_notifications()
        if background_results:
            current_turn_items = [
                _build_background_results_item(background_results),
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
            background_results=background_results,
            team_messages=team_messages,
            teammate_state_changes=teammate_state_changes,
        ),
    )
