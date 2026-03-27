# Development Lessons

## 2026-03-25: `python scripts/cli.py` 与导入测试的模块路径不一致

### 现象

- `tests/test_cli.py` 里直接 `from scripts.cli import main` 可以通过
- 但真实执行 `uv run python scripts/cli.py --help` 时失败
- 报错为 `ModuleNotFoundError: No module named 'src'`

### 错误判断

- 先以为“CLI 测试通过”就代表脚本入口已经可用
- 只验证了导入后的 `main()`，没有验证真实的脚本执行路径

### 根因

当 Python 直接执行 `scripts/cli.py` 时，`sys.path[0]` 是 `scripts/`，不是项目根目录。
这时 `from src.runtime.agent_factory import ...` 不一定能被解析。

### 正确做法

- 对外承诺的是 `python scripts/cli.py ...` 这种脚本入口时，必须做一次真实脚本烟雾测试
- 最小修复是先把项目根目录加入 `sys.path`，再导入 `src.*`
- 同时保留一个 subprocess 级别的回归测试，避免只有“导入式测试”通过

### 后续规则

1. 只要有 CLI 脚本入口，就至少补一个真实脚本执行测试，例如 `--help`
2. 不要把“模块可导入”和“脚本可执行”混为一谈
3. 如果选择 `scripts/*.py` 作为入口，优先显式处理项目根目录导入路径

## 2026-03-25: OpenAI Agents SDK 流式事件判断写错

### 现象

- 终端里看起来像“等很久后一次性输出整段文本”
- 脚本已经调用了 `Runner.run_streamed(...)`
- 第三方接口用 `curl` 明明能看到 `data:` 流式返回

### 错误判断

- 先怀疑第三方接口流式不稳定
- 先去读 SDK 底层源码并自己改写事件判断
- 没有先严格对齐官方文档里的最小流式示例

### 根因

`result.stream_events()` 的外层事件不是直接的 `ResponseTextDeltaEvent`，而是：

- 外层：`raw_response_event`
- 内层：`event.data`
- 真正的文本增量：`event.data.delta`

正确写法应先以官方示例为基线：

```python
async for event in result.stream_events():
    if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
        print(event.data.delta, end="", flush=True)
```

### 为什么会绕远路

- 看到 SDK 有多层 streaming event，就先按自己的理解改写了事件过滤
- 没先把官方最小示例原样跑通
- 在没有证据之前，把“脚本没有流出来”和“接口没有流出来”混为一谈

### 证据

通过单独探针确认：

- 底层 `openai` client 很快就能收到第一个 chunk
- Agents SDK 也能很快收到外层 `raw_response_event`
- 问题出在脚本读取错了事件层级，而不是接口完全不流

### 后续规则

1. 对 OpenAI Agents SDK 这类已有官方最小示例的能力，先把官方示例原样跑通。
2. 先以官方示例作为“最小正确基线”，再叠加第三方接口、CLI 输入、多轮交互等项目逻辑。
3. 如果结果和预期不符，先做最小探针拿证据，区分：
   - 接口层
   - SDK 层
   - 终端显示层
4. 只有当官方最小示例与当前环境不一致时，才进入源码层排查。
5. 发生误判后，要把“现象 / 根因 / 正确做法 / 后续规则”补写回本文件。

### 相关资料

- Streaming guide: https://openai.github.io/openai-agents-python/streaming/
- Running agents: https://openai.github.io/openai-agents-python/running_agents/

## 2026-03-26: 没有 session 时，不要把文件锁做成隐藏缓存

### 现象

- 需要给本地 `Read -> Edit/Write` 链路补乐观锁
- 直觉上容易把 `Read` 的文件元信息偷偷缓存到 runtime 或工具层
- 这样后续 `Edit` / `Write` 看起来可以少传两个参数

### 错误判断

- 先把“省参数”当成第一目标
- 忽略了当前项目还没有 session，跨轮状态并不稳定
- 低估了隐藏状态对调试和冲突解释的伤害

### 根因

当前 CLI 只有多轮交互壳，没有 session 级记忆。
如果现在先做隐藏缓存，会立刻引入两个问题：

- `Edit` / `Write` 到底用了哪一次 `Read` 的结果，不透明
- 冲突时很难解释是“文件真冲突了”，还是“缓存过期了”

### 正确做法

- 在没有 session 的阶段，先用显式元信息传递：
  - `Read` 返回 `stats.file_mtime_ms`
  - `Read` 返回 `stats.file_size_bytes`
  - `Edit` / `Write` 显式接收 `expected_mtime_ms`
  - `Edit` / `Write` 显式接收 `expected_size_bytes`
- 若锁值不匹配，返回结构化 `CONFLICT`
- 等 session 机制落地后，再考虑是否由 runtime 做自动注入

### 后续规则

1. 只要当前步骤还没有 session，就优先避免隐藏工具状态。
2. 需要跨步骤复用的信息，优先通过结构化响应显式返回，再由下一步显式传入。
3. 冲突类错误必须带可比对的期望值和当前值，不能只返回一句“失败了”。

## 2026-03-26: `curl` 的 Authorization header 不能直接当作 SDK 的 `api_key`

### 现象

- `curl` 请求同一个兼容接口可以成功
- CLI 却返回 `401`，提示“无效的令牌”
- `.env` 里看起来已经配置了 `OPENAI_API_KEY`

### 错误判断

- 先把问题归因为上游接口不稳定
- 只盯着 `BASE_URL`、模型名和 SDK 调用链
- 没先验证代码实际读到的 `OPENAI_API_KEY` 长什么样

### 根因

`curl` 需要的是完整请求头：

```text
Authorization: Bearer <token>
```

但 OpenAI Python SDK 的 `api_key` 参数只应该接收原始 token：

```text
<token>
```

如果把 `.env` 写成 `OPENAI_API_KEY=Bearer <token>`，SDK 会再自动补一层 `Bearer `，
最终就可能发成 `Authorization: Bearer Bearer <token>`，从而触发 `401`。

### 正确做法

- `.env` 里的 `OPENAI_API_KEY` 优先保存原始 token，不带 `Bearer `
- 配置读取层可以做一次最小归一化，兼容去掉常见的 `Bearer ` 前缀
- 遇到认证错误时，先打印脱敏后的已加载配置，确认程序实际读到的值

### 后续规则

1. 区分“HTTP 请求头写法”和“SDK 参数写法”，不要直接照抄。
2. 排查认证问题时，先验证程序实际加载到的 key 形态，而不是只看 `.env` 文件肉眼内容。
3. 配置层允许做最小的输入归一化，但不要在下游各模块重复处理同一件事。
