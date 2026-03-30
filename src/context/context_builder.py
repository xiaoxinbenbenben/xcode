from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents import TResponseInputItem

from src.context.compaction import HistorySummary, SummaryGenerator, prepare_history_for_model
from src.context.file_mentions import preprocess_user_input

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


def build_stable_context_layer(tool_names: list[str]) -> StableContextLayer:
    # 只给真实已落地工具生成规则，不提前为未来能力编规则。
    tool_rule_lines = [
        f"- {TOOL_RULE_TEXT[tool_name]}"
        for tool_name in tool_names
        if tool_name in TOOL_RULE_TEXT
    ]
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


async def build_context_bundle(
    *,
    user_input: str,
    session_runtime: CliSessionRuntime | None,
    tool_names: list[str],
    model_name: str,
    summary_generator: SummaryGenerator | None = None,
) -> ContextBundle:
    stable_layer = build_stable_context_layer(tool_names)
    repo_rule_layer = build_repo_rule_layer()
    preprocessed_input = preprocess_user_input(user_input)
    current_turn_items = preprocessed_input.current_turn_items
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
        prepared_history = await prepare_history_for_model(
            session=session_runtime.session,
            session_id=session_runtime.session_id,
            model=model_name,
            stable_text=stable_layer.system_prompt + "\n" + stable_layer.tool_rules,
            repo_rule_text=repo_rule_layer.content,
            current_turn_items=current_turn_items,
            existing_summary=session_runtime.context.history_summary,
            summary_generator=summary_generator,
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
