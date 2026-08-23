"""Tests for phlo-mcp package surfaces.

Exercises the API client route wrapping (with faked HTTP responses),
server tool/resource registration including the auth-gated write
tools, CLI/env config parsing, and run-log/trace rendering helpers.
All HTTP is stubbed; no server or network is involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.cli import parse_args
from phlo_mcp.config import McpConfig, config_from_env
from phlo_mcp.run_analysis import (
    render_run_trace_tree as render_run_trace_tree_text,
    summarize_run_logs,
)
from phlo_mcp.server import create_server
from phlo_mcp.tracing import render_trace_tree


class _FakeResponse:
    def __init__(self, payload):  # noqa: ANN001
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):  # noqa: ANN201
        return self._payload


def test_api_client_wraps_observability_routes(monkeypatch) -> None:
    seen_urls: list[str] = []
    seen_params: list[dict[str, object] | None] = []
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        seen_urls.append(url)
        seen_params.append(params)
        seen_headers.append(headers or {})
        if url.endswith("/api/config"):
            return _FakeResponse({"name": "demo"})
        if url.endswith("/api/plugins"):
            return _FakeResponse({"services": ["phlo-api"]})
        if url.endswith("/api/services"):
            return _FakeResponse([{"name": "dagster", "profile": "orchestration"}])
        if url.endswith("/api/services/dagster"):
            return _FakeResponse({"name": "dagster", "depends_on": []})
        if url.endswith("/api/observatory/assets"):
            return _FakeResponse({"items": [{"id": "silver/orders", "name": "silver/orders"}]})
        if url.endswith("/api/observatory/assets/silver/orders"):
            return _FakeResponse(
                {
                    "asset": {"id": "silver/orders", "name": "silver/orders"},
                    "materializations": [{"id": "run-123", "metadata": {"run_id": "run-123"}}],
                    "column_lineage": {"id": []},
                }
            )
        if url.endswith("/api/observatory/operations"):
            assert params == {
                "status": "failed",
                "kind": "workflow.apply",
                "q": "orders",
                "limit": 2,
            }
            return _FakeResponse(
                {"items": [{"id": "op-123", "kind": "workflow.apply", "status": "failed"}]}
            )
        if url.endswith("/api/observatory/operations/op-123/agent-context"):
            return _FakeResponse(
                {
                    "schema_version": "phlo.operation_observability.v1",
                    "operation": {"id": "op-123", "status": "failed"},
                    "identifiers": {"operation_id": "op-123", "trace_ids": ["trace-123"]},
                }
            )
        if url.endswith("/api/contracts"):
            return _FakeResponse([{"table": "silver.orders"}])
        if url.endswith("/api/contracts/silver.orders"):
            return _FakeResponse({"table": "silver.orders"})
        if url.endswith("/health"):
            return _FakeResponse({"overall_status": "healthy"})
        if url.endswith("/services"):
            return _FakeResponse([{"name": "observability", "status": "unknown"}])
        if url.endswith("/alerts"):
            assert params == {"limit": 3}
            return _FakeResponse([{"title": "No alerts"}])
        if url.endswith("/dashboards"):
            return _FakeResponse([{"title": "ClickStack", "url": "http://example.test"}])
        if "/api/loki/runs/" in url:
            return _FakeResponse(
                {
                    "entries": [
                        {
                            "timestamp": "2026-01-01T00:00:00Z",
                            "level": "info",
                            "message": "materialization started",
                            "metadata": {
                                "service": "dagster",
                                "asset_key": "silver/orders",
                                "trace_id": "abc123",
                            },
                        }
                    ],
                    "has_more": False,
                }
            )
        if "/api/observability/traces/runs/" in url:
            return _FakeResponse(
                [
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "trace_id": "abc123",
                        "span_id": "root",
                        "parent_span_id": None,
                        "span_name": "materialize_orders",
                        "service_name": "dagster",
                        "span_kind": "INTERNAL",
                        "duration_ms": 12.5,
                        "status_code": "STATUS_CODE_OK",
                        "span_attributes": {
                            "phlo.run_id": "run-123",
                            "phlo.asset_key": "silver/orders",
                            "phlo.stage": "materialize",
                            "phlo.operation": "write",
                        },
                        "resource_attributes": {
                            "service.name": "dagster",
                            "service.version": "1.2.3",
                        },
                    }
                ]
            )
        if url.endswith("/api/observability/traces"):
            assert params == {
                "limit": 25,
                "asset_key": "silver/orders",
                "service_name": "dagster",
                "status_code": "STATUS_CODE_ERROR",
            }
            return _FakeResponse(
                [
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "trace_id": "abc123",
                        "span_id": "root",
                        "parent_span_id": None,
                        "span_name": "materialize_orders",
                        "service_name": "dagster",
                        "span_kind": "INTERNAL",
                        "duration_ms": 12.5,
                        "status_code": "STATUS_CODE_ERROR",
                        "span_attributes": {"phlo.asset_key": "silver/orders"},
                        "resource_attributes": {"service.name": "dagster"},
                    }
                ]
            )
        if "/api/observatory/assets/" in url and url.endswith("/materializations"):
            return _FakeResponse([{"run_id": "run-123", "timestamp": "2026-01-01T00:00:00Z"}])
        if url.endswith("/links/logs"):
            return _FakeResponse({"url": "http://logs.test"})
        if url.endswith("/links/metrics"):
            return _FakeResponse({"url": "http://metrics.test"})
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    assert client.get_config()["name"] == "demo"
    assert client.get_plugins()["services"] == ["phlo-api"]
    assert client.get_services()[0]["name"] == "dagster"
    assert client.get_service_info("dagster")["name"] == "dagster"
    assert client.get_assets()[0]["id"] == "silver/orders"
    assert client.get_asset_details("silver/orders")["asset"]["id"] == "silver/orders"
    assert (
        client.list_operations(status="failed", kind="workflow.apply", query="orders", limit=2)[
            "items"
        ][0]["id"]
        == "op-123"
    )
    assert client.get_operation_context("op-123")["identifiers"]["trace_ids"] == ["trace-123"]
    assert client.get_contracts()[0]["table"] == "silver.orders"
    assert client.get_contract("silver.orders")["table"] == "silver.orders"
    assert client.get_platform_health()["overall_status"] == "healthy"
    assert client.get_service_status()[0]["name"] == "observability"
    assert client.get_recent_alerts(limit=3)[0]["title"] == "No alerts"
    assert client.get_dashboard_links()[0]["title"] == "ClickStack"
    assert client.get_run_logs("run-123")["entries"][0]["metadata"]["trace_id"] == "abc123"
    assert client.get_run_trace_spans("run-123")[0]["span_id"] == "root"
    assert (
        client.get_trace_spans(
            asset_key="silver/orders",
            service_name="dagster",
            status_code="STATUS_CODE_ERROR",
            limit=25,
        )[0]["status_code"]
        == "STATUS_CODE_ERROR"
    )
    assert client.get_materialization_history("silver/orders")[0]["run_id"] == "run-123"
    assert client.get_logs_query_link()["url"] == "http://logs.test"
    assert client.get_metrics_query_link()["url"] == "http://metrics.test"
    assert "http://example.test/api/observability/health" in seen_urls
    assert seen_params[seen_urls.index("http://example.test/api/observatory/operations")] == {
        "status": "failed",
        "kind": "workflow.apply",
        "q": "orders",
        "limit": 2,
    }
    assert seen_headers[0] == {}


def test_create_server_registers_resources() -> None:
    server = create_server(McpConfig())

    resource_uris = sorted(str(uri) for uri in server._resource_manager._resources)
    template_uris = sorted(
        template.uri_template for template in server._resource_manager._templates.values()
    )

    assert resource_uris == [
        "phlo://docs/cli",
        "phlo://docs/mcp/prompts",
        "phlo://docs/mcp/tools",
        "phlo://runtime/assets",
        "phlo://runtime/config",
        "phlo://runtime/contracts",
        "phlo://runtime/dashboards",
        "phlo://runtime/plugins",
        "phlo://runtime/services",
    ]
    assert template_uris == [
        "phlo://docs/packages/{package_name}",
        "phlo://runtime/assets/{asset_key_path}",
        "phlo://runtime/contracts/{table_name}",
        "phlo://runtime/operations/{operation_id}",
        "phlo://runtime/schemas/{asset_key_path}",
        "phlo://runtime/services/{service_name}",
    ]


def test_package_docs_resource_reads_local_docs() -> None:
    server = create_server(McpConfig())
    template = next(
        template
        for template in server._resource_manager._templates.values()
        if template.uri_template == "phlo://docs/packages/{package_name}"
    )

    rendered = template.fn("phlo-mcp")

    assert "# phlo-mcp" in rendered


def test_api_client_adds_bearer_token_header(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        captured["headers"] = headers
        return _FakeResponse({"overall_status": "healthy"})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test", api_token="secret"))

    assert client.get_platform_health()["overall_status"] == "healthy"
    assert captured["headers"] == {"Authorization": "Bearer secret"}


def test_api_client_wraps_operational_routes(monkeypatch) -> None:
    seen_posts: list[dict[str, object]] = []
    seen_gets: list[str] = []

    def fake_post(url: str, json=None, headers=None, timeout=30.0):  # noqa: ANN001
        seen_posts.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        return _FakeResponse({"queued": True, "dry_run": json.get("dry_run")})

    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        seen_gets.append(url)
        return _FakeResponse({"run_id": "run-123", "status": "STARTED"})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.post", fake_post)
    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test", api_token="secret"))

    assert client.create_workflow(
        domain="sales", table="orders", unique_key="id", provider="sling"
    ) == {"queued": True, "dry_run": None}
    assert client.materialize_asset("silver/orders", dry_run=True, partition_key="2026-04-26") == {
        "queued": True,
        "dry_run": True,
    }
    assert client.retry_run("run-123", dry_run=False) == {"queued": True, "dry_run": False}
    assert client.cancel_run("run-123", reason="stuck") == {"queued": True, "dry_run": None}
    assert client.backfill_asset("silver/orders", dry_run=True, partitions=["2026-04-26"]) == {
        "queued": True,
        "dry_run": True,
    }
    assert client.get_run_status("run-123")["status"] == "STARTED"
    assert client.list_partitions("silver/orders")["status"] == "STARTED"
    assert seen_posts == [
        {
            "url": "http://example.test/api/authoring/workflows",
            "json": {
                "domain": "sales",
                "table": "orders",
                "unique_key": "id",
                "cron": "0 */1 * * *",
                "fields": [],
                "provider": "sling",
            },
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        },
        {
            "url": "http://example.test/api/observatory/assets/silver/orders/materialize",
            "json": {"dry_run": True, "partition_key": "2026-04-26"},
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        },
        {
            "url": "http://example.test/api/observatory/runs/run-123/retry",
            "json": {"dry_run": False},
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        },
        {
            "url": "http://example.test/api/observatory/runs/run-123/cancel",
            "json": {"reason": "stuck"},
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        },
        {
            "url": "http://example.test/api/observatory/assets/silver/orders/backfill",
            "json": {"dry_run": True, "partitions": ["2026-04-26"]},
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        },
    ]
    assert seen_gets == [
        "http://example.test/api/observatory/runs/run-123/status",
        "http://example.test/api/observatory/assets/silver/orders/partitions",
    ]


def test_search_contracts_propagates_api_errors(monkeypatch) -> None:
    monkeypatch.setattr(PhloApiClient, "get_contracts", lambda self: {"error": "unavailable"})
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    assert client.search_contracts("orders") == {"error": "unavailable"}


def test_follow_run_logs_flushes_final_sse_event(monkeypatch) -> None:
    class _FakeStream:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):  # noqa: ANN201
            return iter(["event: log", 'data: {"message":"done"}'])

    monkeypatch.setattr(
        "phlo_mcp.api_client.httpx.stream",
        lambda *args, **kwargs: _FakeStream(),  # noqa: ARG005
    )
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    assert client.follow_run_logs("run-123")["events"] == [
        {"event": "log", "data": {"message": "done"}}
    ]


def test_create_server_registers_expected_tools() -> None:
    server = create_server(McpConfig())

    tool_names = [tool.name for tool in server._tool_manager.list_tools()]

    assert tool_names == [
        "get_platform_health",
        "list_plugins",
        "get_service_status",
        "get_recent_alerts",
        "get_dashboard_links",
        "list_operations",
        "get_operation_context",
        "get_logs_query_link",
        "get_metrics_query_link",
        "get_materialization_history",
        "get_run_logs",
        "get_run_trace_spans",
        "get_trace_spans",
        "render_trace_spans_tree",
        "inspect_materialization",
        "get_asset_materialization_trace",
        "render_materialization_trace_tree",
        "render_run_trace_tree",
        "list_workflows",
        "list_templates",
        "lint_project",
        "run_doctor",
        "search_assets",
        "search_contracts",
        "search_runs",
        "search_run_logs",
        "follow_run_logs",
        "get_quality_results",
        "get_lineage",
        "diff_schema",
    ]


def test_create_server_hides_write_tools_by_default() -> None:
    server = create_server(McpConfig(api_token="secret"))

    tool_names = [tool.name for tool in server._tool_manager.list_tools()]

    assert "materialize_asset" not in tool_names
    assert "retry_failed_run" not in tool_names
    assert "get_run_status" not in tool_names


def test_create_server_registers_write_tools_only_with_auth() -> None:
    unauthenticated = create_server(McpConfig(enable_write_tools=True))
    authenticated = create_server(McpConfig(api_token="secret", enable_write_tools=True))

    unauthenticated_tool_names = [tool.name for tool in unauthenticated._tool_manager.list_tools()]
    authenticated_tool_names = [tool.name for tool in authenticated._tool_manager.list_tools()]

    assert "materialize_asset" not in unauthenticated_tool_names
    assert "retry_failed_run" not in unauthenticated_tool_names
    assert "get_run_status" not in unauthenticated_tool_names
    assert authenticated_tool_names[-10:] == [
        "create_workflow",
        "validate_workflow",
        "validate_schema",
        "materialize_asset",
        "retry_failed_run",
        "cancel_run",
        "backfill_asset",
        "list_partitions",
        "get_run_status",
        "install_plugin",
    ]


def test_write_tool_returns_audit_context_without_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_materialize_asset(
        self,  # noqa: ANN001
        asset_key_path: str,
        *,
        dry_run: bool = True,
        partition_key: str | None = None,
        job_name: str | None = None,
        repository_location_name: str | None = None,
        repository_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        return {
            "asset_key_path": asset_key_path,
            "dry_run": dry_run,
            "partition_key": partition_key,
            "job_name": job_name,
            "repository_location_name": repository_location_name,
            "repository_name": repository_name,
            "idempotency_key": idempotency_key,
            "queued": False,
        }

    monkeypatch.setattr(PhloApiClient, "materialize_asset", fake_materialize_asset)
    server = create_server(McpConfig(api_token="secret-token", enable_write_tools=True))
    tool = next(
        tool for tool in server._tool_manager.list_tools() if tool.name == "materialize_asset"
    )

    result = tool.fn("silver/orders", dry_run=True, partition_key="2026-04-26")

    assert result["audit_context"] == {
        "operation": "materialize_asset",
        "target": {"asset_key_path": "silver/orders", "partition_key": "2026-04-26"},
        "dry_run": True,
        "authenticated": True,
        "api_base_url": "http://127.0.0.1:4000",
    }
    assert "secret-token" not in json.dumps(result)


def test_parse_args_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_MCP_API_BASE_URL", "http://env.test:4000")
    monkeypatch.setenv("PHLO_MCP_API_TOKEN", "env-token")
    monkeypatch.setenv("PHLO_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("PHLO_MCP_PORT", "8000")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "phlo-mcp",
            "--transport",
            "streamable-http",
            "--api-base-url",
            "http://cli.test:4100",
            "--api-token",
            "cli-token",
            "--enable-write-tools",
            "--port",
            "9000",
        ],
    )

    config = parse_args()

    assert config.transport == "streamable-http"
    assert config.api_base_url == "http://cli.test:4100"
    assert config.api_token == "cli-token"
    assert config.enable_write_tools is True
    assert config.port == 9000


def test_parse_args_preserves_zero_port(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_MCP_PORT", "8000")
    monkeypatch.setattr(sys, "argv", ["phlo-mcp", "--port", "0"])

    config = parse_args()

    assert config.port == 0


def test_config_from_env_reads_write_tool_gate(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_MCP_ENABLE_WRITE_TOOLS", "true")

    config = config_from_env()

    assert config.enable_write_tools is True


def test_run_analysis_helpers_render_materialization_view() -> None:
    entries = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "level": "info",
            "message": "materialization started",
            "metadata": {"service": "dagster", "function": "step", "trace_id": "abc123"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "level": "info",
            "message": "materialization completed",
            "metadata": {"service": "dagster", "function": "step", "trace_id": "abc123"},
        },
    ]
    spans = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "trace_id": "abc123",
            "span_id": "root",
            "parent_span_id": None,
            "span_name": "materialize_orders",
            "service_name": "dagster",
            "span_kind": "INTERNAL",
            "status_code": "STATUS_CODE_OK",
            "duration_ms": 12.5,
            "span_attributes": {
                "phlo.asset_key": "silver/orders",
                "phlo.stage": "materialize",
                "phlo.operation": "write",
            },
            "resource_attributes": {"service.name": "dagster", "service.version": "1.2.3"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "trace_id": "abc123",
            "span_id": "child",
            "parent_span_id": "root",
            "span_name": "write_output",
            "service_name": "dagster",
            "span_kind": "INTERNAL",
            "status_code": "STATUS_CODE_OK",
            "duration_ms": 3.0,
            "span_attributes": {"phlo.stage": "materialize"},
            "resource_attributes": {"service.name": "dagster"},
        },
    ]

    summary = summarize_run_logs("run-123", entries)
    tree = render_run_trace_tree_text("run-123", entries)
    from phlo_mcp.run_analysis import render_span_tree

    span_tree = render_span_tree("run-123", spans)

    assert summary["trace_ids"] == ["abc123"]
    assert summary["entry_count"] == 2
    assert "Run run-123" in tree
    assert "trace abc123" in tree
    assert "materialization started" in tree
    assert "materialize_orders [internal ok]" in span_tree
    assert "write_output [internal ok]" in span_tree
    assert "asset=silver/orders" in span_tree
    assert "stage=materialize" in span_tree
    assert "service.version=1.2.3" in span_tree


def test_render_span_tree_renders_orphaned_filtered_spans() -> None:
    from phlo_mcp.run_analysis import render_span_tree

    tree = render_span_tree(
        "filtered traces",
        [
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "trace_id": "abc123",
                "span_id": "child",
                "parent_span_id": "missing-parent",
                "span_name": "write_output",
                "service_name": "dagster",
                "span_kind": "INTERNAL",
                "status_code": "STATUS_CODE_ERROR",
                "duration_ms": 3.0,
                "span_attributes": {"phlo.stage": "materialize"},
                "resource_attributes": {"service.name": "dagster"},
            }
        ],
    )

    assert "trace abc123" in tree
    assert "write_output [internal error]" in tree
    assert "stage=materialize" in tree


def test_render_run_trace_tree_uses_most_recent_logs_with_limit() -> None:
    entries = [
        {
            "timestamp": "2026-01-01T00:00:03Z",
            "level": "error",
            "message": "latest failure",
            "metadata": {"service": "dagster"},
        },
        {
            "timestamp": "2026-01-01T00:00:02Z",
            "level": "warn",
            "message": "latest warning",
            "metadata": {"service": "dagster"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "level": "info",
            "message": "older info",
            "metadata": {"service": "dagster"},
        },
    ]

    tree = render_run_trace_tree_text("run-123", entries, limit=2)

    assert "latest failure" in tree
    assert "latest warning" in tree
    assert "older info" not in tree
    assert tree.index("latest warning") < tree.index("latest failure")


def test_render_trace_tree_formats_tree(tmp_path: Path) -> None:
    trace_file = tmp_path / "trace.jsonl"
    spans = [
        {
            "name": "mcp.request",
            "context": {"trace_id": "a" * 32, "span_id": "1" * 16, "parent_id": None},
            "start_time_ns": 0,
            "end_time_ns": 4_000_000,
            "attributes": {},
            "status": {"code": "UNSET", "description": None},
        },
        {
            "name": "mcp.tool.execute",
            "context": {"trace_id": "a" * 32, "span_id": "2" * 16, "parent_id": "1" * 16},
            "start_time_ns": 1_000_000,
            "end_time_ns": 3_000_000,
            "attributes": {"mcp.tool.name": "get_platform_health"},
            "status": {"code": "UNSET", "description": None},
        },
    ]
    trace_file.write_text("\n".join(json.dumps(span) for span in spans), encoding="utf-8")

    rendered = render_trace_tree(trace_file)

    assert "Trace aaaaaaaa" in rendered
    assert "mcp.request 4.0ms" in rendered
    assert "mcp.tool.execute 2.0ms [tool=get_platform_health]" in rendered
