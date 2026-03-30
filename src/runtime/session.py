from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from agents import SQLiteSession

from src.context.compaction import HistorySummary
from src.runtime.tracing import LocalTraceLogger, build_trace_logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 当前阶段的本地快照表只服务于安全编辑链路，
# 现在再加上 Todo 状态，但仍不承担摘要、裁剪或跨进程恢复等更重的治理职责。
DEFAULT_MAX_READ_SNAPSHOTS = 256
DEFAULT_TODO_PERSIST_DIR = PROJECT_ROOT / "artifacts" / "todos"


@dataclass(slots=True)
class ReadSnapshot:
    # 这里只保留最小乐观锁字段，不把完整文件内容塞进 runtime 内存。
    file_mtime_ms: int
    file_size_bytes: int


@dataclass(slots=True)
class TodoStateItem:
    # Todo 当前阶段只保留任务内容和状态，不引入 id 或 patch 元信息。
    content: str
    status: str


@dataclass(slots=True)
class TodoState:
    # 这份状态用于后续上下文工程复用：当前摘要、完整列表和简短 recap。
    summary: str
    todos: list[TodoStateItem]
    recap: str


@dataclass(slots=True)
class ToolRuntimeContext:
    # 这张表按“规范化路径 -> 最近一次成功 Read 的快照”保存。
    # 同一路径再次读取时只覆盖自己，不会把别的文件快照挤掉。
    session_id: str = "detached-session"
    session: SQLiteSession | None = None
    current_model: str | None = None
    read_snapshots: OrderedDict[str, ReadSnapshot] = field(default_factory=OrderedDict)
    max_read_snapshots: int = DEFAULT_MAX_READ_SNAPSHOTS
    todo_state: TodoState | None = None
    todo_persist_dir: Path = field(default_factory=lambda: DEFAULT_TODO_PERSIST_DIR)
    todo_archive_path: Path | None = None
    todo_completed_block_count: int = 0
    last_persisted_todo_fingerprint: str | None = None
    history_summary: HistorySummary | None = None
    history_compaction_archive_path: str | None = None
    trace_logger: LocalTraceLogger | None = None
    active_trace_run_id: str | None = None

    def remember_read_snapshot(
        self,
        path: str,
        *,
        file_mtime_ms: int,
        file_size_bytes: int,
    ) -> None:
        # 先按路径去重，再把最新快照移到末尾，形成一个最小 LRU。
        if path in self.read_snapshots:
            self.read_snapshots.pop(path)
        self.read_snapshots[path] = ReadSnapshot(
            file_mtime_ms=file_mtime_ms,
            file_size_bytes=file_size_bytes,
        )

        while len(self.read_snapshots) > self.max_read_snapshots:
            self.read_snapshots.popitem(last=False)

    def get_read_snapshot(self, path: str) -> ReadSnapshot | None:
        snapshot = self.read_snapshots.get(path)
        if snapshot is None:
            return None

        # 命中过的路径也移动到末尾，避免热点文件被过早淘汰。
        self.read_snapshots.move_to_end(path)
        return snapshot

    def set_todo_state(self, summary: str, todos: list[dict[str, str]], recap: str) -> None:
        # 这里保存的是“当前 todo 视图”，后续可直接用于 prompt 注入或 UI 展示。
        self.todo_state = TodoState(
            summary=summary,
            todos=[
                TodoStateItem(content=item["content"], status=item["status"])
                for item in todos
            ],
            recap=recap,
        )

    def clear_todo_persist_fingerprint(self) -> None:
        # 只要重新进入未完成态，就允许下一次完整完成时再次归档。
        self.last_persisted_todo_fingerprint = None

    def get_todo_archive_path(self) -> Path:
        if self.todo_archive_path is None:
            self.todo_archive_path = self.todo_persist_dir / f"todo-session-{self.session_id}.md"
        return self.todo_archive_path

    def mark_todo_persisted(self, fingerprint: str, archive_path: Path) -> None:
        self.todo_archive_path = archive_path
        self.last_persisted_todo_fingerprint = fingerprint
        self.todo_completed_block_count += 1

    def remember_history_summary(
        self,
        summary: HistorySummary,
        *,
        archive_path: str | None,
    ) -> None:
        # 这份结构化 summary 供后续 L3 上下文直接复用，不再重复从文本里反解析。
        self.history_summary = summary
        self.history_compaction_archive_path = archive_path

    def start_trace_run(self, *, user_input: str, model: str) -> str | None:
        if self.trace_logger is None:
            self.active_trace_run_id = None
            return None
        self.active_trace_run_id = self.trace_logger.start_run(
            user_input=user_input,
            model=model,
        )
        return self.active_trace_run_id

    def log_trace_context_build(self, payload: dict[str, object]) -> None:
        # context_build 只记录“这一轮送给模型前的关键治理信息”，不把整段上下文全文重写进 trace。
        if self.trace_logger is None or self.active_trace_run_id is None:
            return
        self.trace_logger.log_context_build(
            run_id=self.active_trace_run_id,
            payload=payload,
        )

    def log_trace_tool_call(self, *, tool_name: str, args: dict[str, object]) -> None:
        if self.trace_logger is None or self.active_trace_run_id is None:
            return
        self.trace_logger.log_tool_call(
            run_id=self.active_trace_run_id,
            tool_name=tool_name,
            args=args,
        )

    def log_trace_tool_result(self, *, tool_name: str, result: dict[str, object]) -> None:
        if self.trace_logger is None or self.active_trace_run_id is None:
            return
        self.trace_logger.log_tool_result(
            run_id=self.active_trace_run_id,
            tool_name=tool_name,
            result=result,
        )
        if result.get("status") == "error":
            self.trace_logger.log_error(
                run_id=self.active_trace_run_id,
                stage="tool_execution",
                message=str(result.get("text", "工具执行失败。")),
                tool=tool_name,
            )

    def log_trace_error(self, *, stage: str, message: str, **payload: object) -> None:
        if self.trace_logger is None:
            return
        self.trace_logger.log_error(
            run_id=self.active_trace_run_id,
            stage=stage,
            message=message,
            **payload,
        )

    def finish_trace_run(
        self,
        *,
        final_output: str,
        usage: dict[str, int] | None,
        status: str = "success",
    ) -> None:
        if self.trace_logger is None or self.active_trace_run_id is None:
            self.active_trace_run_id = None
            return
        self.trace_logger.log_finish(
            run_id=self.active_trace_run_id,
            final_output=final_output,
            usage=usage,
        )
        self.trace_logger.log_run_end(
            run_id=self.active_trace_run_id,
            status=status,
            usage=usage,
        )
        self.active_trace_run_id = None

    def close_trace_session(self) -> None:
        if self.trace_logger is None:
            return
        self.trace_logger.log_session_summary()


@dataclass(slots=True)
class CliSessionRuntime:
    # SDK session 负责对话历史，本地 context 负责工具侧的轻量状态。
    session_id: str
    session: SQLiteSession
    context: ToolRuntimeContext

    def close(self) -> None:
        self.context.close_trace_session()
        self.session.close()


def build_cli_session_runtime() -> CliSessionRuntime:
    # 这一步先只保证“一次 CLI 启动里的多轮记忆”。
    # 因此 session id 每次启动都新建，不做恢复旧会话。
    session_id = f"cli-{uuid4().hex}"
    session = SQLiteSession(session_id=session_id, db_path=":memory:")
    context = ToolRuntimeContext(
        session_id=session_id,
        session=session,
        trace_logger=build_trace_logger(session_id),
    )
    return CliSessionRuntime(
        session_id=session_id,
        session=session,
        context=context,
    )
