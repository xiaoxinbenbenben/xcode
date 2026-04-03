# xcode

一个基于 OpenAI Agents SDK 的本地代码 CLI Agent 原型项目，目标是逐步做出一个面向本地仓库工作的 coding agent。

## 已实现能力

- CLI 与 React + Ink TUI（MVP）
- 可恢复、可命名、可选择的 session
- 新 session 绑定任意项目目录：`--workspace <path>`
- 结构化 runtime events
- 本地 tracing：`artifacts/traces/*.jsonl` + `artifacts/traces/*.html`
- 分层上下文：L1 / L2 / L3
- 结构化主 system prompt 与分组工具提示词
- `@file` 输入预处理与 system reminder
- 长会话治理：`micro_compact`、`auto_compact`、`Compact`
- 工具输出统一截断、落盘与回查
- 统一工具协议
- 只读工具：`LS`、`Glob`、`Grep`、`Read`
- 编辑工具：`Edit`、`Write`
- 计划工具：`TodoWrite`
- 最小 Bash 工具
- 持久化任务系统：`TaskCreate`、`TaskUpdate`、`TaskList`、`TaskGet`
- 一次性分析子代理：`TaskRun`
- 后台命令执行：`BackgroundRun`
- Skills：`SkillLoader` + `Skill`
- AgentTeam

## 目录结构

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

## 运行

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

从仓库根目录启动 CLI：

```bash
uv run python scripts/cli.py
```

单次调用：

```bash
uv run python scripts/cli.py "列出当前项目根目录结构"
```

列出已有 session：

```bash
uv run python scripts/cli.py --list-sessions
```

启动一个新 session：

```bash
uv run python scripts/cli.py --new-session
```

新建并绑定指定项目目录：

```bash
uv run python scripts/cli.py --new-session --workspace /path/to/project
```

恢复指定 session：

```bash
uv run python scripts/cli.py --session <session_id>
```

从仓库根目录启动 TUI：

```bash
npm --prefix tui run dev
```

TUI 新建并绑定指定项目目录：

```bash
npm --prefix tui run dev -- --workspace /path/to/project
```

TUI 恢复指定 session：

```bash
npm --prefix tui run dev -- --session <session_id>
```

TUI 输入规则：

```text
Enter 发送
Esc 后按 Enter 换行
/quit 或 Ctrl+C 退出
```

## 当前语义

- L1：最小 system prompt + 当前已实现工具规则
- L2：仓库本地规则文件 `code_law.md`
- L3：session 历史 + summary
- `@file`：只插入 reminder，不直接注入文件全文
- 长会话：先 `micro_compact`，超阈值再 `auto_compact`
- 工具大输出：只保留预览，完整内容写入 `artifacts/`
- 工具输出一旦是 `partial` / `truncated`，模型应按元信息继续回查完整内容
- tracing：本地写入 JSONL 与 HTML 审计页，不依赖 SDK 官方 tracing
- runtime：Python 侧统一产出结构化事件，CLI/TUI 消费同一条事件流
- tool lifecycle：UI 默认展示工具摘要，不默认展开完整 `tool_result`
- TUI：通过 Python CLI 的 JSONL 事件桥驱动，而不是直接嵌入模型调用
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

- PTY 终端会话还没做，当前只有单次非交互 shell 执行
- 强沙箱 / approval policy engine 还没做
- 还没有多 workspace 的列表、切换和路由；当前是一条 session 绑定一个 workspace
- tool lifecycle 还没有 tool 内部 stdout/stderr 级别的细粒度事件
- TUI 还是 MVP，还没有 session 选择、detail 展开和更完整的输入编辑能力
- `agent_code_root` 和 `workspace_root` 还没有彻底拆开
- AgentTeam 的 JSONL 文件邮箱、独立 session、独立进程还没做
