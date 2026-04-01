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
- AgentTeam phase 1/2：teammate、team 协议
- 工具输出统一截断与完整结果落盘
- 长会话 `compaction`
- `@file` 输入预处理与噪声控制

## 当前已经实现的能力

- 最小 CLI 入口与流式输出
- 可恢复、可命名、可选择的 session
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

## 最小验证

```bash
uv run python -m unittest discover -s tests
```

```bash
uv run python scripts/cli.py --help
```

## 当前边界

当前版本仍然是原型，刻意没有实现这些能力：

- PTY 终端
- 强沙箱
- 多工作区切换
- 官方 tracing 接入
- JSONL 文件邮箱版 AgentTeam
- teammate 独立 session / 独立进程
- AgentTeam 的 task/worktree 绑定
- 更完整的提示词工程
- 更精细的历史治理，例如按完整轮次压缩、summary 再压缩策略

## 说明

这个仓库当前以“开发与学习并行”为目标，优先保留清晰、可解释、可逐步验证的实现。
