"""集中导出 agent hook 的事件模型和注册表。"""

from src.hooks.builtins import build_default_hook_registry
from src.hooks.model import HookCallable, HookContext, HookEvent, HookResult
from src.hooks.registry import HookRegistry

__all__ = [
    "build_default_hook_registry",
    "HookCallable",
    "HookContext",
    "HookEvent",
    "HookRegistry",
    "HookResult",
]
