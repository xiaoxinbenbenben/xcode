# Tool Response Protocol

## 1. 目标

这层协议只解决一个问题：**所有工具返回值都必须先装进统一的结构化信封，再交给 agent runtime 使用**。

这意味着：

- 工具不能直接返回裸字符串
- 工具自己的差异主要放进 `data`
- 顶层字段保持固定，方便后续统一日志、截断、history 写入和错误处理

这一步先只定义协议层和最小 helper，不实现任何具体工具。

## 2. 职责边界

协议层负责：

- 约束工具返回的顶层结构
- 统一 `success` / `partial` / `error` 的语义
- 提供低成本 helper，避免每个工具手写顶层信封

协议层**不负责**：

- 工具本体如何执行
- 具体工具 `data` 内部字段如何生成
- 输出截断如何落盘
- session、tracing、tool registry 等运行时能力

## 3. 顶层最小形态

所有工具结果都必须满足以下顶层结构：

```python
{
    "status": "success" | "partial" | "error",
    "data": dict,
    "text": str,
    "stats": dict,
    "context": dict,
    "error": {"code": str, "message": str} | None,  # 仅 error 时必填
}
```

约束：

- `status` 只允许 `success`、`partial`、`error`
- `data` 必须是对象，不能为 `None`
- `text` 是给 agent/LLM 看的摘要
- `stats` 必须至少包含 `time_ms`
- `context` 必须至少包含：
  - `cwd`
  - `params_input`
- `error` 保留在顶层，但只在 `status == "error"` 时要求提供

## 4. status 语义

### 4.1 success

表示结果完整可用，没有明显折扣。

典型场景：

- 正常读取文件且没有截断
- 正常列目录且没有回退或部分失败
- 正常写入文件且真实生效

### 4.2 partial

表示结果**可用，但有折扣**。这是这套协议里最重要的状态之一。

典型场景：

- 输出被截断，例如只返回前 N 条结果
- 工具发生策略回退，例如从 `rg` 回退到 Python 搜索
- dry-run，只生成预览但没有真实写入
- 批量操作部分成功、部分失败

约束：

- `partial` 不是失败，而是“能继续推进，但要让上层知道结果打了折”
- 若结果因截断而打折，建议在 `data` 中显式写 `truncated: true`
- `text` 里要明确说明折扣原因和下一步建议

### 4.3 error

表示工具没有提供有效结果，或只拿到了不足以复用的失败结果。

约束：

- `status == "error"` 时，必须带 `error.code` 和 `error.message`
- 旧版 `error: "some string"` 形式不再允许

推荐错误码先复用 legacy 文档里的命名，例如：

- `NOT_FOUND`
- `ACCESS_DENIED`
- `INVALID_PARAM`
- `TIMEOUT`
- `INTERNAL_ERROR`
- `CONFLICT`

## 5. 为什么适合用简单 helper

这层协议更适合用**简单 helper**，而不是继承体系。

原因：

- 协议层的核心价值是统一顶层字段，不是封装复杂行为
- 各工具的差异几乎都在 `data`，没必要为每种工具派生响应类
- helper 能把统一约束收口到一处，同时保持工具实现足够直接

因此这一步推荐：

- 用很薄的类型别名或 `TypedDict` 描述协议
- 用 3 个 helper 构造标准返回：
  - `success_response(...)`
  - `partial_response(...)`
  - `error_response(...)`

如有需要，再保留一个更底层的 `make_tool_response(...)` 统一做最小校验。

## 6. helper 的最小职责

helper 负责：

- 补齐固定顶层字段
- 统一 `error` 结构
- 做最小输入校验，例如：
  - `stats.time_ms` 必填
  - `context.cwd` / `context.params_input` 必填
  - `error` 只能和 `status == "error"` 组合

helper 不负责：

- 自动生成 `text`
- 自动推断 `partial` 的原因
- 自动截断数据

这些都应由具体工具或后续框架层决定。

## 7. 最小代码形态

建议代码接口如下：

```python
success_response(
    data={"entries": []},
    text="Listed 0 entries in '.'.",
    stats={"time_ms": 3},
    context={"cwd": ".", "params_input": {"path": "."}},
)

partial_response(
    data={"matches": [], "truncated": True},
    text="Found 100 matches (truncated). Try narrowing the pattern.",
    stats={"time_ms": 12, "total_matches": 230},
    context={"cwd": ".", "params_input": {"pattern": "TODO"}},
)

error_response(
    code="NOT_FOUND",
    message="File 'src/main.py' does not exist.",
    text="Could not read 'src/main.py'. Check whether the path is correct.",
    stats={"time_ms": 1},
    context={"cwd": ".", "params_input": {"path": "src/main.py"}},
)
```

## 8. 后续工具如何复用

后续具体工具只需要做两件事：

1. 先把工具自己的结果整理进 `data`
2. 再调用对应 helper 组装顶层信封

例如：

- `ls` 工具主要填 `data.entries`
- `grep` 工具主要填 `data.matches`
- `read` 工具主要填 `data.content`
- `edit/write` 工具主要填 `data.applied`

也就是说，这层协议先把“返回长什么样”钉住，后面每个工具只需要关心“自己的 `data` 是什么”。
