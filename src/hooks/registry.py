from __future__ import annotations

from collections import defaultdict

from src.hooks.model import HookCallable, HookContext, HookEvent, HookResult


class HookRegistry:
    """维护不同 hook 事件对应的回调列表。"""

    def __init__(self) -> None:
        """初始化空的 hook 注册表。"""
        self._hooks: dict[HookEvent, list[HookCallable]] = defaultdict(list)

    def register(self, event: HookEvent, hook: HookCallable) -> None:
        """把一个 hook 按注册顺序挂到指定事件上。"""
        self._hooks[event].append(hook)

    def run(self, event: HookEvent, context: HookContext) -> HookResult:
        """按注册顺序执行事件上的 hook，并返回聚合结果。"""
        for hook in self._hooks.get(event, []):
            result = hook(context)
            if result is None:
                continue
            if result.stop:
                return result
        return HookResult()
