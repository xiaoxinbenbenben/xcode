# Session

## 1. 目标

这一阶段的 session 只解决一个核心问题：让 CLI 在连续多轮输入时拥有**最小可持续的对话上下文**，而不是每一轮都从空白开始。

要求是：

- 使用 OpenAI Agents SDK 原生 `session` 机制
- 不手工维护完整消息列表
- 不实现 Summary 压缩

## 2. session 在这个系统中的职责

当前系统里，session 承担两类不同但相关的职责：

### 2.1 对话历史职责

这部分直接交给 SDK 的 `session` 机制：

- 每次 run 前，SDK 自动取出当前会话历史
- 每次 run 后，SDK 自动把本轮新增内容写回会话
- CLI 在同一次启动里的连续多轮输入，因此能自然记住前文

也就是说，session 的主要价值是：

- 不需要我们自己维护消息列表
- 不需要手工把上轮输出拼回下轮输入

### 2.2 本地工具状态职责

除了对话历史，本项目还需要一层很薄的本地运行时状态，用来支持安全编辑链路。

当前只放一类状态：

- `read_snapshots`

它的语义不是“全局最后一次 Read”，而是：

- **按文件路径保存该文件最近一次 `Read` 的锁快照**

形态类似：

```python
{
    "src/a.py": {"file_mtime_ms": 1, "file_size_bytes": 100},
    "src/b.py": {"file_mtime_ms": 2, "file_size_bytes": 220},
}
```

这样：

- `Read A` 不会覆盖 `B`
- 同一路径再次 `Read` 时，只更新该路径自己的快照

## 3. 为什么要把这两层分开

SDK session 负责“模型历史”。

本地快照表负责“本地工具的非消息状态”。

它们不能混成一层，原因是：

- 对话历史是给模型继续推理用的
- 文件锁快照是给 `Edit/Write` 做乐观锁校验用的
- 两者的生命周期、调试方式和未来治理方式都不同

所以当前结构采用：

- `session=`：交给 SDK
- `context=`：交给本项目自己的 runtime context

## 4. 当前阶段的最小实现

### 4.1 SDK session 选型

当前先使用 SDK 自带的 `SQLiteSession`。

但为了控制复杂度，这一步先用**当前 CLI 进程内的会话对象**，只保证：

- 同一次 CLI 启动中的多轮输入有记忆

这一步先不承诺：

- 跨 CLI 重启恢复同一个会话
- 用户可见的 session 管理命令

### 4.2 本地快照表

本地快照表只存：

- `file_mtime_ms`
- `file_size_bytes`

并且加一个很小的容量上限，例如 256 条。

超过上限时：

- 淘汰最久未使用的路径快照

这样做的原因是：

- 防止长会话里读了大量不同文件后，快照表无界增长
- 保持实现足够简单，不引入复杂历史治理

## 5. 自动锁注入如何与 session 配合

`Read`、`Edit`、`Write` 的协作关系如下：

1. `Read` 成功后，把该文件的 `file_mtime_ms` 和 `file_size_bytes` 记入 `read_snapshots[path]`
2. `Edit/Write` 若调用时没有显式传 `expected_*`
3. runtime 先按路径去 `read_snapshots[path]` 里查
4. 找到则自动补上锁值
5. 再进入现有乐观锁校验逻辑

这里有几个边界：

- 只按**同一路径**注入，不会拿别的文件的锁
- 成功写入后，会把该路径的快照更新为新版本
- 冲突时不会偷偷刷新快照；出现 `CONFLICT` 后应先重新 `Read`

## 6. 当前阶段先不解决哪些长上下文问题

这一步只做“有 session”。

以下高级历史治理能力先不做：

- Summary 压缩
- 历史裁剪策略
- 旧轮次工具结果的精细落盘与回放
- 跨进程恢复旧会话
- 多会话切换、命名、列出、删除
- 针对超长上下文的自动 memory compaction

也就是说，当前阶段的 session 只是把“多轮记忆”立起来，不试图一次解决所有长上下文治理问题。

## 7. 与当前 runtime 的关系

当前 runtime 的职责边界保持不变，只是把 session 接进去：

- `src/runtime/session.py`
  - 创建 SDK session
  - 创建本地 runtime context
- `src/runtime/runner.py`
  - 调用 `Runner.run_streamed(..., session=..., context=...)`
- `scripts/cli.py`
  - 在一次 REPL 生命周期内复用同一个 session runtime

这样后续继续扩展时也比较清楚：

- 若要做更强历史治理，优先改 session/runtime 层
- 若要做工具自动状态注入，优先改 runtime context 与工具包装层

## 8. 当前最小成功标准

这一步完成后，应满足：

1. 用户在同一次 CLI 会话里连续提问，agent 能基于前文继续回答
2. CLI 不需要手工维护完整消息列表
3. `Read` 后的文件锁快照能在同一会话里按路径复用给 `Edit/Write`
4. 不实现 Summary、裁剪和复杂 session 管理
