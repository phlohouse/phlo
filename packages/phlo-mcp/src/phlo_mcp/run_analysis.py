"""Helpers for materialization and run-level observability analysis.

Pure functions over log entries and OTEL span rows: they summarize correlated
logs and render best-effort trace/span trees as text. Missing or malformed
fields degrade to "unknown" placeholders instead of raising, so a partial
telemetry stream still yields a usable summary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def summarize_run_logs(
    run_id: str,
    entries: list[dict[str, Any]],
    *,
    max_messages: int = 10,
) -> dict[str, Any]:
    """Build a compact summary of correlated run logs."""
    level_counts = Counter(entry.get("level", "unknown") for entry in entries)
    service_counts = Counter(
        (entry.get("metadata") or {}).get("service") or "unknown" for entry in entries
    )
    trace_ids = sorted(
        {
            (entry.get("metadata") or {}).get("trace_id")
            for entry in entries
            if (entry.get("metadata") or {}).get("trace_id")
        }
    )
    asset_keys = sorted(
        {
            (entry.get("metadata") or {}).get("asset_key")
            for entry in entries
            if (entry.get("metadata") or {}).get("asset_key")
        }
    )
    messages = [
        {
            "timestamp": entry.get("timestamp"),
            "level": entry.get("level"),
            "service": (entry.get("metadata") or {}).get("service"),
            "message": entry.get("message"),
        }
        for entry in entries[:max_messages]
    ]
    return {
        "run_id": run_id,
        "entry_count": len(entries),
        "level_counts": dict(level_counts),
        "service_counts": dict(service_counts),
        "trace_ids": trace_ids,
        "asset_keys": asset_keys,
        "messages": messages,
    }


def render_run_trace_tree(run_id: str, entries: list[dict[str, Any]], *, limit: int = 40) -> str:
    """Render a best-effort execution tree from correlated run logs."""
    lines = [f"Run {run_id}"]
    if not entries:
        lines.append("└─ no logs found")
        return "\n".join(lines)

    trace_ids = [
        (entry.get("metadata") or {}).get("trace_id")
        for entry in entries
        if (entry.get("metadata") or {}).get("trace_id")
    ]
    unique_trace_ids = sorted(set(trace_ids))
    if unique_trace_ids:
        for trace_index, trace_id in enumerate(unique_trace_ids):
            trace_entries = [
                entry
                for entry in entries
                if (entry.get("metadata") or {}).get("trace_id") == trace_id
            ]
            is_last_trace = trace_index == len(unique_trace_ids) - 1
            trace_prefix = "└─" if is_last_trace else "├─"
            lines.append(f"{trace_prefix} trace {trace_id[:16]}")
            _append_entry_lines(
                lines,
                trace_entries,
                prefix="   " if is_last_trace else "│  ",
                limit=limit,
            )
    else:
        lines.append("└─ logs")
        _append_entry_lines(lines, entries, prefix="   ", limit=limit)
    return "\n".join(lines)


def render_span_tree(run_id: str, spans: list[dict[str, Any]]) -> str:
    """Render a proper span tree from OTEL span rows."""
    lines = [f"Run {run_id}"]
    if not spans:
        lines.append("└─ no spans found")
        return "\n".join(lines)

    spans_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        trace_id = span.get("trace_id") or "unknown"
        spans_by_trace[trace_id].append(span)

    trace_ids = sorted(spans_by_trace)
    for trace_index, trace_id in enumerate(trace_ids):
        trace_spans = spans_by_trace[trace_id]
        children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        span_ids = {span.get("span_id") for span in trace_spans if span.get("span_id")}
        for span in trace_spans:
            parent_id = span.get("parent_span_id") or None
            children[parent_id].append(span)
        for child_list in children.values():
            child_list.sort(key=lambda item: item.get("timestamp") or "")

        roots = [
            span
            for span in trace_spans
            if not span.get("parent_span_id") or span.get("parent_span_id") not in span_ids
        ]
        roots.sort(key=lambda item: item.get("timestamp") or "")
        trace_prefix = "└─" if trace_index == len(trace_ids) - 1 else "├─"
        lines.append(f"{trace_prefix} trace {trace_id[:16]}")
        child_prefix = "   " if trace_index == len(trace_ids) - 1 else "│  "
        for root_index, root in enumerate(roots):
            _append_span_lines(
                lines,
                root,
                children,
                prefix=child_prefix,
                is_last=root_index == len(roots) - 1,
            )
    return "\n".join(lines)


def _append_entry_lines(
    lines: list[str],
    entries: list[dict[str, Any]],
    *,
    prefix: str,
    limit: int,
) -> None:
    # Loki responses are normalized newest-first; render the most recent entries
    # in chronological order so the tree reads top-to-bottom.
    display_entries = list(reversed(entries[:limit]))
    for index, entry in enumerate(display_entries):
        is_last = index == len(display_entries) - 1
        connector = "└─" if is_last else "├─"
        metadata = entry.get("metadata") or {}
        service = metadata.get("service") or "unknown"
        function = metadata.get("function")
        duration_ms = metadata.get("durationMs")
        context_bits = [service]
        if function:
            context_bits.append(function)
        context = " / ".join(context_bits)
        suffix = f" ({duration_ms}ms)" if duration_ms else ""
        message = entry.get("message") or ""
        lines.append(
            f"{prefix}{connector} [{entry.get('level', 'info')}] {context}: {message}{suffix}"
        )


def _append_span_lines(
    lines: list[str],
    span: dict[str, Any],
    children: dict[str | None, list[dict[str, Any]]],
    *,
    prefix: str,
    is_last: bool,
) -> None:
    connector = "└─" if is_last else "├─"
    service = (
        span.get("service_name")
        or (span.get("resource_attributes") or {}).get("service.name")
        or "unknown"
    )
    kind = _normalize_span_kind(span.get("span_kind"))
    status = _normalize_status_code(span.get("status_code"))
    duration_ms = span.get("duration_ms")
    duration_suffix = f" ({duration_ms}ms)" if duration_ms is not None else ""
    detail_suffix = _span_detail_suffix(span)
    lines.append(
        f"{prefix}{connector} {service} / {span.get('span_name')} [{kind} {status}]"
        f"{duration_suffix}{detail_suffix}"
    )
    span_id = span.get("span_id")
    span_children = children.get(span_id, [])
    child_prefix = prefix + ("   " if is_last else "│  ")
    for index, child in enumerate(span_children):
        _append_span_lines(
            lines,
            child,
            children,
            prefix=child_prefix,
            is_last=index == len(span_children) - 1,
        )


def _normalize_span_kind(value: Any) -> str:
    if not value:
        return "internal"
    return str(value).replace("SPAN_KIND_", "").lower()


def _normalize_status_code(value: Any) -> str:
    if not value:
        return "unset"
    normalized = str(value).replace("STATUS_CODE_", "").lower()
    if normalized == "ok":
        return "ok"
    if normalized == "error":
        return "error"
    return normalized


def _span_detail_suffix(span: dict[str, Any]) -> str:
    span_attributes = span.get("span_attributes") or {}
    resource_attributes = span.get("resource_attributes") or {}
    details: list[str] = []
    for label, key in (
        ("stage", "phlo.stage"),
        ("asset", "phlo.asset_key"),
        ("job", "phlo.job_name"),
        ("operation", "phlo.operation"),
    ):
        value = span_attributes.get(key)
        if value:
            details.append(f"{label}={value}")
    service_version = resource_attributes.get("service.version")
    if service_version:
        details.append(f"service.version={service_version}")
    if not details:
        return ""
    return " {" + ", ".join(details) + "}"
