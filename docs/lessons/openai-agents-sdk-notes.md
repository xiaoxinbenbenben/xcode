# OpenAI Agents SDK Notes

> 范围：只记录 OpenAI Agents SDK 的 Python 主线用法。  
> 原则：优先以官方文档中的最小正确示例为基线，再讨论如何扩展到项目场景。  
> 官方文档主页：https://openai.github.io/openai-agents-python/

## 1. SDK 是什么

官方把 Agents SDK 描述为一组非常小的 primitives，核心包括：

- `Agent`
- `Runner`
- `Tools`
- `Handoffs`
- `Guardrails`
- `Sessions`
- `Tracing`

它的目标不是替代 Python，而是给你一个轻量但足够表达 agent loop 的运行时。

参考：
- Intro: https://openai.github.io/openai-agents-python/

## 2. 安装与最小示例

安装：

```bash
pip install openai-agents
```

官方最小示例：

```python
from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are a helpful assistant")
result = Runner.run_sync(agent, "Write a haiku about recursion in programming.")
print(result.final_output)
```

这个最小示例只覆盖：

- 创建 agent
- 单次运行
- 读取 `final_output`

它还没有覆盖 tools、sessions、streaming、handoffs、guardrails。

参考：
- Intro / Hello world: https://openai.github.io/openai-agents-python/
- Quickstart: https://openai.github.io/openai-agents-python/quickstart/

## 3. Agent：最核心的配置对象

`Agent(...)` 常用字段包括：

- `name`
- `instructions`
- `model`
- `model_settings`
- `tools`
- `handoffs`
- `output_type`
- `hooks`
- `input_guardrails`
- `output_guardrails`

常见用法：

```python
from agents import Agent

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
)
```

如果需要指定模型和模型参数：

```python
from agents import Agent, ModelSettings

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    model="gpt-5.2",
    model_settings=ModelSettings(temperature=0.2),
)
```

参考：
- Agents: https://openai.github.io/openai-agents-python/agents/

## 4. Runner：三种运行方式

官方文档明确给了三种入口：

1. `Runner.run()`：异步，返回 `RunResult`
2. `Runner.run_sync()`：同步包装
3. `Runner.run_streamed()`：异步流式，返回 `RunResultStreaming`

示意：

```python
from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are a helpful assistant.")

result = Runner.run_sync(agent, "Hello")
print(result.final_output)
```

如果要用异步：

```python
import asyncio
from agents import Agent, Runner

async def main():
    agent = Agent(name="Assistant", instructions="You are a helpful assistant.")
    result = await Runner.run(agent, "Hello")
    print(result.final_output)

asyncio.run(main())
```

参考：
- Running agents: https://openai.github.io/openai-agents-python/running_agents/

## 5. Streaming：最小正确写法

官方最小流式示例：

```python
import asyncio
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner

async def main():
    agent = Agent(name="Joker", instructions="You are a helpful assistant.")
    result = Runner.run_streamed(agent, input="Please tell me 5 jokes.")
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
            print(event.data.delta, end="", flush=True)

asyncio.run(main())
```

这里最重要的点：

- `result.stream_events()` 返回的是 **外层 stream event**
- 文本增量在 `raw_response_event` 的 `event.data` 里
- `event.data` 才是 `ResponseTextDeltaEvent`

不要把外层 `event` 直接当成 `ResponseTextDeltaEvent`。

另外，官方文档还说明：

- 一次 streaming run 结束后，`RunResultStreaming` 会包含完整 run 信息
- `stream_events()` 结束才表示这一轮真的结束

参考：
- Streaming: https://openai.github.io/openai-agents-python/streaming/
- Running agents: https://openai.github.io/openai-agents-python/running_agents/

## 6. Tools：主要分类与起手方式

官方工具大类包括：

- Hosted OpenAI tools
- Local/runtime tools
- Function tools
- Agents as tools
- Experimental Codex tool

最常见起手方式是 function tool：

```python
from agents import Agent, Runner, function_tool

@function_tool
def add(a: int, b: int) -> int:
    return a + b

agent = Agent(
    name="Math",
    instructions="Use tools when needed.",
    tools=[add],
)

print(Runner.run_sync(agent, "What is 2 + 3?").final_output)
```

参考：
- Tools: https://openai.github.io/openai-agents-python/tools/

### 6.1 `function_tool` 的最小实用补充

当前本地环境里，`function_tool` 除了最基本的装饰器用法外，还支持：

- `name_override`
- `description_override`
- `strict_mode`
- `timeout`

本项目当前阶段最有用的是前两个：

```python
from agents import function_tool

@function_tool(
    name_override="LS",
    description_override="列出目录或文件条目。",
)
def list_files(path: str = ".") -> dict:
    return {"status": "success", "data": {}, "text": "...", "stats": {"time_ms": 1}, "context": {"cwd": ".", "params_input": {"path": path}}, "error": None}
```

另外，通过本地探针确认：

- 装饰后的对象类型是 `FunctionTool`
- 可以直接放进 `Agent(tools=[...])`
- 对象上可读取：
  - `tool.name`
  - `tool.description`
  - `tool.params_json_schema`

这意味着当前阶段不需要自己再做一层“工具参数转 OpenAI function schema”的注册系统；优先把函数签名、默认值和 docstring 写清楚，再让 SDK 负责 schema 生成和注册。

### 6.2 `TypedDict + Literal` 适合生成嵌套工具参数 schema

本地探针确认，`function_tool` 对这类签名支持良好：

```python
from typing import Literal, TypedDict

class TodoItem(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]

def todo_write(summary: str, todos: list[TodoItem]) -> dict:
    ...
```

生成出来的 `params_json_schema` 会包含：

- `summary: string`
- `todos: array`
- `todos[].content: string`
- `todos[].status: enum[...]`

这对当前项目很有用，因为像 `TodoWrite` 这种“顶层对象 + 子项数组”的结构，不需要额外引入 Pydantic 模型或手写 JSON schema。

### 6.3 当前项目没有直接采用 SDK 自带 `ShellTool`

本地 SDK 确实已经提供了：

- `LocalShellTool`
- `ShellTool`

但当前项目的最小 Bash 工具仍然优先使用本地 `function_tool` 自行实现。

原因不是 SDK 做不到，而是当前阶段我们更关心这几个项目内语义：

- 固定工作区边界
- 沿用统一 `ToolResponse` 协议
- 沿用 `src/tools/common.py` 的共享 helper
- 保持和只读工具、编辑工具、Todo 工具同一种接入方式

换句话说，当前没有直接采用 SDK shell tool，主要是为了和本项目现有工具层保持一致，而不是否认 SDK 自带 shell 能力的价值。

## 7. Agents as tools 和 handoffs

这两个都能让 agent 间协作，但语义不同：

- `agents as tools`：把另一个 agent 暴露成可调用工具，主 agent 保留控制权

## 8. Session 与结构化 input 的一个实用边界

本地探针确认：

- `Runner.run_streamed(...)` 的 `input` 不只可以是 `str`
- 也可以是 `list[TResponseInputItem]`
- `SQLiteSession` 还提供 `get_items()`，可以读出当前已保存的历史项

这对当前项目的上下文分层很重要，因为：

- builder 可以先保留结构化层次
- 运行边界再把“当前轮输入”适配成 input items
- session 历史可以单独读取出来做 L3 层

另外要注意一件事：

- session 会持久化每轮输入项
- 如果把仓库规则文档作为每轮 input items 注入，它会重复写进 history

因此，像 `code_law.md` 这种“不应该进入历史”的规则，更适合在当前阶段通过 `Agent.instructions` 注入，而不是作为每轮 input items 反复传入。
- `handoff`：把控制权交给另一个 agent，让它接管对话

handoff 的官方说明里几个关键点：

- 所有 agent 都有 `handoffs` 参数
- 你可以直接传 `Agent`
- 或者用 `handoff()` 自定义行为、输入、过滤器

参考：
- Tools / Agents as tools: https://openai.github.io/openai-agents-python/tools/
- Handoffs: https://openai.github.io/openai-agents-python/handoffs/

## 8. Sessions：多轮记忆

官方对 session memory 的核心行为说明很重要：

1. 每次 run 之前，runner 会自动取出当前 session 历史并拼到输入前面
2. 每次 run 之后，本轮新增 items 会自动写回 session
3. 同一个 session 的后续 run 会带上完整历史

这意味着你不需要手动维护多轮历史，也不需要手动调用 `.to_input_list()` 来接续普通对话。

最常见的内置实现：

- `SQLiteSession`
- `AsyncSQLiteSession`
- `RedisSession`
- `SQLAlchemySession`
- `OpenAIConversationsSession`
- `OpenAIResponsesCompactionSession`

最小示例：

```python
import asyncio
from agents import Agent, Runner, SQLiteSession

async def main():
    agent = Agent(name="Assistant")
    session = SQLiteSession("user_123", "conversations.db")
    result = await Runner.run(agent, "Hello", session=session)
    print(result.final_output)

asyncio.run(main())
```

参考：
- Sessions: https://openai.github.io/openai-agents-python/sessions/

### 8.1 `session=` 和 `context=` 可以同时使用

本地探针确认，`Runner.run_streamed(...)` 的签名同时支持：

- `session=...`
- `context=...`

这两个参数职责不同：

- `session`：给 SDK 管理对话历史
- `context`：给本地 agent / tools 保存非消息状态

这很适合当前项目的最小结构：

- 用 SDK session 记住多轮对话
- 用本地 context 保存“最近一次 Read 的文件锁快照”

也就是说，session 不需要承担所有运行时状态。

### 8.2 `RunContextWrapper` 首参不会进入 tool schema

本地探针确认，若 function tool 的第一个参数是：

```python
ctx: RunContextWrapper[MyContext]
```

那么这个参数不会进入生成出来的 `params_json_schema`。

例如：

```python
from dataclasses import dataclass
from agents import RunContextWrapper, function_tool

@dataclass
class MyContext:
    value: int = 0

def foo(ctx: RunContextWrapper[MyContext], a: int) -> int:
    return a

tool = function_tool(foo, name_override="Foo")
print(tool.params_json_schema)
```

输出里只会包含 `a`，不会包含 `ctx`。

这意味着当前项目可以把：

- 模型看见的参数
- 本地 runtime state

清楚分开。对 `Read/Edit/Write` 这类工具尤其有用：模型不必显式传本地状态参数，但工具仍能通过 `ctx.context` 访问当前会话里的运行时信息。

## 9. Guardrails：输入与输出校验

官方文档当前主线强调两类 guardrails：

1. Input guardrails：跑在初始用户输入上
2. Output guardrails：跑在最终 agent 输出上

常见用途：

- 校验输入是否满足格式
- 判断当前问题是否属于允许范围
- 判断最终输出是否满足业务约束

参考：
- Guardrails: https://openai.github.io/openai-agents-python/guardrails/

## 10. Tracing：默认内建

官方说明：

- SDK 默认会 trace
- 默认 trace 名称是 `"Agent workflow"`
- `Runner.run()` / `run_sync()` / `run_streamed()` 默认都在 trace 里

默认会追踪的内容包括：

- agent runs
- LLM generations
- function tool calls
- handoffs
- guardrails

如果不是直连 OpenAI 平台，而是第三方兼容接口，常见处理是：

```python
from agents import set_tracing_disabled

set_tracing_disabled(True)
```

参考：
- Tracing: https://openai.github.io/openai-agents-python/tracing/

## 11. 第三方 OpenAI-compatible 接口接法

官方 models 文档给了几种接非 OpenAI provider 的方法，最常用的一种是：

- 自己创建 `AsyncOpenAI(base_url=..., api_key=...)`
- 用 `set_default_openai_client(...)` 设成全局默认 client

官方还明确说明：

- SDK 默认使用 Responses API
- 很多第三方 provider 还不支持 Responses API
- 这种情况下应切到 `chat_completions`

最小示例：

```python
from agents import set_default_openai_client, set_default_openai_api, set_tracing_disabled
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url="https://example.com/v1", api_key="sk-...")
set_default_openai_client(client)
set_default_openai_api("chat_completions")
set_tracing_disabled(True)
```

如果你希望 Chat Completions 流式请求带上 usage chunk，可以用：

```python
from agents import Agent, ModelSettings

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    model_settings=ModelSettings(include_usage=True),
)
```

在当前 `openai-agents` 版本里，这会让 Chat Completions 流式请求携带：

```json
{
  "stream_options": {
    "include_usage": true
  }
}
```

参考：
- Models: https://openai.github.io/openai-agents-python/models/

## 12. 实用开发规则

1. 先从官方最小示例开始，不要先自行抽象。
2. 先跑通最小基线，再叠加第三方 provider、CLI 输入、多轮状态等项目逻辑。
3. 流式输出优先直接对齐官方 streaming 示例。
4. 如果行为不符合预期，先写最小探针收集证据，再决定是否下钻源码。
