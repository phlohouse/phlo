"""MCP server exposing curated Phlo observability tools.

Builds a FastMCP instance whose tools and resources delegate to one shared
PhloApiClient; configuration comes from the environment, OpenTelemetry
tracing is configured at creation, and package docs are read from
docs/packages/ under the project root.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from opentelemetry import trace

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.config import McpConfig, config_from_env
from phlo_mcp.models import ToolContract
from phlo_mcp.run_analysis import (
    render_run_trace_tree as render_log_trace_tree_text,
    render_span_tree,
    summarize_run_logs,
)
from phlo_mcp.tracing import configure_tracing


def _read_package_doc(package_name: str) -> str:
    safe_name = package_name.removesuffix(".md")
    if "/" in safe_name or "\\" in safe_name or safe_name in {"", ".", ".."}:
        return f"# {package_name}\n\nPackage documentation not found.\n"
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "docs" / "packages" / f"{safe_name}.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return f"# {package_name}\n\nPackage documentation not found.\n"


def create_server(config: McpConfig | None = None) -> FastMCP:
    """Create a configured FastMCP server instance."""
    resolved = config or config_from_env()
    configure_tracing(trace_file=resolved.trace_file)

    mcp = FastMCP("phlo", json_response=True)
    mcp.settings.host = resolved.host
    mcp.settings.port = resolved.port
    mcp.settings.streamable_http_path = resolved.streamable_http_path

    tracer = trace.get_tracer("phlo.mcp")
    client = PhloApiClient(resolved)

    @mcp.resource(
        "phlo://runtime/config",
        name="runtime_config",
        mime_type="application/json",
    )
    def runtime_config() -> dict[str, Any] | list[dict[str, Any]]:
        """Read the current phlo project configuration."""
        return client.get_config()

    @mcp.resource(
        "phlo://runtime/services",
        name="runtime_services",
        mime_type="application/json",
    )
    def runtime_services() -> dict[str, Any] | list[dict[str, Any]]:
        """Read discovered service metadata."""
        return client.get_services()

    @mcp.resource(
        "phlo://runtime/plugins",
        name="runtime_plugins",
        mime_type="application/json",
    )
    def runtime_plugins() -> dict[str, Any] | list[dict[str, Any]]:
        """Read installed plugin metadata."""
        return client.get_plugins()

    @mcp.resource(
        "phlo://runtime/assets",
        name="runtime_assets",
        mime_type="application/json",
    )
    def runtime_assets() -> dict[str, Any] | list[dict[str, Any]]:
        """Read asset metadata."""
        return client.get_assets()

    @mcp.resource(
        "phlo://runtime/contracts",
        name="runtime_contracts",
        mime_type="application/json",
    )
    def runtime_contracts() -> dict[str, Any] | list[dict[str, Any]]:
        """Read schema contract metadata."""
        return client.get_contracts()

    @mcp.resource(
        "phlo://runtime/dashboards",
        name="runtime_dashboards",
        mime_type="application/json",
    )
    def runtime_dashboards() -> dict[str, Any] | list[dict[str, Any]]:
        """Read observability dashboard metadata."""
        return client.get_dashboard_links()

    @mcp.resource(
        "phlo://runtime/services/{service_name}",
        name="runtime_service",
        mime_type="application/json",
    )
    def runtime_service(service_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read metadata for one service."""
        return client.get_service_info(service_name)

    @mcp.resource(
        "phlo://runtime/assets/{asset_key_path}",
        name="runtime_asset",
        mime_type="application/json",
    )
    def runtime_asset(asset_key_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read metadata for one asset."""
        return client.get_asset_details(asset_key_path)

    @mcp.resource(
        "phlo://runtime/schemas/{asset_key_path}",
        name="runtime_asset_schema",
        mime_type="application/json",
    )
    def runtime_asset_schema(asset_key_path: str) -> dict[str, Any]:
        """Read schema metadata for one asset."""
        details = client.get_asset_details(asset_key_path)
        if not isinstance(details, dict):
            return {"asset_key_path": asset_key_path, "columns": []}
        return {
            "asset_key_path": asset_key_path,
            "columns": details.get("columns") or [],
            "column_lineage": details.get("column_lineage"),
            "partition_definition": details.get("partition_definition"),
        }

    @mcp.resource(
        "phlo://runtime/operations/{operation_id}",
        name="runtime_operation_context",
        mime_type="application/json",
    )
    def runtime_operation_context(operation_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read stable observability context for one Phlo operation."""
        return client.get_operation_context(operation_id)

    @mcp.resource(
        "phlo://runtime/contracts/{table_name}",
        name="runtime_contract",
        mime_type="application/json",
    )
    def runtime_contract(table_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read one schema contract."""
        return client.get_contract(table_name)

    @mcp.resource(
        "phlo://docs/packages/{package_name}",
        name="package_docs",
        mime_type="text/markdown",
    )
    def package_docs(package_name: str) -> str:
        """Read package documentation from the local docs tree."""
        return _read_package_doc(package_name)

    @mcp.resource("phlo://docs/mcp/tools", name="mcp_tools", mime_type="application/json")
    def mcp_tools() -> list[dict[str, Any]]:
        """List registered MCP tools for self-introspection."""
        return [
            ToolContract(
                name=tool.name,
                description=tool.description,
                input_schema=getattr(tool, "parameters", None),
                output_schema=getattr(tool, "output_schema", None),
                required_scope=_required_scope_for_tool(tool.name),
            ).model_dump(mode="json")
            for tool in mcp._tool_manager.list_tools()
        ]

    @mcp.resource("phlo://docs/mcp/prompts", name="mcp_prompts", mime_type="application/json")
    def mcp_prompts() -> list[dict[str, Any]]:
        """List registered MCP prompts for self-introspection."""
        return [
            {"name": prompt.name, "description": prompt.description}
            for prompt in mcp._prompt_manager.list_prompts()
        ]

    @mcp.resource("phlo://docs/cli", name="cli_docs", mime_type="text/markdown")
    def cli_docs() -> str:
        """Return a lightweight CLI command index."""
        from phlo.cli.main import cli

        lines = ["# Phlo CLI", ""]
        for name, command in sorted(cli.commands.items()):
            lines.append(f"- `phlo {name}` — {command.short_help or command.help or ''}")
        return "\n".join(lines)

    def _write_audit_context(
        operation: str, target: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "target": target,
            "dry_run": dry_run,
            "authenticated": bool(resolved.api_token),
            "api_base_url": client.api_base_url,
        }

    def _append_audit_record(audit_context: dict[str, Any]) -> None:
        try:
            audit_dir = Path.cwd() / ".phlo" / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            record = {"timestamp": datetime.now(UTC).isoformat(), **audit_context}
            with (audit_dir / "operations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            # Audit logging is best-effort: a read-only or missing workspace
            # must never fail the tool call that produced the record.
            return

    def _required_scope_for_tool(tool_name: str) -> str | None:
        if tool_name in {
            "materialize_asset",
            "retry_failed_run",
            "cancel_run",
            "backfill_asset",
        }:
            return "lakehouse:operate"
        if tool_name in {"create_workflow", "validate_workflow", "validate_schema", "lint_project"}:
            return "project:write"
        if tool_name == "install_plugin":
            return "admin"
        if (
            tool_name.startswith("get_")
            or tool_name.startswith("search_")
            or tool_name == "list_operations"
        ):
            return "lakehouse:read"
        return None

    @mcp.prompt(name="phlo.debug_run")
    def debug_run(run_id: str) -> str:
        """Guide an agent through run failure debugging with Phlo tools."""
        return (
            f"Debug Phlo run {run_id}. Use get_run_status, get_run_logs, "
            "get_run_trace_spans, and render_run_trace_tree. Identify the failing span or "
            "log line, explain the likely root cause, and propose the smallest safe fix."
        )

    @mcp.prompt(name="phlo.triage_failure")
    def triage_failure(asset_key: str) -> str:
        """Guide an agent through asset failure triage."""
        return (
            f"Triage the latest failure for Phlo asset {asset_key}. Use "
            "inspect_materialization, render_materialization_trace_tree, get_run_logs, and "
            "get_trace_spans. Summarize impact, suspected cause, and remediation steps."
        )

    @mcp.prompt(name="phlo.audit_asset")
    def audit_asset(asset_key: str) -> str:
        """Guide an agent through an asset health audit."""
        return (
            f"Audit Phlo asset {asset_key}. Inspect asset metadata, schema, recent "
            "materializations, traces, logs, and downstream impact. Return risks, evidence, "
            "and prioritized fixes."
        )

    @mcp.prompt(name="phlo.plan_backfill")
    def plan_backfill(asset_key: str, partition_range: str) -> str:
        """Guide an agent through safe backfill planning."""
        return (
            f"Plan a safe backfill for Phlo asset {asset_key} over {partition_range}. Use "
            "list_partitions and backfill_asset with dry_run=true first. Call out expected "
            "partitions, caveats, and the exact live command only if the plan is safe."
        )

    @mcp.prompt(name="phlo.scaffold_workflow")
    def scaffold_workflow(domain: str, table: str) -> str:
        """Guide an agent through workflow scaffolding once authoring tools are available."""
        return (
            f"Scaffold a Phlo workflow for domain {domain} and table {table}. Prefer MCP "
            "authoring tools when available; otherwise inspect templates and provide a "
            "minimal workflow plan with validation and materialization steps."
        )

    @mcp.tool()
    def get_platform_health() -> dict[str, Any]:
        """Get Phlo platform observability health from phlo-api."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_platform_health"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_platform_health"},
            ):
                with tracer.start_as_current_span("phlo.observability.health"):
                    payload = client.get_platform_health()
                    return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def list_plugins() -> dict[str, Any]:
        """List installed Phlo plugins through phlo-api."""
        payload = client.get_plugins()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def get_service_status() -> dict[str, Any]:
        """Get current service status snapshot from phlo-api."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_service_status"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_service_status"},
            ):
                with tracer.start_as_current_span("phlo.observability.services"):
                    services = client.get_service_status()
                    return {"api_base_url": client.api_base_url, "services": services}

    @mcp.tool()
    def get_recent_alerts(limit: int = 5, cursor: str | None = None) -> dict[str, Any]:
        """Get recent observability alerts from phlo-api."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_recent_alerts"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_recent_alerts"},
            ):
                with tracer.start_as_current_span("phlo.observability.alerts"):
                    alerts = client.get_recent_alerts(limit, cursor=cursor)
                    return {"api_base_url": client.api_base_url, "alerts": alerts}

    @mcp.tool()
    def get_dashboard_links() -> dict[str, Any]:
        """Get available observability dashboard links from phlo-api."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_dashboard_links"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_dashboard_links"},
            ):
                with tracer.start_as_current_span("phlo.observability.dashboards"):
                    dashboards = client.get_dashboard_links()
                    return {"api_base_url": client.api_base_url, "dashboards": dashboards}

    @mcp.tool()
    def list_operations(
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Find recent operations before fetching stable operation context."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "list_operations"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "list_operations"},
            ):
                with tracer.start_as_current_span("phlo.observability.operations"):
                    payload = client.list_operations(
                        status=status,
                        kind=kind,
                        query=query,
                        limit=limit,
                    )
                    return {
                        "api_base_url": client.api_base_url,
                        "filters": {
                            "status": status,
                            "kind": kind,
                            "query": query,
                            "limit": limit,
                        },
                        "payload": payload,
                    }

    @mcp.tool()
    def get_operation_context(operation_id: str) -> dict[str, Any]:
        """Get stable operation, trace, log, metric, and incident context."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_operation_context"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_operation_context"},
            ):
                with tracer.start_as_current_span("phlo.observability.operation_context"):
                    payload = client.get_operation_context(operation_id)
                    return {
                        "api_base_url": client.api_base_url,
                        "operation_id": operation_id,
                        "payload": payload,
                    }

    @mcp.tool()
    def get_logs_query_link(service: str | None = None) -> dict[str, Any]:
        """Get a backend-specific log query link, optionally filtered by service."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_logs_query_link"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_logs_query_link"},
            ):
                with tracer.start_as_current_span("phlo.observability.links.logs"):
                    payload = client.get_logs_query_link(service)
                    return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def get_metrics_query_link(metric: str | None = None) -> dict[str, Any]:
        """Get a backend-specific metrics query link, optionally filtered by metric."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_metrics_query_link"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_metrics_query_link"},
            ):
                with tracer.start_as_current_span("phlo.observability.links.metrics"):
                    payload = client.get_metrics_query_link(metric)
                    return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def get_materialization_history(
        asset_key_path: str, limit: int = 10, cursor: str | None = None
    ) -> dict[str, Any]:
        """Get recent materializations for an asset."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_materialization_history"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_materialization_history"},
            ):
                with tracer.start_as_current_span("phlo.orchestrator.materialization_history"):
                    events = client.get_materialization_history(
                        asset_key_path, limit=limit, cursor=cursor
                    )
                    return {
                        "api_base_url": client.api_base_url,
                        "asset_key_path": asset_key_path,
                        "events": events,
                    }

    @mcp.tool()
    def get_run_logs(
        run_id: str,
        limit: int = 200,
        level: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Get correlated logs for a specific run or materialization."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_run_logs"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_run_logs"},
            ):
                with tracer.start_as_current_span("phlo.loki.run_logs"):
                    payload = client.get_run_logs(run_id, limit=limit, level=level, cursor=cursor)
                    return {
                        "api_base_url": client.api_base_url,
                        "run_id": run_id,
                        "payload": payload,
                    }

    @mcp.tool()
    def get_run_trace_spans(
        run_id: str, limit: int = 500, cursor: str | None = None
    ) -> dict[str, Any]:
        """Get OTEL spans correlated to a specific run id."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_run_trace_spans"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_run_trace_spans"},
            ):
                with tracer.start_as_current_span("phlo.observability.run_spans"):
                    payload = client.get_run_trace_spans(run_id, limit=limit, cursor=cursor)
                    return {
                        "api_base_url": client.api_base_url,
                        "run_id": run_id,
                        "spans": payload,
                    }

    @mcp.tool()
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
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_trace_spans"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_trace_spans"},
            ):
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
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "render_trace_spans_tree"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "render_trace_spans_tree"},
            ):
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

    def _trace_filter_payload(**filters: Any) -> dict[str, Any]:
        return {key: value for key, value in filters.items() if value is not None}

    @mcp.tool()
    def inspect_materialization(
        asset_key_path: str, limit: int = 5, log_limit: int = 200
    ) -> dict[str, Any]:
        """Inspect recent materializations and correlated logs for the latest run."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "inspect_materialization"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "inspect_materialization"},
            ):
                with tracer.start_as_current_span("phlo.materialization.inspect"):
                    return _inspect_materialization_payload(
                        asset_key_path,
                        limit=limit,
                        log_limit=log_limit,
                    )

    @mcp.tool()
    def get_asset_materialization_trace(
        asset_key_path: str, limit: int = 5, span_limit: int = 500
    ) -> dict[str, Any]:
        """Resolve the latest materialization for an asset and return its trace payload."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "get_asset_materialization_trace"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "get_asset_materialization_trace"},
            ):
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
    def render_materialization_trace_tree(
        asset_key_path: str, limit: int = 5, span_limit: int = 500
    ) -> dict[str, Any]:
        """Render the latest materialization trace tree for an asset key path."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "render_materialization_trace_tree"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "render_materialization_trace_tree"},
            ):
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
    def render_run_trace_tree(run_id: str, limit: int = 200) -> dict[str, Any]:
        """Render a run execution tree using real spans when available, else logs."""
        with tracer.start_as_current_span(
            "mcp.request",
            attributes={"mcp.tool.name": "render_run_trace_tree"},
        ):
            with tracer.start_as_current_span(
                "mcp.tool.execute",
                attributes={"mcp.tool.name": "render_run_trace_tree"},
            ):
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

    @mcp.tool()
    def list_workflows(
        search: str | None = None,
        group: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List workflows discovered in the Phlo project."""
        payload = client.list_workflows(search=search, group=group, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def list_templates() -> dict[str, Any]:
        """List available Phlo project templates."""
        payload = client.list_templates()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def lint_project() -> dict[str, Any]:
        """Run lightweight Phlo project lint checks."""
        payload = client.lint_project()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def run_doctor() -> dict[str, Any]:
        """Run Phlo doctor through phlo-api and return JSON diagnostics."""
        payload = client.run_doctor()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def search_assets(query: str, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search Observatory assets."""
        payload = client.search_assets(query, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    def search_contracts(query: str, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search Phlo contracts."""
        payload = client.search_contracts(query, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    def search_runs(
        query: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """Search recent orchestrator runs."""
        payload = client.search_runs(query, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    def search_run_logs(
        run_id: str,
        query: str,
        regex: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Search logs for one run using text and optional regex filters."""
        payload = client.search_run_logs(
            run_id,
            query=query,
            regex=regex,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        )
        return {"api_base_url": client.api_base_url, "run_id": run_id, "payload": payload}

    @mcp.tool()
    def follow_run_logs(run_id: str, timeout_seconds: int = 30) -> dict[str, Any]:
        """Follow run logs through a bounded phlo-api Server-Sent Event stream."""
        payload = client.follow_run_logs(
            run_id,
            timeout_seconds=timeout_seconds,
            limit=min(max(timeout_seconds * 10, 1), 200),
        )
        return {"api_base_url": client.api_base_url, "run_id": run_id, "payload": payload}

    @mcp.tool()
    def get_quality_results(
        asset_key: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        """Get quality results, optionally filtered by asset or run."""
        payload = client.get_quality_results(asset_key=asset_key, run_id=run_id)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    def get_lineage(asset_key: str, direction: str = "both", depth: int = 1) -> dict[str, Any]:
        """Get bounded lineage around one asset."""
        payload = client.get_lineage(asset_key, direction=direction, depth=depth)
        return {"api_base_url": client.api_base_url, "asset_key": asset_key, "payload": payload}

    @mcp.tool()
    def diff_schema(
        asset_key: str, from_run: str | None = None, to_run: str | None = None
    ) -> dict[str, Any]:
        """Diff schema snapshots for an asset across two runs."""
        payload = client.diff_schema(asset_key, from_run=from_run, to_run=to_run)
        return {"api_base_url": client.api_base_url, "asset_key": asset_key, "payload": payload}

    # Write tools are registered only when both the flag is set and an API token
    # exists: without a token every guarded call would run unauthenticated, so the
    # tools stay unregistered rather than failing (or worse, succeeding) per call.
    if resolved.enable_write_tools and resolved.api_token:

        @mcp.tool()
        def create_workflow(
            domain: str,
            table: str,
            unique_key: str,
            cron: str = "0 */1 * * *",
            api_base_url: str | None = None,
            fields: list[str] | None = None,
            provider: str | None = None,
        ) -> dict[str, Any]:
            """Create a workflow scaffold through phlo-api when write tools are enabled."""
            payload = client.create_workflow(
                domain=domain,
                table=table,
                unique_key=unique_key,
                cron=cron,
                api_base_url=api_base_url,
                fields=fields,
                provider=provider,
            )
            target: dict[str, Any] = {"domain": domain, "table": table}
            if provider:
                target["provider"] = provider
            audit_context = _write_audit_context("create_workflow", target, False)
            _append_audit_record(audit_context)
            return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        def validate_workflow(workflow_path: str) -> dict[str, Any]:
            """Validate a workflow file through phlo-api."""
            payload = client.validate_workflow(workflow_path)
            return {"api_base_url": client.api_base_url, "payload": payload}

        @mcp.tool()
        def validate_schema(schema_path: str) -> dict[str, Any]:
            """Validate a schema file through phlo-api."""
            payload = client.validate_schema(schema_path)
            return {"api_base_url": client.api_base_url, "payload": payload}

        @mcp.tool()
        def materialize_asset(
            asset_key_path: str,
            dry_run: bool = True,
            partition_key: str | None = None,
            job_name: str | None = None,
            repository_location_name: str | None = None,
            repository_name: str | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Materialize an asset through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "materialize_asset"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "materialize_asset"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.asset.materialize"):
                        target = {"asset_key_path": asset_key_path}
                        if partition_key:
                            target["partition_key"] = partition_key
                        if job_name:
                            target["job_name"] = job_name
                        if repository_location_name:
                            target["repository_location_name"] = repository_location_name
                        if repository_name:
                            target["repository_name"] = repository_name
                        if idempotency_key:
                            target["idempotency_key"] = idempotency_key
                        audit_context = _write_audit_context("materialize_asset", target, dry_run)
                        _append_audit_record(audit_context)
                        payload = client.materialize_asset(
                            asset_key_path,
                            dry_run=dry_run,
                            partition_key=partition_key,
                            job_name=job_name,
                            repository_location_name=repository_location_name,
                            repository_name=repository_name,
                            idempotency_key=idempotency_key,
                        )
                        return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        def retry_failed_run(
            run_id: str,
            dry_run: bool = True,
            strategy: str = "FROM_FAILURE",
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Retry a failed orchestrator run through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "retry_failed_run"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "retry_failed_run"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.run.retry"):
                        audit_context = _write_audit_context(
                            "retry_failed_run",
                            {
                                key: value
                                for key, value in {
                                    "run_id": run_id,
                                    "strategy": strategy,
                                    "idempotency_key": idempotency_key,
                                }.items()
                                if value
                            },
                            dry_run,
                        )
                        _append_audit_record(audit_context)
                        payload = client.retry_run(
                            run_id,
                            dry_run=dry_run,
                            strategy=strategy,
                            idempotency_key=idempotency_key,
                        )
                        return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        def cancel_run(
            run_id: str, reason: str | None = None, idempotency_key: str | None = None
        ) -> dict[str, Any]:
            """Cancel an orchestrator run through phlo-api when write tools are enabled."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "cancel_run"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "cancel_run"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.run.cancel"):
                        audit_context = _write_audit_context(
                            "cancel_run",
                            {
                                key: value
                                for key, value in {
                                    "run_id": run_id,
                                    "reason": reason,
                                    "idempotency_key": idempotency_key,
                                }.items()
                                if value
                            },
                            False,
                        )
                        _append_audit_record(audit_context)
                        payload = client.cancel_run(
                            run_id, reason=reason, idempotency_key=idempotency_key
                        )
                        return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        def backfill_asset(
            asset_key_path: str,
            dry_run: bool = True,
            partitions: list[str] | None = None,
            partition_range: dict[str, str] | None = None,
            partition_set_name: str | None = None,
            repository_location_name: str | None = None,
            repository_name: str | None = None,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            """Plan or launch an asset partition backfill through phlo-api."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "backfill_asset"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "backfill_asset"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.asset.backfill"):
                        target: dict[str, Any] = {"asset_key_path": asset_key_path}
                        if partitions:
                            target["partitions"] = partitions
                        if partition_range:
                            target["partition_range"] = partition_range
                        if partition_set_name:
                            target["partition_set_name"] = partition_set_name
                        if repository_location_name:
                            target["repository_location_name"] = repository_location_name
                        if repository_name:
                            target["repository_name"] = repository_name
                        if idempotency_key:
                            target["idempotency_key"] = idempotency_key
                        audit_context = _write_audit_context("backfill_asset", target, dry_run)
                        _append_audit_record(audit_context)
                        payload = client.backfill_asset(
                            asset_key_path,
                            dry_run=dry_run,
                            partitions=partitions,
                            partition_range=partition_range,
                            partition_set_name=partition_set_name,
                            repository_location_name=repository_location_name,
                            repository_name=repository_name,
                            idempotency_key=idempotency_key,
                        )
                        return {"audit_context": audit_context, "payload": payload}

        @mcp.tool()
        def list_partitions(asset_key_path: str) -> dict[str, Any]:
            """List partition keys for an asset through phlo-api."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "list_partitions"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "list_partitions"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.asset.partitions"):
                        payload = client.list_partitions(asset_key_path)
                        return {
                            "api_base_url": client.api_base_url,
                            "asset_key_path": asset_key_path,
                            "partitions": payload,
                        }

        @mcp.tool()
        def get_run_status(run_id: str) -> dict[str, Any]:
            """Get orchestrator run status through phlo-api for operational follow-up."""
            with tracer.start_as_current_span(
                "mcp.request",
                attributes={"mcp.tool.name": "get_run_status"},
            ):
                with tracer.start_as_current_span(
                    "mcp.tool.execute",
                    attributes={"mcp.tool.name": "get_run_status"},
                ):
                    with tracer.start_as_current_span("phlo.orchestrator.run.status"):
                        payload = client.get_run_status(run_id)
                        return {
                            "api_base_url": client.api_base_url,
                            "run_id": run_id,
                            "payload": payload,
                        }

        @mcp.tool()
        def install_plugin(package_name: str) -> dict[str, Any]:
            """Install a trusted Phlo plugin package through phlo-api."""
            audit_context = _write_audit_context(
                "install_plugin", {"package_name": package_name}, False
            )
            _append_audit_record(audit_context)
            payload = client.install_plugin(package_name)
            return {"audit_context": audit_context, "payload": payload}

    return mcp
