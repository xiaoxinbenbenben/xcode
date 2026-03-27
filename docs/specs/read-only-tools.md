# Read-Only Tools

## 1. 目标

这一组只读工具的目标不是“替 agent 思考”，而是给 agent 最基础的**找证据**能力：

- 先看目录结构
- 再按名称找文件
- 再按内容搜代码
- 最后读取具体文件

这一步先实现最小只读工具，不实现写入、session、tracing、todo、task。

## 2. 工具职责边界

### 2.1 LS

- 负责列目录或文件条目
- 不读取文件内容
- 适合回答“这个目录里有什么”“下一步该读哪个文件”

### 2.2 Glob

- 负责按名称或 glob 模式找文件路径
- 只返回路径，不读取内容
- 适合回答“这个项目里有没有某类文件”

### 2.3 Grep

- 负责按内容搜索代码
- 返回匹配的文件、行号和文本
- 适合回答“某个符号、配置、字符串在哪里出现”

### 2.4 Read

- 负责读取文本文件
- 返回带行号内容，方便后续定位与编辑
- 在 `stats` 中返回 `file_mtime_ms` 和 `file_size_bytes`，供后续安全写入复用
- 不负责写入

## 3. 工作区边界

当前阶段所有只读工具都**固定以项目根目录为边界**：

- 相对路径一律相对于项目根目录解析
- `context.cwd` 当前统一写 `"."`
- 不支持任意切换 working directory
- 绝对路径只有在解析后仍位于项目根目录内才允许访问

这样做的原因是：

- 现在还没有 session / working_dir 机制
- 先把安全边界收紧，避免工具越界访问
- 后续如果需要“当前目录”语义，再在 runtime 层统一加，不在工具层各自发明

## 4. 协议约束

所有只读工具都必须返回统一协议，而不是裸字符串：

```python
{
    "status": "success" | "partial" | "error",
    "data": {...},
    "text": "...",
    "stats": {"time_ms": ...},
    "context": {"cwd": ".", "params_input": {...}, ...},
    "error": {"code": "...", "message": "..."} | None,
}
```

其中：

- `success` 表示结果完整可用
- `partial` 表示结果可用但有折扣，例如截断、fallback、部分成功
- `error` 表示没有拿到可复用结果

## 5. 为什么优先用 OpenAI Agents SDK 的 function tool

这一阶段不需要自己做一套工具 schema 汇总系统。

原因：

- Agents SDK 的 `function_tool` 已经能从 Python 函数签名、类型注解、默认值和 docstring 自动生成 OpenAI function schema
- `Agent(tools=[...])` 已经能直接注册这些工具
- 当前我们真正需要自己控制的是：
  - 参数语义是否清楚
  - 返回协议是否统一
  - 工作区边界是否安全

所以这一步采用：

- 每个工具实现为一个普通 Python 函数
- 再通过 `function_tool(...)` 包成 SDK 工具对象
- 最后在 `agent_factory.py` 里把工具列表挂到根 agent

## 6. 统一工具入口的思路

虽然使用了多个 `function_tool`，但实现上仍然保留一个轻量统一入口层：

- `src/tools/common.py`
  - 放路径解析、越界校验、相对路径格式化、通用统计与上下文构造
- `src/tools/read_only.py`
  - 放 4 个只读工具实现
  - 放 `READ_ONLY_TOOLS` 统一导出列表

这样做的意义是：

- schema 生成与注册交给 SDK
- 共享的安全和协议逻辑由本项目自己控制
- 后续新增工具时，只需要继续复用 `common.py` 和协议 helper

## 7. 当前最小实现范围

### 7.1 LS

- 参数：`path`、`offset`、`limit`、`include_hidden`、`ignore`
- 返回 `data.entries`

### 7.2 Glob

- 参数：`pattern`、`path`、`limit`、`include_hidden`、`include_ignored`
- 返回 `data.paths`

### 7.3 Grep

- 参数：`pattern`、`path`、`include`、`case_sensitive`、`limit`
- 返回 `data.matches`
- 若 `rg` 不可用，可回退 Python 搜索，并用 `partial` 表达结果折扣

### 7.4 Read

- 参数：`path`、`start_line`、`limit`
- 返回 `data.content`
- 在 `stats` 中补充 `file_mtime_ms`、`file_size_bytes`

## 8. 错误处理原则

优先使用协议层已有的结构化 `error`：

- `NOT_FOUND`
- `ACCESS_DENIED`
- `INVALID_PARAM`
- `IS_DIRECTORY`
- `BINARY_FILE`
- `INTERNAL_ERROR`

工具需要做到：

- 参数错误明确报错
- 越界访问明确报错
- 文本摘要里说明失败原因和下一步建议

## 9. 后续扩展点

这一步完成后，后续可以继续在现有结构上扩展：

- 加统一输出截断
- 给 `Read` 增加更完整的编码/分页策略
- 给 `Grep` 增加更稳定的 `rg` / Python 双路径策略
- 在 runtime 层接 session、tracing
- 在 agent 指令里加强“先找证据再回答”的行为约束
