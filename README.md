# xcode

开发中。

这是一个基于 OpenAI Agents SDK 的本地代码 CLI Agent 原型项目，目标是逐步实现一个面向本地仓库工作的 coding agent。

当前版本已经具备：

- 最小 CLI 交互与流式输出
- SDK session 驱动的多轮记忆
- 分层上下文构造
- 统一工具协议
- 只读工具、编辑工具、Todo 工具、最小 Bash 工具
- 工具输出统一截断与完整结果落盘
- 长会话 `compaction`
- `@file` 输入预处理与噪声控制

## 当前已经实现的能力

- 最小 CLI 入口与流式输出
- SDK session 驱动的最小多轮记忆
- 最小上下文分层与拼装
- 长会话治理：`micro_compact`、`auto_compact`、`Compact`
- `@file` 输入预处理：system reminder + 去重 + 数量上限
- 统一工具响应协议
- 只读工具：`LS`、`Glob`、`Grep`、`Read`
- 安全编辑链路：`Edit`、`Write`
- 最小乐观锁：`file_mtime_ms + file_size_bytes`
- 多步骤任务工具：`TodoWrite`
- 最小 Bash 工具：非交互、工作区内执行、带基本超时和最小安全规则
- 统一工具输出截断：超长结果预览 + 落盘 + 回查路径

## 当前结构

```text
src/
  runtime/   # agent 运行时、session、runner
  tools/     # 已实现工具
  context/   # 上下文分层、压缩、@file 预处理
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
```

安装依赖：

```bash
uv sync
```

启动 CLI：

```bash
uv run python scripts/cli.py
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

## 最小验证

```bash
uv run python -m unittest discover -s tests
```

```bash
uv run python scripts/cli.py --help
```

## 当前边界

当前版本仍然是原型，刻意没有实现这些能力：

- tracing
- PTY 终端
- 强沙箱
- 多工作区切换
- 更完整的提示词工程
- 更精细的历史治理，例如按完整轮次压缩、summary 再压缩策略

## 说明

这个仓库当前以“开发与学习并行”为目标，优先保留清晰、可解释、可逐步验证的实现。
