"""Local tracing utilities for phlo-mcp.

Spans are exported as JSON lines to the file named by PHLO_MCP_TRACE_FILE
using a SimpleSpanProcessor; tracing stays disabled when no file is
configured. load_spans/render_trace_tree read that same file back for
local debugging.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

_TRACE_FILE_ENV = "PHLO_MCP_TRACE_FILE"
_CONFIGURED_PATH: str | None = None


class JsonLineSpanExporter(SpanExporter):
    """Write spans to a JSONL file for local tracing/debug workflows."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Append readable spans to the JSONL trace file and report success."""
        with self._path.open("a", encoding="utf-8") as handle:
            for span in spans:
                parent_id = None
                if span.parent is not None:
                    parent_id = f"{span.parent.span_id:016x}"
                payload = {
                    "name": span.name,
                    "context": {
                        "trace_id": f"{span.context.trace_id:032x}",
                        "span_id": f"{span.context.span_id:016x}",
                        "parent_id": parent_id,
                    },
                    "start_time_ns": span.start_time,
                    "end_time_ns": span.end_time,
                    "attributes": {key: value for key, value in (span.attributes or {}).items()},
                    "status": {
                        "code": getattr(
                            span.status.status_code, "name", str(span.status.status_code)
                        ),
                        "description": span.status.description,
                    },
                }
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """No-op shutdown; spans are flushed by the simple span processor."""
        return None


def configure_tracing(
    *, service_name: str = "phlo-mcp", trace_file: str | None = None
) -> str | None:
    """Configure local tracing if a trace file is configured."""
    global _CONFIGURED_PATH
    resolved_trace_file = trace_file or os.environ.get(_TRACE_FILE_ENV)
    # Tracing is configured at most once per process. The global tracer provider
    # cannot be swapped after installation, so a call with a different path keeps
    # whichever file won the race rather than silently redirecting spans.
    if _CONFIGURED_PATH == resolved_trace_file:
        return resolved_trace_file
    if _CONFIGURED_PATH is not None and _CONFIGURED_PATH != resolved_trace_file:
        return _CONFIGURED_PATH
    if not resolved_trace_file:
        return None

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "phlo",
            "phlo.package": "phlo-mcp",
            "phlo.runtime": "python",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(JsonLineSpanExporter(resolved_trace_file)))
    trace.set_tracer_provider(provider)
    _CONFIGURED_PATH = resolved_trace_file
    return resolved_trace_file


def load_spans(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load span dicts from a JSONL trace file, empty when missing."""
    trace_path = Path(path)
    if not trace_path.exists():
        return []
    return [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line
    ]


def render_trace_tree(path: str | os.PathLike[str]) -> str:
    """Render a compact tree view for spans written by JsonLineSpanExporter."""
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
