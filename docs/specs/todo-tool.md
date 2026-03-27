# Todo Tool

## 1. 目标

Todo 工具不是通用待办列表应用，而是给 coding agent 处理**多步骤任务**时使用的最小计划工具。

它解决的是这几个问题：

- 复杂任务里，模型容易偏航或忘掉当前阶段目标
- 多轮上下文里，模型需要一个短小、稳定、可复述的任务概览
- 用户需要看到当前计划，而不是一串难追踪的内部推理

因此，这个工具的核心作用是：

- 让模型显式提交“当前完整任务列表”
- 让工具负责校验、压缩、展示和完成态落盘

## 2. 为什么最小数据结构建议使用 `summary + todos[]`

当前阶段最小输入建议固定为：

- `summary`
- `todos[]`

其中每个 todo 至少包含：

- `content`
- `status`

这样做有几个好处：

- `summary` 提供“整体目标”，避免只看碎片任务时失去方向
- `todos[]` 提供“当前执行面”，便于模型明确下一步
- 结构足够小，模型容易稳定输出
- 未来若要补 `owner`、`priority`、`id`，也可以在不破坏主形状的前提下扩展

当前阶段不强制引入 `id`，因为：

- 我们采用声明式全量覆盖，不依赖 patch 定位
- 给模型增加 `id` 维护只会提高心智负担

## 3. 为什么采用声明式全量覆盖，而不是增量 patch

这一阶段推荐模型每次都提交“**当前完整列表**”。

原因很直接：

- coding agent 擅长重新规划，但不擅长可靠维护细碎 patch
- 若使用 `add/update/remove/reorder` 这类增量操作，模型更容易出现状态漂移
- 全量覆盖时，工具只需要校验“当前列表是否合法”，不用推断历史差异

也就是说，Todo 工具的目标不是“做数据库 patch”，而是“把当前计划声明清楚”。

## 4. 最小数据结构

### 4.1 输入

```python
{
    "summary": "修复 session 与 TodoWrite 集成",
    "todos": [
        {"content": "补 TodoWrite 规范", "status": "completed"},
        {"content": "实现 TodoWrite 工具", "status": "in_progress"},
        {"content": "补测试与验证", "status": "pending"},
    ],
}
```

### 4.2 状态集合

当前只支持：

- `pending`
- `in_progress`
- `completed`
- `cancelled`

### 4.3 约束

当前阶段建议工具负责兜底这些约束：

- `summary` 不能为空
- `todos` 不能为空
- todo 数量上限为 10
- 单条 `content` 去首尾空白后不能为空
- 单条 `content` 长度上限为 60
- 列表里最多只能有一个 `in_progress`

## 5. `in_progress` 约束如何表达

表达方式很简单：

- 允许 0 个 `in_progress`
- 允许 1 个 `in_progress`
- 超过 1 个时，返回 `INVALID_PARAM`

工具不尝试替模型“猜”哪个应该保留。

因为多于一个 `in_progress` 的根因通常是：

- 模型没有完成状态切换
- 模型没有在重规划时清理旧项

这类错误更适合直接报结构化错误，让模型重提完整列表。

## 6. `recap` 如何生成

`recap` 由工具自动生成，目标是：

- 给模型一个短小、稳定、可放进上下文尾部的当前任务概览
- 降低模型在长对话中丢失执行焦点的风险

当前生成规则：

1. 先计算进度前缀：
   - `done = completed + cancelled`
   - 形式为 `[done/total]`
2. 只优先复述：
   - `in_progress`：最多 1 条
   - `pending`：最多 3 条
   - `cancelled`：最多 2 条
3. `completed` 默认不逐条复述
4. 若没有 `in_progress` 和 `pending`：
   - 生成完成态概括，如 `All todos resolved.`

示例：

```text
[1/4] In progress: 实现 TodoWrite 工具. Pending: 补测试与验证. Cancelled: 放弃额外 UI.
```

## 7. 返回结构

TodoWrite 继续复用统一工具响应协议：

```python
{
    "status": "success" | "partial" | "error",
    "data": {...},
    "text": "...",
    "stats": {"time_ms": ...},
    "context": {"cwd": ".", "params_input": {...}},
    "error": {"code": "...", "message": "..."} | None,
}
```

当前阶段成功返回的 `data` 最小包含：

- `summary`
- `todos`
- `recap`
- `persisted`
- `archive_path`

`stats` 至少包含：

- `time_ms`
- `total`
- `pending`
- `in_progress`
- `completed`
- `cancelled`

## 8. 用户可读文本

`text` 是给用户看的简洁清单，不是给模型做主解析的数据。

当前阶段建议使用简洁 ASCII 标记：

- `[>]` 表示 `in_progress`
- `[ ]` 表示 `pending`
- `[x]` 表示 `completed`
- `[-]` 表示 `cancelled`

示例：

```text
TODO: 修复 session 与 TodoWrite 集成
[x] 补 TodoWrite 规范
[>] 实现 TodoWrite 工具
[ ] 补测试与验证
```

## 9. 完成态持久化何时触发

当前阶段的完成态持久化规则是：

- 当 `todos` 中所有项都变成 `completed` 或 `cancelled` 时触发
- 若本次完成态与上一次已持久化的完成态完全相同，则不重复写入

这里“完全相同”基于：

- `summary`
- 规范化后的 `todos` 顺序与状态

工具通过 runtime context 记录最近一次已持久化完成态的 fingerprint，避免同一完成态反复追加。

## 10. 完成态持久化写到哪里

当前新项目里，完成态持久化最小适配到：

- `artifacts/todos/todo-session-<session_id>.md`

说明：

- 每个 CLI session 使用一个归档文件
- 同一 session 内多次“整单完成”，会向同一个文件追加新的任务块
- 这样用户可以在当前项目里直接查看完成历史，不依赖额外数据库

任务块建议格式：

```text
## task1-20260326-180102

Summary: 修复 session 与 TodoWrite 集成
Recap: [3/3] All todos resolved.

### Completed
- [x] 补 TodoWrite 规范
- [x] 实现 TodoWrite 工具
- [x] 补测试与验证
```

## 11. 当前阶段的运行时职责分工

### 11.1 模型负责

- 决定何时使用 TodoWrite
- 提交完整 `summary + todos[]`
- 调整任务顺序、状态与取消决策

### 11.2 工具负责

- 参数校验
- `in_progress` 约束校验
- `recap` 生成
- 用户文本生成
- 完成态持久化
- 结构化结果返回

### 11.3 runtime context 负责

- 保存当前 todo 状态
- 保存已持久化完成态 fingerprint
- 记录当前 session 的 todo 归档文件路径与完成块计数

## 12. 这一步先不解决的问题

当前阶段明确不做：

- 增量 patch
- todo id 稳定复用
- 多列表并存
- Todo Summary 压缩
- 把 todo 自动注入系统 prompt
- 跨 CLI 重启恢复 todo 当前态
- 通用待办应用能力

也就是说，这一步只做一个**面向 coding agent 的最小计划工具**，先把“结构化计划、简短 recap、完成态归档”立起来。
