# File Edit Safety

## 1. 目标

这一步只解决一个问题：在当前项目里建立一条最小可用、可解释的本地编辑链路：

- `Read` 先读取文件内容和文件元信息
- `Edit` / `Write` 在落盘前校验这些元信息
- 若文件在读取后发生变化，工具必须返回结构化冲突错误，而不是直接覆盖

这一步先实现最小 `Read -> Edit/Write` 数据流，不实现 `MultiEdit`、session、自动锁注入、diff 预览。

## 2. 为什么保留先读后写约束

已有文件上的盲改风险很高，主要有两类：

- agent 还没真正读过文件，就基于猜测写入
- agent 读过文件，但用户或其它进程随后改过文件，agent 仍然按旧上下文覆盖

因此当前阶段保留一个明确约束：

- **已有文件**：必须先 `Read`，再 `Edit` 或 `Write`
- **新文件**：允许直接 `Write` 创建，因为不存在旧内容，也不存在覆盖别人修改的问题

这个约束的价值不是“多一道手续”，而是让每次写入都有一份可核对的读取快照。

## 3. 最小乐观锁设计

### 3.1 锁字段

当前最小乐观锁只使用两个显式字段：

- `file_mtime_ms`
- `file_size_bytes`

`Read` 负责把它们返回出来；`Edit` 和 `Write` 在处理已有文件前必须校验：

- `expected_mtime_ms`
- `expected_size_bytes`

### 3.2 为什么先用这两个字段

原因很简单：

- 它们容易解释，也容易测试
- 不需要额外状态存储
- 足够表达“我看到的文件版本”和“当前磁盘上的文件版本”是否一致

当前阶段**不做隐藏缓存**。也就是说：

- 工具层不偷偷记住上一次 `Read`
- CLI / runtime 也不保存文件锁状态
- 锁值完全通过 `Read` 返回，再由后续 `Edit` / `Write` 显式传入

这样做的原因是当前还没有 session 机制。若现在先做隐式缓存，行为会变得不透明，冲突来源也更难定位。

### 3.3 当前方案的边界

`mtime_ms + size_bytes` 是一个务实的最小方案，但不是“绝对完美版本号”。

例如：

- 某些文件系统的 mtime 精度有限
- 极端情况下，文件可能在很短时间内被改成相同大小

当前项目先接受这个权衡，因为它能以最小复杂度把“先读后写”和“冲突显式报错”立起来。后续若需要更强保证，可以在不改变数据流的前提下补充内容哈希。

## 4. 数据流约束

### 4.1 Read

`Read` 除了返回带行号文本，还需要在 `stats` 里返回：

- `file_mtime_ms`
- `file_size_bytes`

这样后续编辑工具可以直接复用 `Read` 的结果，不需要再额外探测一次“旧版本”。

### 4.2 Edit

`Edit` 只处理**已有文件**，并且仍然采用最小单点替换语义：

- 传入 `path`
- 传入 `old_string`
- 传入 `new_string`
- 传入 `expected_mtime_ms`
- 传入 `expected_size_bytes`

执行顺序：

1. 校验路径与工作区边界
2. 校验目标文件存在且不是目录
3. 校验乐观锁字段存在
4. 对比当前文件的 `mtime` 和 `size`
5. 通过后再读取文本并执行唯一锚点替换
6. 若成功则落盘

### 4.3 Write

`Write` 负责全量写入字符串内容：

- 若目标文件不存在：允许直接创建
- 若目标文件已存在：必须带 `expected_mtime_ms` 和 `expected_size_bytes`

这样可以同时满足两种常见路径：

- 新建文件
- 覆盖已经读过的文本文件

## 5. 冲突错误如何表达

当 `Edit` 或 `Write` 发现当前文件元信息与期望值不一致时，必须返回统一协议里的结构化错误：

```python
{
    "status": "error",
    "data": {
        "conflict": {
            "expected_mtime_ms": 1735212345000,
            "expected_size_bytes": 128,
            "current_mtime_ms": 1735212359000,
            "current_size_bytes": 156,
        }
    },
    "text": "文件在读取后已发生变化，请先重新读取再重试。",
    "stats": {"time_ms": 3},
    "context": {
        "cwd": ".",
        "params_input": {...},
        "path_resolved": "src/example.py",
    },
    "error": {
        "code": "CONFLICT",
        "message": "File has been modified since it was read."
    }
}
```

这里有两个要求：

- `error.code` 必须是 `CONFLICT`
- `data.conflict` 必须尽量带上期望值和当前值，方便 agent 判断下一步是否需要重新 `Read`

## 6. 与统一工具协议的关系

这一层继续复用已有的统一工具响应协议：

- `status`
- `data`
- `text`
- `stats`
- `context`
- `error`

协议层负责统一顶层形状；编辑安全层只负责：

- 定义 `Read -> Edit/Write` 的安全顺序
- 定义乐观锁字段
- 定义冲突时的 `data.conflict`

## 7. 为什么这一步不做 MultiEdit

`MultiEdit` 会带来两个额外复杂度：

- 多个锚点都必须基于同一份原始内容定位
- 多个替换之间还要做区间冲突检测

这些都不属于“最小安全编辑链路”的必要前置，所以这一步先不做。

## 8. 未来如何扩展到 MultiEdit

后续扩展 `MultiEdit` 时，乐观锁设计不需要推倒重来。

可以直接复用当前做法：

- 仍由 `Read` 返回 `file_mtime_ms` 和 `file_size_bytes`
- `MultiEdit` 仍接收 `expected_mtime_ms` 和 `expected_size_bytes`
- 先做一次锁校验，再在同一份原始内容上定位多个 `old_string`
- 若所有替换都合法，则一次性落盘

也就是说，`MultiEdit` 是“同一把锁下的多处原子编辑”，而不是另一套新的安全模型。

## 9. 当前最小实现范围

这一步只实现：

- `Read` 返回编辑需要的文件元信息
- `Edit` 支持已有文本文件的唯一锚点替换
- `Write` 支持新建文件和基于锁的覆盖写入
- 冲突时返回结构化 `CONFLICT`

这一步明确不实现：

- `MultiEdit`
- session 级自动锁注入
- diff 预览 / dry_run
- 更强的内容哈希锁
