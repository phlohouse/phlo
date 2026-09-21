"""Canonical MCP operation scopes with a compatible JSONL debug drain."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from contextlib import AbstractContextManager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import uuid4

import phlo.telemetry as phlo_observe

_TRACE_FILE_ENV = "PHLO_MCP_TRACE_FILE"
_CONFIGURED_PATH: str | None = None
_ACTIVE_SPANS: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
    "phlo_mcp_active_spans", default=()
)


def configure_tracing(
    *, service_name: str = "phlo-mcp", trace_file: str | None = None
) -> str | None:
    """Configure local tracing if a trace file is configured."""
    global _CONFIGURED_PATH
    resolved_trace_file = trace_file or os.environ.get(_TRACE_FILE_ENV)
    # The debug drain is configured at most once per process. A later call with
    # a different path keeps the first destination rather than splitting one
    # operation tree across files.
    if _CONFIGURED_PATH == resolved_trace_file:
        return resolved_trace_file
    if _CONFIGURED_PATH is not None and _CONFIGURED_PATH != resolved_trace_file:
        return _CONFIGURED_PATH
    if not resolved_trace_file:
        return None

    Path(resolved_trace_file).parent.mkdir(parents=True, exist_ok=True)
    _CONFIGURED_PATH = resolved_trace_file
    return resolved_trace_file


class _OperationScope(AbstractContextManager["_OperationScope"]):
    """Observe-core operation plus the legacy local debug-drain record."""

    def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.name = name
        self.attributes = attributes or {}
        self._scope: Any = None
        self._started_at = 0
        self._span: dict[str, Any] | None = None
        self._token: Any = None

    def __enter__(self) -> "_OperationScope":
        from time import time_ns

        self._started_at = time_ns()
        parent = _ACTIVE_SPANS.get()
        trace_id = parent[-1]["context"]["trace_id"] if parent else uuid4().hex
        self._span = {
            "name": self.name,
            "context": {
                "trace_id": trace_id,
                "span_id": uuid4().hex[:16],
                "parent_id": parent[-1]["context"]["span_id"] if parent else None,
            },
            "start_time_ns": self._started_at,
            "attributes": self.attributes,
        }
        self._token = _ACTIVE_SPANS.set((*parent, self._span))
        self._scope = phlo_observe.observe(self.name)
        event = self._scope.__enter__()
        if self.attributes:
            event.set(**self.attributes)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        from time import time_ns

        if self._span is not None:
            self._span["end_time_ns"] = time_ns()
            self._span["status"] = {
                "code": "ERROR" if exc_type else "UNSET",
                "description": str(exc) if exc else None,
            }
            _write_debug_span(self._span)
        if self._token is not None:
            _ACTIVE_SPANS.reset(self._token)
        return self._scope.__exit__(exc_type, exc, traceback)


class CanonicalTracer:
    """Compatibility façade for MCP nesting while observe-core owns activity."""

    def start_as_current_span(
        self, name: str, *, attributes: dict[str, Any] | None = None
    ) -> _OperationScope:
        return _OperationScope(name, attributes)


def get_tracer() -> CanonicalTracer:
    """Return the MCP operation façade; no OTel provider is installed here."""
    return CanonicalTracer()


def _write_debug_span(span: dict[str, Any]) -> None:
    if not _CONFIGURED_PATH:
        return
    with Path(_CONFIGURED_PATH).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(span, sort_keys=True, default=str) + "\n")


def load_spans(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load span dicts from a JSONL trace file, empty when missing."""
    trace_path = Path(path)
    if not trace_path.exists():
        return []
    return [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line
    ]


def render_trace_tree(path: str | os.PathLike[str]) -> str:
    """Render a compact tree view for records written by the debug drain."""
    spans = load_spans(path)
    if not spans:
        return "(no spans captured)"

    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        by_trace[span["context"]["trace_id"]].append(span)

    lines: list[str] = []
    for trace_id, trace_spans in sorted(
        by_trace.items(), key=lambda item: min(span["start_time_ns"] for span in item[1])
    ):
        children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for span in trace_spans:
            children[span["context"]["parent_id"]].append(span)
        for node_list in children.values():
            node_list.sort(key=lambda span: span["start_time_ns"])

        root_spans = children[None]
        total_ms = sum(_duration_ms(span) for span in root_spans)
        lines.append(f"Trace {trace_id[:8]} ({total_ms:.1f}ms)")
        for index, root in enumerate(root_spans):
            _render_span(root, children, lines, prefix="", is_last=index == len(root_spans) - 1)
    return "\n".join(lines)


def _render_span(
    span: dict[str, Any],
    children: dict[str | None, list[dict[str, Any]]],
    lines: list[str],
    *,
    prefix: str,
    is_last: bool,
) -> None:
    connector = "└─ " if is_last else "├─ "
    attrs = span.get("attributes", {})
    suffix_parts: list[str] = []
    tool_name = attrs.get("mcp.tool.name")
    url = attrs.get("url.full") or attrs.get("http.url")
    if tool_name:
        suffix_parts.append(f"tool={tool_name}")
    if url:
        suffix_parts.append(str(url))
    suffix = f" [{', '.join(suffix_parts)}]" if suffix_parts else ""
    lines.append(f"{prefix}{connector}{span['name']} {_duration_ms(span):.1f}ms{suffix}")

    child_prefix = prefix + ("   " if is_last else "│  ")
    span_children = children.get(span["context"]["span_id"], [])
    for index, child in enumerate(span_children):
        _render_span(
            child,
            children,
            lines,
            prefix=child_prefix,
            is_last=index == len(span_children) - 1,
        )


def _duration_ms(span: dict[str, Any]) -> float:
    return (span["end_time_ns"] - span["start_time_ns"]) / 1_000_000
