from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_DIR = "artifacts/traces"
HTML_RESULT_DATA_PREVIEW_CHARS = 300
_BOOL_TRUE_VALUES = {"1", "true", "yes", "on"}
_BOOL_FALSE_VALUES = {"0", "false", "no", "off"}
_BEARER_TOKEN_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9]+\b")


@dataclass(frozen=True, slots=True)
class TraceConfig:
    enabled: bool
    trace_dir: Path
    sanitize: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in _BOOL_TRUE_VALUES:
        return True
    if normalized in _BOOL_FALSE_VALUES:
        return False
    raise ValueError(f"{name} 必须是 true/false。")


def load_trace_config() -> TraceConfig:
    trace_dir_raw = os.environ.get("TRACE_DIR", DEFAULT_TRACE_DIR).strip() or DEFAULT_TRACE_DIR
    trace_dir = Path(trace_dir_raw)
    if not trace_dir.is_absolute():
        trace_dir = PROJECT_ROOT / trace_dir
    return TraceConfig(
        enabled=_read_bool_env("TRACE_ENABLED", True),
        trace_dir=trace_dir.resolve(),
        sanitize=_read_bool_env("TRACE_SANITIZE", True),
    )


def _normalize_usage(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump(mode="python")

    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        total_tokens = usage.get("total_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
        completion_tokens = getattr(
            usage,
            "completion_tokens",
            getattr(usage, "output_tokens", None),
        )
        total_tokens = getattr(usage, "total_tokens", None)

    if not all(isinstance(value, int) for value in [prompt_tokens, completion_tokens, total_tokens]):
        return None

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def extract_usage_from_raw_event_data(raw_event_data: Any) -> dict[str, int] | None:
    # 第三方 provider 可能只在完成事件里带 usage，所以这里统一做一次鸭子类型提取。
    response = getattr(raw_event_data, "response", None)
    usage = getattr(response, "usage", None) if response is not None else None
    normalized = _normalize_usage(usage)
    if normalized is not None:
        return normalized
    return _normalize_usage(getattr(raw_event_data, "usage", None))


def _sanitize_string(value: str) -> str:
    sanitized = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", value)
    sanitized = _OPENAI_TOKEN_RE.sub("sk-[REDACTED]", sanitized)
    return sanitized


def sanitize_trace_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_trace_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitize_trace_payload(item)
            for key, item in value.items()
        }
    return value


class LocalTraceLogger:
    def __init__(self, *, session_id: str, config: TraceConfig | None = None) -> None:
        self._config = config or load_trace_config()
        self._session_id = session_id
        self._trace_path = self._config.trace_dir / f"trace-{session_id}.jsonl"
        self._html_path = self._config.trace_dir / f"trace-{session_id}.html"
        self._run_steps: dict[str, int] = {}
        self._run_count = 0
        self._tools_used: set[str] = set()
        self._records: list[dict[str, Any]] = []
        self._total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def trace_path(self) -> str:
        try:
            return str(self._trace_path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(self._trace_path)

    @property
    def html_path(self) -> str:
        try:
            return str(self._html_path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(self._html_path)

    def _next_step(self, run_id: str | None) -> int:
        if run_id is None:
            return 0
        next_step = self._run_steps.get(run_id, 0) + 1
        self._run_steps[run_id] = next_step
        return next_step

    def _write_event(self, *, run_id: str | None, event: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        event_payload = sanitize_trace_payload(payload) if self._config.sanitize else payload
        record = {
            "ts": _utc_now(),
            "session_id": self._session_id,
            "run_id": run_id,
            "step": self._next_step(run_id),
            "event": event,
            "payload": event_payload,
        }
        self._records.append(record)
        with self._trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        # HTML 审计页和 JSONL 保持同 session 同步更新，便于随时回查当前运行状态。
        self._write_html_snapshot()

    def _write_html_snapshot(self) -> None:
        self._html_path.parent.mkdir(parents=True, exist_ok=True)
        self._html_path.write_text(self._render_html(), encoding="utf-8")

    def _render_html(self) -> str:
        # HTML 只做最小审计页，不追求复杂 UI；重点是让事件时间线和工具结果可读。
        event_blocks = "\n".join(self._render_event_block(record) for record in self._records)
        total_usage = html.escape(json.dumps(self._total_usage, ensure_ascii=False))
        tools_used = html.escape(", ".join(sorted(self._tools_used)) or "None")
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Trace {html.escape(self._session_id)}</title>
  <style>
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 24px; background: #f7f7f5; color: #1f2328; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta {{ margin-bottom: 24px; padding: 16px; background: #fff; border: 1px solid #d0d7de; border-radius: 8px; }}
    .event {{ margin-bottom: 12px; background: #fff; border: 1px solid #d0d7de; border-radius: 8px; }}
    .event summary {{ cursor: pointer; padding: 12px 16px; font-weight: 600; }}
    .payload {{ margin: 0; padding: 0 16px 16px; white-space: pre-wrap; word-break: break-word; }}
    .muted {{ color: #656d76; }}
  </style>
</head>
<body>
  <section class="meta">
    <h1>Trace Audit</h1>
    <p class="muted">session_id: {html.escape(self._session_id)}</p>
    <p class="muted">events: {len(self._records)} | runs: {self._run_count}</p>
    <p class="muted">tools_used: {tools_used}</p>
    <p class="muted">total_usage: {total_usage}</p>
  </section>
  <section>
    <h2>Events</h2>
    {event_blocks}
  </section>
</body>
</html>
"""

    def _render_event_block(self, record: dict[str, Any]) -> str:
        title = (
            f"step {record['step']} · {record['event']} · "
            f"{record['ts']} · run={record['run_id'] or '-'}"
        )
        payload_text = html.escape(
            json.dumps(
                self._build_html_payload(record),
                ensure_ascii=False,
                indent=2,
            )
        )
        return (
            "<details class=\"event\" open>"
            f"<summary>{html.escape(title)}</summary>"
            f"<pre class=\"payload\">{payload_text}</pre>"
            "</details>"
        )

    def _build_html_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        # JSONL 保留完整结果；HTML 只对最容易膨胀的 tool_result.data 做审计预览。
        payload = json.loads(json.dumps(record["payload"], ensure_ascii=False))
        if record["event"] != "tool_result":
            return payload
        result = payload.get("result")
        if not isinstance(result, dict):
            return payload
        data = result.get("data")
        if data is None:
            return payload
        serialized_data = json.dumps(data, ensure_ascii=False)
        if len(serialized_data) <= HTML_RESULT_DATA_PREVIEW_CHARS:
            return payload
        result["data"] = {
            "_preview": serialized_data[:HTML_RESULT_DATA_PREVIEW_CHARS] + "...",
            "_truncated": True,
        }
        return payload

    def start_run(self, *, user_input: str, model: str) -> str:
        run_id = f"run-{uuid4().hex[:8]}"
        self._run_count += 1
        self._write_event(run_id=run_id, event="run_start", payload={"model": model})
        self._write_event(run_id=run_id, event="user_input", payload={"text": user_input})
        return run_id

    def log_context_build(self, *, run_id: str, payload: dict[str, Any]) -> None:
        self._write_event(run_id=run_id, event="context_build", payload=payload)

    def log_tool_call(self, *, run_id: str, tool_name: str, args: dict[str, Any]) -> None:
        self._tools_used.add(tool_name)
        self._write_event(
            run_id=run_id,
            event="tool_call",
            payload={"tool": tool_name, "args": args},
        )

    def log_tool_result(self, *, run_id: str, tool_name: str, result: dict[str, Any]) -> None:
        self._tools_used.add(tool_name)
        self._write_event(
            run_id=run_id,
            event="tool_result",
            payload={"tool": tool_name, "result": result},
        )

    def log_error(
        self,
        *,
        run_id: str | None,
        stage: str,
        message: str,
        **payload: Any,
    ) -> None:
        error_payload = {"stage": stage, "message": message, **payload}
        self._write_event(run_id=run_id, event="error", payload=error_payload)

    def log_finish(
        self,
        *,
        run_id: str,
        final_output: str,
        usage: dict[str, int] | None,
    ) -> None:
        self._write_event(
            run_id=run_id,
            event="finish",
            payload={
                "final": final_output,
                "usage": usage,
            },
        )

    def log_run_end(self, *, run_id: str, status: str, usage: dict[str, int] | None) -> None:
        if usage is not None:
            for key in self._total_usage:
                self._total_usage[key] += usage.get(key, 0)
        self._write_event(
            run_id=run_id,
            event="run_end",
            payload={"status": status, "usage": usage},
        )

    def log_session_summary(self) -> None:
        self._write_event(
            run_id=None,
            event="session_summary",
            payload={
                "runs": self._run_count,
                "tools_used": sorted(self._tools_used),
                "total_usage": dict(self._total_usage),
            },
        )


def build_trace_logger(
    session_id: str,
    *,
    trace_dir: Path | None = None,
    enabled: bool | None = None,
) -> LocalTraceLogger | None:
    # tracing 配置默认仍从环境变量读取，但 session 层可以覆盖目录和开关。
    config = load_trace_config()
    if trace_dir is not None:
        config = replace(config, trace_dir=trace_dir)
    if enabled is not None:
        config = replace(config, enabled=enabled)
    if not config.enabled:
        return None
    return LocalTraceLogger(session_id=session_id, config=config)
