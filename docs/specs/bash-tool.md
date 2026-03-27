# Bash Tool

## 1. 目标语义

Bash 工具的目标不是替代现有只读工具，也不是提供一个无边界的终端。

它在代码代理里的最小职责是：

- 让 agent 能在当前项目工作区里执行**本地、非交互**命令
- 支持像 `pytest`、`git status`、`uv run ...` 这类命令型任务
- 把执行结果统一返回为结构化对象，而不是裸文本

因此，这一版 Bash 工具更像“受约束的命令执行器”，而不是“完整 shell 环境”。

## 2. 为什么这里优先使用本地 `function_tool`

本地 OpenAI Agents SDK 确实已经提供了 `ShellTool` / `LocalShellTool` 能力，但当前项目这一阶段更适合使用本地 `function_tool` 自行实现最小 Bash 工具。

原因是：

- 当前项目已经统一采用 `function_tool + 统一响应协议 + common.py helper` 这条路径
- 当前项目需要的是**项目工作区语义**，包括固定 `cwd`、轻量安全规则和统一协议封装
- SDK 自带 shell 能力更偏通用 shell 执行与 approval 流程，不直接落在我们现有的 `ToolResponse` 信封上
- 这一版 Bash 只需要最小单命令执行，不需要引入更重的 shell tool 生命周期

所以当前方案是：

- 命令执行能力自己实现
- 参数 schema 继续交给 `function_tool`
- 安全边界、协议层、共享 helper 继续沿用本项目既有结构

后续如果项目真的需要更强的 shell 生命周期或 approval 机制，再评估是否迁移到 SDK 的 shell tool。

## 3. 与统一响应协议的衔接

这一版 Bash 工具继续返回统一协议：

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

其中 `data` 最小包含：

- `stdout`
- `stderr`
- `exit_code`

当前实现还建议补充：

- `command`
- `directory`
- `timed_out`
- `truncated`

状态语义：

- `success`
  - 命令执行完成
  - `exit_code == 0`
  - 未超时
- `partial`
  - 结果可用但有折扣
  - 例如退出码非 0、超时但已有部分输出、输出被截断
- `error`
  - 参数非法
  - 目录越界
  - 命令被安全规则阻止
  - 超时且没有任何可复用输出
  - 进程执行异常

## 4. 与轻量工具协调层的衔接

当前 Bash 工具应直接接在现有轻量工具协调层上：

- `src/tools/bash_tool.py`
  - 放 Bash 工具实现和 `function_tool` 包装
- `src/tools/common.py`
  - 复用路径解析、上下文构造、错误包装、统计 helper
- `src/tools/__init__.py`
  - 导出 `BASH_TOOLS`
  - 拼进 `AGENT_TOOLS`

这意味着 Bash 工具和现有工具保持同一种接入方式：

- schema 由 SDK 自动生成
- 注册通过 `AGENT_TOOLS`
- 协议层和共享状态走同一套 helper

## 5. 最小安全边界

这一版 Bash 工具只做最小非交互版本，安全边界收得比较硬：

- 默认只在当前项目工作区内运行
- `directory` 必须解析到项目根目录以内
- 不支持 PTY
- 不支持后台任务
- 不支持命令内部再切换目录，使用 `directory` 参数表达工作目录

同时，当前会阻止几类明显不适合的命令：

- 交互命令
  - `vim`、`vi`、`nano`、`less`、`more`、`top`、`htop`、`watch`、`tmux`、`screen`
- 网络命令
  - `curl`、`wget`、`ssh`、`scp`、`sftp`、`ftp`
- 权限提升与破坏性系统命令
  - `sudo`、`su`、`doas`、`mkfs`、`fdisk`、`dd`、`shutdown`、`reboot`、`poweroff`、`halt`
- 应优先交给专用工具的读/搜/列命令
  - `ls`、`cat`、`head`、`tail`、`grep`、`find`、`rg`

这样做的原因有两点：

- 保持工具职责清晰，避免模型把“找证据”全塞进一条黑盒 shell
- 把 Bash 保持为“执行本地命令”的补充能力，而不是万能入口

需要明确的是：

- 这仍然只是**最小规则约束**
- 不是完整沙箱
- 也不尝试做完整 shell 语法分析

## 6. 参数与行为

当前最小参数建议为：

- `command: str`
- `directory: str = "."`
- `timeout_ms: int = 120000`

行为约束：

- `command` 不能为空
- `directory` 必须是项目工作区内的目录
- `timeout_ms` 必须在合理范围内
- 环境变量继承父进程，并额外注入 `MYCODEAGENT=1`

## 7. 错误表达

这一版 Bash 工具继续用统一错误结构表达失败。

推荐错误码：

- `INVALID_PARAM`
- `ACCESS_DENIED`
- `NOT_FOUND`
- `COMMAND_BLOCKED`
- `TIMEOUT`
- `EXECUTION_ERROR`

若是超时但已有部分输出，优先返回：

- `status = "partial"`
- `data.stdout / data.stderr` 中保留已捕获的部分
- `data.exit_code = None`
- `data.timed_out = True`

这样 agent 至少还能利用部分诊断信息。

## 8. 当前版本还不做什么

这一版刻意不做：

- PTY / 交互终端
- 多条命令批量执行
- shell approval 流程
- 完整命令 AST 解析
- 更强的沙箱
- 输出落盘与超长 observation 截断协同
- 与 session 的命令历史治理

这一步的目标只是先把“最小、安全、可复用”的 Bash 工具接进当前工具层。
