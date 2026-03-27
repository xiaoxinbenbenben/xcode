# xcode

开发中。

这是一个基于 OpenAI Agents SDK 的本地代码 CLI Agent 原型项目，目标是逐步实现一个面向本地仓库工作的 coding agent。

## 当前已经实现的能力

- 最小 CLI 入口与流式输出
- SDK session 驱动的最小多轮记忆
- 最小上下文分层与拼装
- 统一工具响应协议
- 只读工具：`LS`、`Glob`、`Grep`、`Read`
- 安全编辑链路：`Edit`、`Write`
- 最小乐观锁：`file_mtime_ms + file_size_bytes`
- 多步骤任务工具：`TodoWrite`
- 最小 Bash 工具：非交互、工作区内执行、带基本超时和最小安全规则

## 当前目录结构

```text
src/
  runtime/   # agent 运行时、session、runner
  tools/     # 已实现工具
  context/   # 上下文分层与拼装
  protocol/  # 工具统一响应协议
scripts/     # CLI 入口
tests/       # 单元测试
docs/specs/  # 当前保留的规范文档
docs/lessons/# 开发和 SDK 经验记录
artifacts/   # 本地产物目录
```

## 最小运行方式

先准备 `.env`：

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_MODEL=gpt-5.2-codex
```

启动 CLI：

```bash
uv run python scripts/cli.py
```

或单次调用：

```bash
uv run python scripts/cli.py "列出当前项目根目录结构"
```

## 最小验证

```bash
uv run python -m unittest discover -s tests
```

## 当前边界

当前版本仍然是原型，刻意没有实现这些能力：

- tracing
- summary 压缩
- `@file` 噪声控制
- PTY 终端
- 强沙箱
- 多工作区切换
- 更完整的提示词工程

## 说明

仓库当前只保留和现有实现直接相关的代码与文档。
legacy、reference、plans 和本地验证脚本只用于开发期参考，不再纳入仓库版本历史。
