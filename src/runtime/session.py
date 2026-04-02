from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from agents import SQLiteSession

from src.context.compaction import HistorySummary
from src.runtime.tracing import LocalTraceLogger, build_trace_logger

if TYPE_CHECKING:
    from src.tasks.agent_team import AgentTeamRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_ROOT = PROJECT_ROOT / "artifacts" / "sessions"
DEFAULT_MAX_READ_SNAPSHOTS = 256
DEFAULT_TODO_PERSIST_DIR = PROJECT_ROOT / "artifacts" / "todos"
_WHITESPACE_RE = re.compile(r"\s+")
_FILE_MENTION_RE = re.compile(r"@\S+")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _build_default_session_name() -> str:
    # 默认名只承担“先能识别这个会话”的职责，不让 session 一开始就是空标题。
    return f"未命名会话 {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def _build_session_name_from_user_input(user_input: str) -> str | None:
    # 第一条有效用户输入只做一个很薄的标题裁剪，不额外调用模型。
    text = _FILE_MENTION_RE.sub("", user_input).strip()
    if not text:
        return None
    text = _WHITESPACE_RE.sub(" ", text)
    return text[:24].strip() or None


def _default_session_root() -> Path:
    return DEFAULT_SESSION_ROOT


def _session_pointer_path(session_root: Path) -> Path:
    return session_root / "current_session.json"


@dataclass(slots=True)
class SessionMeta:
    session_id: str
    name: str
    workspace_root: str
    created_at: str
    last_active_at: str
    default_name: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "workspace_root": self.workspace_root,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "default_name": self.default_name,
        }


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
    session_name: str = "detached-session"
    session: SQLiteSession | None = None
    session_root: Path = field(default_factory=_default_session_root)
    session_dir: Path = field(default_factory=lambda: _default_session_root() / "detached-session")
    tasks_dir: Path = field(default_factory=lambda: _default_session_root() / "detached-session" / "tasks")
    traces_dir: Path = field(default_factory=lambda: _default_session_root() / "detached-session" / "traces")
    compaction_dir: Path = field(default_factory=lambda: _default_session_root() / "detached-session" / "compaction")
    # workspace_root 表示这次会话服务哪个仓库根目录。
    # execution_root 表示当前工具默认在哪个目录执行；phase 4 会把它切到 worktree。
    workspace_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    execution_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    # team_dir 和 actor_name 是 AgentTeam phase 1 新加的最小协作状态。
    # lead 和 teammate 共享一套 session 目录，但通过 actor_name 区分发送者身份。
    team_dir: Path = field(default_factory=lambda: _default_session_root() / "detached-session" / "team")
    current_model: str | None = None
    main_model: str | None = None
    light_model: str | None = None
    actor_name: str = "team-lead"
    team_runtime: AgentTeamRuntime | None = None
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
    background_notifications: list[dict[str, object]] = field(default_factory=list)
    background_notification_lock: threading.Lock = field(default_factory=threading.Lock)

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

    def set_execution_root(self, execution_root: Path) -> None:
        # execution_root 变化时，把旧目录的读快照清掉，避免把另一个 worktree 的版本继续当成当前锁。
        resolved_root = execution_root.resolve()
        if resolved_root == self.execution_root:
            return
        self.execution_root = resolved_root
        self.read_snapshots.clear()

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

    def enqueue_background_notification(self, *, task_id: int, text: str) -> None:
        # 后台线程和主线程会并发读写这一队列，所以这里统一加锁。
        with self.background_notification_lock:
            self.background_notifications.append({"task_id": task_id, "text": text})

    def drain_background_notifications(self) -> list[dict[str, object]]:
        with self.background_notification_lock:
            notifications = list(self.background_notifications)
            self.background_notifications.clear()
        return notifications

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
    # SDK session 负责对话历史，本地 context 负责工具侧和会话级轻量状态。
    session_id: str
    session: SQLiteSession
    context: ToolRuntimeContext
    session_dir: Path
    session_root: Path
    meta_path: Path
    meta: SessionMeta

    @property
    def session_name(self) -> str:
        return self.meta.name

    def update_name_from_user_input(self, user_input: str) -> None:
        # 第一条有效用户输入到来后，再把默认标题改成一个简短可识别的名字。
        self.meta.last_active_at = _utc_now()
        if self.meta.default_name:
            derived_name = _build_session_name_from_user_input(user_input)
            if derived_name:
                self.meta.name = derived_name
                self.meta.default_name = False
                self.context.session_name = derived_name
        _save_session_meta(self.meta_path, self.meta)

    def close(self) -> None:
        if self.context.team_runtime is not None:
            self.context.team_runtime.close()
        self.context.close_trace_session()
        self.session.close()


def _load_session_meta(meta_path: Path, *, session_id: str) -> SessionMeta:
    if not meta_path.exists():
        now = _utc_now()
        return SessionMeta(
            session_id=session_id,
            name=_build_default_session_name(),
            workspace_root=str(PROJECT_ROOT.resolve()),
            created_at=now,
            last_active_at=now,
            default_name=True,
        )

    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    return SessionMeta(
        session_id=str(raw.get("session_id") or session_id),
        name=str(raw.get("name") or _build_default_session_name()),
        workspace_root=str(raw.get("workspace_root") or PROJECT_ROOT.resolve()),
        created_at=str(raw.get("created_at") or _utc_now()),
        last_active_at=str(raw.get("last_active_at") or _utc_now()),
        default_name=bool(raw.get("default_name", False)),
    )


def _save_session_meta(meta_path: Path, meta: SessionMeta) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(meta.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_current_session_pointer(pointer_path: Path, session_id: str) -> None:
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_current_session_pointer(pointer_path: Path) -> str | None:
    if not pointer_path.exists():
        return None
    raw = json.loads(pointer_path.read_text(encoding="utf-8"))
    session_id = raw.get("session_id")
    return str(session_id) if isinstance(session_id, str) and session_id else None


def list_saved_sessions(*, session_root: Path | None = None) -> list[SessionMeta]:
    active_root = session_root or _default_session_root()
    if not active_root.exists():
        return []

    sessions: list[SessionMeta] = []
    for child in active_root.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "session_meta.json"
        session_id = child.name
        if not meta_path.exists():
            continue
        sessions.append(_load_session_meta(meta_path, session_id=session_id))

    return sorted(sessions, key=lambda item: item.last_active_at, reverse=True)


def build_cli_session_runtime(
    *,
    session_root: Path | None = None,
    session_id: str | None = None,
    new_session: bool = False,
    workspace_root: Path | None = None,
    trace_enabled: bool | None = None,
) -> CliSessionRuntime:
    # 第一版先把“启动时恢复/选择 session”做好，不做运行中的 session 切换。
    active_root = session_root or _default_session_root()
    active_root.mkdir(parents=True, exist_ok=True)
    pointer_path = _session_pointer_path(active_root)

    # workspace_root 是这条 session 服务哪个项目目录的稳定绑定。
    # 第一版只允许在创建新 session 时指定，避免把旧历史和新目录硬拼在一起。
    if workspace_root is not None and not new_session:
        raise ValueError("`--workspace` 只能和 `--new-session` 一起使用。")

    if new_session:
        active_session_id = session_id or f"session-{uuid4().hex[:12]}"
    else:
        active_session_id = session_id or _read_current_session_pointer(pointer_path)
        if active_session_id is None:
            active_session_id = f"session-{uuid4().hex[:12]}"

    session_dir = active_root / active_session_id
    if session_id is not None and not new_session and not session_dir.exists():
        raise FileNotFoundError(f"未找到 session: {session_id}")
    session_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir = session_dir / "tasks"
    traces_dir = session_dir / "traces"
    compaction_dir = session_dir / "compaction"
    # AgentTeam phase 1 先把 team 状态和 transcript 都收进同一 session 根目录下。
    team_dir = session_dir / "team"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    compaction_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)

    meta_path = session_dir / "session_meta.json"
    meta = _load_session_meta(meta_path, session_id=active_session_id)
    if new_session:
        resolved_workspace_root = (workspace_root or PROJECT_ROOT).resolve()
        meta.workspace_root = str(resolved_workspace_root)
    else:
        resolved_workspace_root = Path(meta.workspace_root).resolve()
    meta.last_active_at = _utc_now()
    _save_session_meta(meta_path, meta)
    _write_current_session_pointer(pointer_path, active_session_id)

    session = SQLiteSession(
        session_id=active_session_id,
        db_path=session_dir / "session.db",
    )
    context = ToolRuntimeContext(
        session_id=active_session_id,
        session_name=meta.name,
        session=session,
        session_root=active_root,
        session_dir=session_dir,
        tasks_dir=tasks_dir,
        traces_dir=traces_dir,
        compaction_dir=compaction_dir,
        workspace_root=resolved_workspace_root,
        execution_root=resolved_workspace_root,
        team_dir=team_dir,
        trace_logger=build_trace_logger(
            active_session_id,
            trace_dir=traces_dir,
            enabled=trace_enabled,
        ),
    )

    # 旧进程退出后，残留的 running 任务要被诚实地标成失败，而不是继续伪装活着。
    from src.tasks.background import mark_interrupted_running_tasks
    from src.tasks.agent_team import build_agent_team_runtime

    mark_interrupted_running_tasks(tasks_dir=tasks_dir)
    # team runtime 是 session 级对象：CLI 每次恢复 session 时一并恢复 team 状态视图。
    context.team_runtime = build_agent_team_runtime(runtime_context=context)
    return CliSessionRuntime(
        session_id=active_session_id,
        session=session,
        context=context,
        session_dir=session_dir,
        session_root=active_root,
        meta_path=meta_path,
        meta=meta,
    )
