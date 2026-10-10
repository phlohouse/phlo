"""Register trace queries and materialization inspection tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.run_analysis import (
    render_run_trace_tree as render_log_trace_tree_text,
)
from phlo_mcp.run_analysis import (
    render_span_tree,
    summarize_run_logs,
)
from phlo_mcp.tracing import CanonicalTracer, trace_tool


def _register_trace_tools(client: PhloApiClient, tracer: CanonicalTracer, mcp: FastMCP) -> None:
    @mcp.tool()
    @trace_tool
    def get_trace_spans(
        run_id: str | None = None,
        asset_key: str | None = None,
        job_name: str | None = None,
        service_name: str | None = None,
        span_name: str | None = None,
        status_code: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Get OTEL spans filtered by run, asset, job, service, status, name, or time."""
        with tracer.start_as_current_span("phlo.observability.trace_spans"):
            payload = client.get_trace_spans(
                run_id=run_id,
                asset_key=asset_key,
                job_name=job_name,
                service_name=service_name,
                span_name=span_name,
                status_code=status_code,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                cursor=cursor,
            )
            return {
                "api_base_url": client.api_base_url,
                "filters": _trace_filter_payload(
                    run_id=run_id,
                    asset_key=asset_key,
                    job_name=job_name,
                    service_name=service_name,
                    span_name=span_name,
                    status_code=status_code,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                ),
                "spans": payload,
            }

    @mcp.tool()
    @trace_tool
    def render_trace_spans_tree(
        run_id: str | None = None,
        asset_key: str | None = None,
        job_name: str | None = None,
        service_name: str | None = None,
        span_name: str | None = None,
        status_code: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Render a trace tree for spans matching observability filters."""
        with tracer.start_as_current_span("phlo.observability.trace_spans_tree"):
            payload = client.get_trace_spans(
                run_id=run_id,
                asset_key=asset_key,
                job_name=job_name,
                service_name=service_name,
                span_name=span_name,
                status_code=status_code,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
            spans = payload if isinstance(payload, list) else []
            label = run_id or asset_key or job_name or "filtered traces"
            return {
                "api_base_url": client.api_base_url,
                "filters": _trace_filter_payload(
                    run_id=run_id,
                    asset_key=asset_key,
                    job_name=job_name,
                    service_name=service_name,
                    span_name=span_name,
                    status_code=status_code,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                ),
                "span_count": len(spans),
                "tree": render_span_tree(label, spans),
            }


def _trace_filter_payload(**filters: Any) -> dict[str, Any]:
    return {key: value for key, value in filters.items() if value is not None}


def _register_materialization_tools(
    client: PhloApiClient, tracer: CanonicalTracer, mcp: FastMCP
) -> None:
    def _inspect_materialization_payload(
        asset_key_path: str,
        *,
        limit: int,
        log_limit: int,
        span_limit: int = 500,
    ) -> dict[str, Any]:
        events = client.get_materialization_history(asset_key_path, limit=limit)
        if not isinstance(events, list) or not events:
            return {
                "api_base_url": client.api_base_url,
                "asset_key_path": asset_key_path,
                "events": events,
                "latest_run": None,
            }
        latest = events[0]
        run_id = latest.get("run_id")
        log_payload = client.get_run_logs(run_id, limit=log_limit) if run_id else {"entries": []}
        entries = log_payload.get("entries", []) if isinstance(log_payload, dict) else []
        span_payload = client.get_run_trace_spans(run_id, limit=span_limit) if run_id else []
        spans = span_payload if isinstance(span_payload, list) else []
        return {
            "api_base_url": client.api_base_url,
            "asset_key_path": asset_key_path,
            "events": events,
            "latest_run": {
                "run_id": run_id,
                "log_summary": summarize_run_logs(run_id or "unknown", entries),
                "span_count": len(spans),
                "trace_tree": render_span_tree(run_id or "unknown", spans)
                if spans
                else render_log_trace_tree_text(run_id or "unknown", entries),
                "trace_source": "spans" if spans else "logs",
                "spans": spans,
            },
        }

    @mcp.tool()
    @trace_tool
    def inspect_materialization(
        asset_key_path: str, limit: int = 5, log_limit: int = 200
    ) -> dict[str, Any]:
        """Inspect recent materializations and correlated logs for the latest run."""
        with tracer.start_as_current_span("phlo.materialization.inspect"):
            return _inspect_materialization_payload(
                asset_key_path,
                limit=limit,
                log_limit=log_limit,
            )

    @mcp.tool()
    @trace_tool
    def get_asset_materialization_trace(
        asset_key_path: str, limit: int = 5, span_limit: int = 500
    ) -> dict[str, Any]:
        """Resolve the latest materialization for an asset and return its trace payload."""
        with tracer.start_as_current_span("phlo.materialization.trace"):
            payload = _inspect_materialization_payload(
                asset_key_path,
                limit=limit,
                log_limit=50,
                span_limit=span_limit,
            )
            latest_run = payload.get("latest_run") or {}
            return {
                "api_base_url": client.api_base_url,
                "asset_key_path": asset_key_path,
                "run_id": latest_run.get("run_id"),
                "trace_source": latest_run.get("trace_source"),
                "span_count": latest_run.get("span_count"),
                "spans": latest_run.get("spans", []),
                "events": payload.get("events", []),
            }

    @mcp.tool()
    @trace_tool
    def render_materialization_trace_tree(
        asset_key_path: str, limit: int = 5, span_limit: int = 500
    ) -> dict[str, Any]:
        """Render the latest materialization trace tree for an asset key path."""
        with tracer.start_as_current_span("phlo.materialization.trace_tree"):
            payload = _inspect_materialization_payload(
                asset_key_path,
                limit=limit,
                log_limit=50,
                span_limit=span_limit,
            )
            latest_run = payload.get("latest_run") or {}
            return {
                "api_base_url": client.api_base_url,
                "asset_key_path": asset_key_path,
                "run_id": latest_run.get("run_id"),
                "trace_source": latest_run.get("trace_source"),
                "span_count": latest_run.get("span_count"),
                "tree": latest_run.get("trace_tree"),
            }

    @mcp.tool()
    @trace_tool
    def render_run_trace_tree(run_id: str, limit: int = 200) -> dict[str, Any]:
        """Render a run execution tree using real spans when available, else logs."""
        with tracer.start_as_current_span("phlo.run.trace_tree"):
            span_payload = client.get_run_trace_spans(run_id, limit=max(limit, 500))
            spans = span_payload if isinstance(span_payload, list) else []
            if spans:
                return {
                    "api_base_url": client.api_base_url,
                    "run_id": run_id,
                    "tree": render_span_tree(run_id, spans),
                    "span_count": len(spans),
                    "source": "spans",
                }
            payload = client.get_run_logs(run_id, limit=limit)
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            return {
                "api_base_url": client.api_base_url,
                "run_id": run_id,
                "tree": render_log_trace_tree_text(run_id, entries, limit=limit),
                "entry_count": len(entries),
                "source": "logs",
            }
