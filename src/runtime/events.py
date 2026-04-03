from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class RuntimeEventBuilder:
    # 这层只负责把一轮 run 中发生的事情包装成统一事件信封。
    run_id: str
    session_id: str
    seq: int = 0

    def build(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        return {
            "event_id": f"evt-{uuid4().hex[:12]}",
            "run_id": self.run_id,
            "session_id": self.session_id,
            "seq": self.seq,
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "type": event_type,
            "payload": payload,
        }


def summarize_tool_call(item: Any) -> dict[str, Any]:
    # SDK 的 tool call item 类型很多，这里只抽 UI 需要的最小公共字段。
    raw_item = getattr(item, "raw_item", None)
    tool_name = _extract_tool_name(raw_item) or "unknown_tool"
    arguments = _extract_tool_arguments(raw_item)
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "summary": f"{tool_name}({arguments})" if arguments else tool_name,
    }


def summarize_tool_result(item: Any) -> dict[str, Any]:
    # UI 默认只展示摘要，但事件层仍保留完整 result，后续 detail 面板可直接复用。
    output = getattr(item, "output", None)
    tool_name = _extract_tool_name(getattr(item, "raw_item", None)) or "unknown_tool"
    status = "success"
    summary = "工具执行完成。"
    full_output_path = None
    exit_code = None

    if isinstance(output, dict):
        status = str(output.get("status") or "success")
        summary = _short_text(str(output.get("text") or "")) or "工具执行完成。"
        data = output.get("data")
        if isinstance(data, dict):
            truncation = data.get("truncation")
            if isinstance(truncation, dict):
                full_output_path = truncation.get("full_output_path")
            exit_code = data.get("exit_code")
    elif output is not None:
        summary = _short_text(str(output)) or "工具执行完成。"

    return {
        "tool_name": tool_name,
        "status": status,
        "summary": summary,
        "exit_code": exit_code,
        "full_output_path": full_output_path,
        "result": output,
    }


def _extract_tool_name(raw_item: Any) -> str | None:
    if isinstance(raw_item, dict):
        name = raw_item.get("name") or raw_item.get("tool_name")
        return str(name) if name else None
    name = getattr(raw_item, "name", None) or getattr(raw_item, "tool_name", None)
    return str(name) if name else None


def _extract_tool_arguments(raw_item: Any) -> str:
    if isinstance(raw_item, dict):
        return _short_text(str(raw_item.get("arguments") or "")).strip()
    arguments = getattr(raw_item, "arguments", None)
    return _short_text(str(arguments or "")).strip()


def _short_text(text: str, *, limit: int = 120) -> str:
    # 事件层的摘要默认只保留一小段，避免 tool_result 把 CLI/TUI 直接刷屏。
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."
