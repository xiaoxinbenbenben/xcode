# xcode

开发中。

这是一个基于 OpenAI Agents SDK 的本地代码 CLI Agent 原型项目，目标是逐步实现一个面向本地仓库工作的 coding agent。

当前版本已经具备：

- 最小 CLI 交互与流式输出
- 可恢复、可选择的 session 多轮记忆
- 分层上下文构造
- 本地 tracing：JSONL + HTML 审计页
- 统一工具协议
- 只读工具、编辑工具、Todo 工具、最小 Bash 工具
- 持久化 task graph、subagent、background task
- skills
- AgentTeam phase 1/2/3/4：teammate、team 协议、task claim、worktree 绑定
- 工具输出统一截断与完整结果落盘
- 长会话 `compaction`
- `@file` 输入预处理与噪声控制
- 新 session 绑定任意项目目录：`--workspace <path>`

## 当前已经实现的能力

- 最小 CLI 入口与流式输出
- 可恢复、可命名、可选择的 session
- 新 session 可绑定任意 `workspace_root`
- 最小上下文分层与拼装
- 本地 tracing：`run_start / context_build / tool_call / tool_result / finish / session_summary`
- trace 产物：`artifacts/traces/*.jsonl` + `artifacts/traces/*.html`
- 长会话治理：`micro_compact`、`auto_compact`、`Compact`
- `@file` 输入预处理：system reminder + 去重 + 数量上限
- 统一工具响应协议
- 只读工具：`LS`、`Glob`、`Grep`、`Read`
- 安全编辑链路：`Edit`、`Write`
- 最小乐观锁：`file_mtime_ms + file_size_bytes`
- 多步骤任务工具：`TodoWrite`
- 最小 Bash 工具：非交互、工作区内执行、带基本超时和最小安全规则
- 统一工具输出截断：超长结果预览 + 落盘 + 回查路径
- 持久化任务系统：`TaskCreate`、`TaskUpdate`、`TaskList`、`TaskGet`
- 一次性分析子代理：`TaskRun`
- 后台命令执行：`BackgroundRun`
- Skills：`SkillLoader` + `Skill`
- AgentTeam：
  - `SpawnTeammate`
  - `ListTeammates`
  - `SendMessage`
  - `ShutdownRequest`
  - `PlanApproval`
  - phase 3 task claim：`ClaimTask`（teammate 内部工具）
  - task lease：`owner + owner_agent_id + lease_expires_at`
  - phase 4 worktree：`WorktreeCreate`、`WorktreeList`、`WorktreeCloseout`
  - task 绑定 worktree 后，teammate 会在对应 `execution_root` 下执行文件和 shell 工具

## 当前结构

```text
src/
  runtime/   # agent 运行时、session、runner、tracing
  tools/     # 已实现工具
  context/   # 上下文分层、压缩、@file 预处理
  tasks/     # task graph、subagent、background、agent team
  protocol/  # 工具统一响应协议
scripts/     # CLI 入口
tests/       # 单元测试
artifacts/   # 本地产物目录
```

## 最小运行

先准备 `.env`，可以参考 [`.env.example`](./.env.example)：

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_MODEL=gpt-5.2-codex
TRACE_ENABLED=true
```

安装依赖：

```bash
uv sync
```

启动 CLI：

```bash
uv run python scripts/cli.py
```

列出已有 session：

```bash
uv run python scripts/cli.py --list-sessions
```

启动一个新 session：

```bash
uv run python scripts/cli.py --new-session
```

启动一个绑定指定项目目录的新 session：

```bash
uv run python scripts/cli.py --new-session --workspace /path/to/project
```

恢复指定 session：

```bash
uv run python scripts/cli.py --session <session_id>
```

或单次调用：

```bash
uv run python scripts/cli.py "列出当前项目根目录结构"
```

## 当前上下文工程语义

- L1：最小 system prompt + 当前已实现工具规则
- L2：仓库本地规则文件 `code_law.md`
- L3：session 历史 + summary
- `@file`：只插入 reminder，不直接注入文件全文
- 长会话：先 `micro_compact`，超阈值再 `auto_compact`
- 工具大输出：只保留预览，完整内容写入 `artifacts/`
- tracing：本地写入 JSONL 与 HTML 审计页，不依赖 SDK 官方 tracing
- task graph：独立持久化到 session 目录，不受 `micro_compact` 影响
- AgentTeam：teammate 消息会以 `<team-messages>` 注入 lead 的下一轮 L3
- AgentTeam phase 3：teammate 在没有显式消息时可从 task board 认领可执行任务
- AgentTeam phase 4：需要隔离改动的 task 可绑定 worktree，teammate 的 `execution_root` 会切到该 worktree

## 最小验证

```bash
uv run python -m unittest discover -s tests
```

```bash
uv run python scripts/cli.py --help
```

## 当前边界

当前版本仍然是原型，刻意没有实现这些更完整的能力：

- PTY-backed terminal session
  - 当前只有单次非交互 shell 执行，还没有“真实终端会话”能力
- 强沙箱 / approval policy engine
  - 当前只有最小工作区边界和命令限制，还没有正式的审批策略与强沙箱执行层
- 多工作区与 workspace routing
  - 当前已经支持“新 session 绑定任意项目目录”
  - 但还没有多 workspace 的列表、选择、切换和路由
- 事件驱动运行时（event-driven runtime）
  - 当前 runtime 仍然以“流式文本回调 + 直接打印”为主，不是统一的结构化事件流
- 工具生命周期事件流（tool lifecycle stream）
  - 当前流式层主要消费文本增量，还没有把 tool intent、tool start、tool finish、tool result 做成一等流事件暴露给 UI
- 状态化 TUI / REPL
  - 当前还是 `prompt_toolkit` CLI，不是基于 React + Ink 的事件驱动 TUI
- `workspace_root` 与 `agent_code_root` 解耦
  - 当前已经区分 `workspace_root` 与 `execution_root`
  - 但 `agent_code_root` 和 `workspace_root` 还没有彻底拆开
- 官方 tracing 接入
- JSONL 文件邮箱版 AgentTeam
- teammate 独立 session / 独立进程
- 更完整的提示词工程
- 更精细的历史治理，例如按完整轮次压缩、summary 再压缩策略

## 说明

这个仓库当前以“开发与学习并行”为目标，优先保留清晰、可解释、可逐步验证的实现。
