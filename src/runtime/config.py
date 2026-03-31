from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """CLI 入口所需的最小运行时配置。"""

    api_key: str
    model: str
    light_model: str
    base_url: str | None = None


def normalize_api_key(raw_api_key: str) -> str:
    """SDK 需要原始 token，这里兼容去掉常见的 `Bearer ` 前缀。"""
    api_key = raw_api_key.strip()
    if api_key[:7].lower() == "bearer ":
        return api_key[7:].strip()
    return api_key


def load_runtime_config() -> RuntimeConfig:
    """读取单代理 CLI 运行所需的最小环境变量集合。"""
    load_dotenv()

    api_key = normalize_api_key(os.getenv("OPENAI_API_KEY") or "")
    model = (os.getenv("OPENAI_MODEL") or "gpt-5.2").strip() or "gpt-5.2"
    light_model = (os.getenv("LIGHT_OPENAI_MODEL") or "gpt-5").strip() or "gpt-5"
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or None

    if not api_key:
        raise SystemExit("Configuration error: Missing required env var: OPENAI_API_KEY")

    return RuntimeConfig(
        api_key=api_key,
        model=model,
        light_model=light_model,
        base_url=base_url,
    )
