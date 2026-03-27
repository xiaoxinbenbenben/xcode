# Runtime Structure

本阶段的 runtime 只保留 3 个边界：

- `src/runtime/config.py`
  - 读取最小环境变量：`OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`
- `src/runtime/agent_factory.py`
  - 只负责创建根 `Agent`
- `src/runtime/runner.py`
  - 负责 SDK 默认设置和一次 `Runner.run_streamed(...)`

CLI `[scripts/cli.py](/Users/xiaoxin/my_area/LLM/xx-coding/scripts/cli.py)` 只负责输入输出与 REPL 交互，不再直接处理 SDK 初始化。

后续扩展点：

- `session`：优先加在 `runner.py`
- `tracing`：优先加在 `runner.py`
- `tools`：优先加在 `agent_factory.py`
