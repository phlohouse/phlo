"""Tests for phlo-mcp package surfaces.

Exercises the API client route wrapping (with faked HTTP responses),
server tool/resource registration including the auth-gated write
tools, CLI/env config parsing, and run-log/trace rendering helpers.
All HTTP is stubbed; no server or network is involved.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.cli import main, parse_args
from phlo_mcp.config import McpConfig, config_from_env
from phlo_mcp.run_analysis import (
    render_run_trace_tree as render_run_trace_tree_text,
)
from phlo_mcp.run_analysis import (
    summarize_run_logs,
)
from phlo_mcp.server import create_server
from phlo_mcp.tracing import CanonicalTracer


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "localhost"])
def test_mcp_rejects_non_loopback_http_even_with_outbound_token(transport, host) -> None:
    with pytest.raises(ValueError, match="no inbound authentication.*PHLO_MCP_API_TOKEN"):
        create_server(McpConfig(transport=transport, host=host, api_token="outbound-only"))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_mcp_allows_loopback_http_and_stdio(host) -> None:
    assert create_server(McpConfig(transport="streamable-http", host=host)).settings.host == host
    assert create_server(McpConfig(transport="stdio", host="0.0.0.0"))


def test_mcp_cli_fails_before_listening_on_non_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["phlo-mcp", "--transport", "streamable-http", "--host", "0.0.0.0"]
    )
    with pytest.raises(SystemExit, match="Bind to 127.0.0.1.*authenticated reverse proxy"):
        main()


class _FakeResponse:
    def __init__(self, payload):  # noqa: ANN001
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):  # noqa: ANN201
        return self._payload


def test_api_client_takes_only_config_and_uses_shared_tracer() -> None:
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    assert isinstance(client._tracer, CanonicalTracer)
    with pytest.raises(TypeError):
        PhloApiClient(McpConfig(), **{"tracer_name": "phlo.mcp"})


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
        if url.endswith("/api/v1/operations"):
            return _FakeResponse(
                {
                    "scope": "project",
                    "items": [{"id": "op-123", "kind": "workflow.apply", "status": "failed"}],
                    "next_cursor": None,
                }
            )
        if url.endswith("/api/v1/operations/package.install%3Aphlo-x/agent-context"):
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
        if url.endswith("/api/v1/assets/silver/orders/materializations"):
            assert params == {"env": "staging", "limit": 10}
            return _FakeResponse(
                {
                    "env": "staging",
                    "items": [{"run_id": "run-123", "timestamp": "2026-01-01T00:00:00Z"}],
                    "next_cursor": None,
                }
            )
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
    assert (
        client.list_operations(status="failed", kind="workflow.apply", query="orders", limit=2)[
            "items"
        ][0]["id"]
        == "op-123"
    )
    assert client.get_operation_context("package.install:phlo-x")["identifiers"]["trace_ids"] == [
        "trace-123"
    ]
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
    assert (
        client.get_materialization_history("silver/orders", "staging")["items"][0]["run_id"]
        == "run-123"
    )
    assert client.get_logs_query_link()["url"] == "http://logs.test"
    assert client.get_metrics_query_link()["url"] == "http://metrics.test"
    assert "http://example.test/api/observability/health" in seen_urls
    assert seen_params[seen_urls.index("http://example.test/api/v1/operations")] == {
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
        "phlo://runtime/config",
        "phlo://runtime/contracts",
        "phlo://runtime/dashboards",
        "phlo://runtime/plugins",
        "phlo://runtime/services",
    ]
    assert template_uris == [
        "phlo://docs/packages/{package_name}",
        "phlo://runtime/contracts/{table_name}",
        "phlo://runtime/environments/{env}/assets",
        "phlo://runtime/environments/{env}/assets/{asset_id}",
        "phlo://runtime/environments/{env}/runs",
        "phlo://runtime/environments/{env}/runs/{run_id}",
        "phlo://runtime/environments/{env}/schemas/{asset_id}",
        "phlo://runtime/operations/{operation_id}",
        "phlo://runtime/services/{service_name}",
    ]


def test_cli_docs_resource_includes_unloaded_builtin_commands(monkeypatch) -> None:
    from phlo.cli.lazy import LazyPhloGroup

    root = LazyPhloGroup("phlo")
    root.lazy_commands = {
        "doctor": ("phlo.cli.commands.doctor:doctor_cmd", "Diagnose setup."),
        "services": ("phlo.cli.commands.services:_register_commands", "Manage services."),
    }
    monkeypatch.setattr("phlo.cli.main.cli", root)
    server = create_server(McpConfig())
    resource = next(
        resource
        for uri, resource in server._resource_manager._resources.items()
        if str(uri) == "phlo://docs/cli"
    )

    rendered = resource.fn()

    assert "`phlo doctor`" in rendered
    assert "`phlo services`" in rendered
    assert "Manage Phlo infrastructure services" in rendered


def test_package_docs_resource_reads_matching_section(tmp_path, monkeypatch) -> None:
    docs = tmp_path / "docs" / "reference"
    docs.mkdir(parents=True)
    (docs / "packages.md").write_text(
        "# Packages\n\n## phlo-mcp\n\nMCP package details.\n\n## phlo-dlt\n\nDLT package details.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    server = create_server(McpConfig())
    template = next(
        template
        for template in server._resource_manager._templates.values()
        if template.uri_template == "phlo://docs/packages/{package_name}"
    )

    rendered = template.fn("phlo-mcp")

    assert rendered == "# phlo-mcp\n\nMCP package details.\n"
    assert "DLT package details" not in rendered


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
    seen_gets: list[tuple[str, object]] = []

    def fake_post(url: str, params=None, json=None, headers=None, timeout=30.0):  # noqa: ANN001
        seen_posts.append({"url": url, "params": params, "json": json})
        assert headers == {"Authorization": "Bearer secret"}
        return _FakeResponse({"queued": True})

    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        seen_gets.append((url, params))
        return _FakeResponse({"run_id": "run-123", "status": "STARTED"})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.post", fake_post)
    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test", api_token="secret"))

    client.create_workflow(domain="sales", table="orders", unique_key="id", provider="sling")
    client.materialize_asset(
        "silver/orders",
        "staging",
        job_name="orders_job",
        partition_key="2026-04-26",
        idempotency_key="key-1",
    )
    client.retry_run("run-123", "prod", dry_run=False, confirmed=True, idempotency_key="key-2")
    client.cancel_run("run-123", "prod", reason="stuck", idempotency_key="key-3")
    client.backfill_asset(
        "silver/orders",
        "staging",
        job_name="orders_job",
        partition_set_name="orders_partitions",
        partitions=["2026-04-26"],
        idempotency_key="key-4",
    )
    client.list_partitions("silver/orders", "staging", limit=5)

    assert [post["url"] for post in seen_posts] == [
        "http://example.test/api/authoring/workflows",
        "http://example.test/api/v1/assets/silver/orders/materialize",
        "http://example.test/api/v1/runs/run-123/retry",
        "http://example.test/api/v1/runs/run-123/cancel",
        "http://example.test/api/v1/assets/silver/orders/backfill",
    ]
    assert [post["params"] for post in seen_posts] == [
        None,
        {"env": "staging"},
        {"env": "prod"},
        {"env": "prod"},
        {"env": "staging"},
    ]
    assert [post["json"] for post in seen_posts[1:]] == [
        {
            "job_name": "orders_job",
            "dry_run": True,
            "idempotency_key": "key-1",
            "partition_key": "2026-04-26",
        },
        {
            "expected_status": "FAILURE",
            "dry_run": False,
            "confirmed": True,
            "idempotency_key": "key-2",
        },
        {
            "expected_status": "STARTED",
            "dry_run": True,
            "confirmed": False,
            "reason": "stuck",
            "idempotency_key": "key-3",
        },
        {
            "job_name": "orders_job",
            "partition_set_name": "orders_partitions",
            "selection": "explicit",
            "dry_run": True,
            "confirmed": False,
            "idempotency_key": "key-4",
            "partitions": ["2026-04-26"],
        },
    ]
    assert seen_gets == [
        (
            "http://example.test/api/v1/assets/silver/orders/partitions",
            {"env": "staging", "limit": 5},
        ),
    ]


def test_actions_generate_distinct_idempotency_keys_when_caller_omits_one(monkeypatch) -> None:
    keys: list[str] = []

    def fake_post(url: str, params=None, json=None, headers=None, timeout=30.0):  # noqa: ANN001
        keys.append(json["idempotency_key"])
        return _FakeResponse({"queued": True})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.post", fake_post)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    client.retry_run("run-123", "prod")
    client.retry_run("run-123", "prod")

    assert len(set(keys)) == 2
    assert all(key.startswith("phlo-mcp-") for key in keys)


def test_install_plugin_applies_exactly_the_planned_install(monkeypatch) -> None:
    plan = {
        "plan_id": "abc",
        "registry_name": "trino",
        "package_name": "phlo-trino",
        "package_spec": "phlo-trino==1.0",
        "installed_version": None,
    }
    seen: list[tuple[str, object]] = []

    def fake_post(url: str, params=None, json=None, headers=None, timeout=30.0):  # noqa: ANN001
        seen.append((url, json))
        if url.endswith("/install-plan"):
            return _FakeResponse({"plan": plan, "plan_token": "tok", "operation_id": "op"})
        return _FakeResponse({"status": "succeeded", "plan_token": "tok"})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.post", fake_post)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test", api_token="secret"))

    assert client.install_plugin("trino")["status"] == "succeeded"
    assert seen == [
        ("http://example.test/api/v1/admin/packages/trino/install-plan", {}),
        (
            "http://example.test/api/v1/admin/packages/trino/install",
            {
                "plan": plan,
                "confirmation_token": "tok",
                "idempotency_key": "phlo-mcp-install-tok",
            },
        ),
    ]


def test_install_plugin_does_not_apply_a_rejected_plan(monkeypatch) -> None:
    seen: list[str] = []

    def fake_post(url: str, params=None, json=None, headers=None, timeout=30.0):  # noqa: ANN001
        seen.append(url)
        return httpx.Response(400, json={"detail": "untrusted"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("phlo_mcp.api_client.httpx.post", fake_post)
    result = PhloApiClient(McpConfig(api_base_url="http://example.test")).install_plugin("evil")

    assert result["error"]["code"] == "phlo.api.bad_request"
    assert seen == ["http://example.test/api/v1/admin/packages/evil/install-plan"]


def test_search_quality_lineage_and_schema_reads_use_environment_scoped_routes(
    monkeypatch,
) -> None:
    seen: list[tuple[str, dict[str, object]]] = []
    quality_pages = {
        None: {
            "env": "prod",
            "items": [
                {"asset_id": "silver/orders", "check_name": "a", "latest": {"run_id": "r1"}},
                {"asset_id": "silver/users", "check_name": "b", "latest": {"run_id": "r1"}},
            ],
            "unverified_assets": [],
            "next_cursor": "page-2",
        },
        "page-2": {
            "env": "prod",
            "items": [
                {"asset_id": "silver/orders", "check_name": "c", "latest": {"run_id": "r2"}},
                {"asset_id": "silver/orders", "check_name": "d", "latest": None},
            ],
            "unverified_assets": ["gold/kpis"],
            "next_cursor": None,
        },
    }

    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        seen.append((url, dict(params)))
        if url.endswith("/api/v1/quality-checks"):
            return _FakeResponse(quality_pages[params.get("cursor")])
        return _FakeResponse({"env": params["env"], "items": [], "next_cursor": None})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test"))

    quality = client.get_quality_results("prod", asset_key="silver/orders", run_id="r2")
    assert [item["check_name"] for item in quality["items"]] == ["c"]
    assert quality["unverified_assets"] == ["gold/kpis"]
    assert quality["next_cursor"] is None
    seen.clear()

    client.search_assets("orders", "prod", limit=5)
    client.search_runs("staging", "failed", cursor="next")
    client.search_runs("staging")
    client.get_lineage("silver/orders", "prod", direction="upstream", depth=2)
    client.diff_schema("silver/orders", "prod", from_run="r1")

    assert seen == [
        (
            "http://example.test/api/v1/search",
            {"env": "prod", "q": "orders", "kind": "asset", "limit": 5},
        ),
        (
            "http://example.test/api/v1/search",
            {"env": "staging", "q": "failed", "kind": "run", "limit": 20, "cursor": "next"},
        ),
        ("http://example.test/api/v1/runs", {"env": "staging", "limit": 20}),
        (
            "http://example.test/api/v1/assets/silver/orders/neighbors",
            {"env": "prod", "direction": "upstream", "depth": 2},
        ),
        (
            "http://example.test/api/v1/assets/silver/orders/schema-diff",
            {"env": "prod", "from_run": "r1"},
        ),
    ]


def test_quality_results_return_the_first_page_error(monkeypatch) -> None:
    def fake_get(url: str, params=None, headers=None, timeout=10.0):  # noqa: ANN001
        return httpx.Response(503, json={"detail": "down"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    result = PhloApiClient(McpConfig()).get_quality_results("prod", asset_key="silver/orders")

    assert result["error"]["code"] == "phlo.api.unavailable"
    assert "items" not in result


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
        "list_runs",
        "get_run",
        "get_materialization_history",
        "get_run_logs",
        "get_run_trace_spans",
        "get_trace_spans",
        "render_trace_spans_tree",
        "inspect_materialization",
        "get_asset_materialization_trace",
        "render_materialization_trace_tree",
        "render_run_trace_tree",
        "list_assets",
        "get_asset",
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
        env: str,
        *,
        job_name: str,
        dry_run: bool = True,
        partition_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        return {"asset_key_path": asset_key_path, "env": env, "queued": False}

    monkeypatch.setattr(PhloApiClient, "materialize_asset", fake_materialize_asset)
    server = create_server(McpConfig(api_token="secret-token", enable_write_tools=True))
    tool = next(
        tool for tool in server._tool_manager.list_tools() if tool.name == "materialize_asset"
    )

    result = tool.fn(
        "silver/orders", "staging", "orders_job", dry_run=True, partition_key="2026-04-26"
    )

    assert result["payload"]["env"] == "staging"
    assert result["audit_context"] == {
        "operation": "materialize_asset",
        "target": {
            "asset_key_path": "silver/orders",
            "env": "staging",
            "job_name": "orders_job",
            "partition_key": "2026-04-26",
        },
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


@pytest.mark.parametrize(
    "tool_name,method_name",
    [("list_plugins", "get_plugins"), ("get_platform_health", "get_platform_health")],
)
@pytest.mark.parametrize("fail", [False, True])
def test_registered_tools_trace_success_and_failure_without_changing_results(
    monkeypatch, tool_name: str, method_name: str, fail: bool
) -> None:
    from observe_core import flush
    from observe_core.runtime import get_runtime

    from phlo import telemetry

    def response(_self):
        if fail:
            raise RuntimeError("backend unavailable")
        return {"healthy": True}

    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "false")
    monkeypatch.setattr(PhloApiClient, method_name, response)
    telemetry.reset_for_tests()
    try:
        assert telemetry.configure(enabled=True, runtime_backend="capture", drains=[])
        server = create_server(McpConfig())
        tool = next(tool for tool in server._tool_manager.list_tools() if tool.name == tool_name)
        assert tool.parameters["properties"] == {}
        if fail:
            with pytest.raises(RuntimeError, match="backend unavailable"):
                tool.fn()
        else:
            assert tool.fn()["payload"] == {"healthy": True}
        flush()
        events = [
            record
            for record in get_runtime()._backend.payloads()
            if record["event"].startswith(("mcp.", "phlo.observability."))
        ]
    finally:
        telemetry.reset_for_tests()

    by_name = {event["event"]: event for event in events}
    request = by_name["mcp.request"]["correlation"]
    execution = by_name["mcp.tool.execute"]["correlation"]
    assert sum(event["event"] == "mcp.request" for event in events) == 1
    assert sum(event["event"] == "mcp.tool.execute" for event in events) == 1
    assert execution["parent_span_id"] == request["span_id"]
    assert execution["trace_id"] == request["trace_id"]
    assert by_name["mcp.tool.execute"]["attributes"]["mcp.tool.name"] == tool_name
    assert all(event["outcome"] == ("failure" if fail else "success") for event in events)
    if tool_name == "get_platform_health":
        health = by_name["phlo.observability.health"]["correlation"]
        assert health["parent_span_id"] == execution["span_id"]


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_canonical_reads_preserve_pages_filters_and_bearer(monkeypatch, env) -> None:
    seen = []
    payload = {"env": env, "items": [{"id": "orders"}], "next_cursor": "next"}

    def fake_get(url, *, params, headers, timeout):
        seen.append((url, params))
        assert headers == {"Authorization": "Bearer user-token"}
        return _FakeResponse(payload)

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    client = PhloApiClient(McpConfig(api_base_url="http://example.test", api_token="user-token"))
    assert client.list_assets(env, limit=2, cursor="asset-cursor") == payload
    assert client.get_asset("silver/orders", env) == payload
    assert (
        client.list_runs(env, limit=3, job_id="daily", status="FAILURE", cursor="run-cursor")
        == payload
    )
    assert client.get_run("run-1", env) == payload
    assert seen == [
        ("http://example.test/api/v1/assets", {"env": env, "limit": 2, "cursor": "asset-cursor"}),
        ("http://example.test/api/v1/assets/silver/orders", {"env": env}),
        (
            "http://example.test/api/v1/runs",
            {
                "env": env,
                "limit": 3,
                "job_id": "daily",
                "status": "FAILURE",
                "cursor": "run-cursor",
            },
        ),
        ("http://example.test/api/v1/runs/run-1", {"env": env}),
    ]


@pytest.mark.parametrize("env", [None, "", "bad|environment", "PROD", "a" * 64])
def test_canonical_client_rejects_invalid_environment_before_http(monkeypatch, env) -> None:
    def unexpected_http(*args, **kwargs):
        pytest.fail("Invalid environment reached HTTP")

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", unexpected_http)
    client = PhloApiClient(McpConfig())
    for method, args in [
        (client.list_assets, ()),
        (client.get_asset, ("orders",)),
        (client.list_runs, ()),
        (client.get_run, ("run-1",)),
    ]:
        with pytest.raises(ValueError, match="env must be a configured environment ID"):
            method(*args, env=env)


@pytest.mark.parametrize("name,maximum", [("list_assets", 500), ("list_runs", 100)])
def test_canonical_list_bounds_before_http(monkeypatch, name, maximum) -> None:
    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", lambda *a, **kw: pytest.fail("HTTP"))
    client = PhloApiClient(McpConfig())
    for limit in [0, maximum + 1]:
        with pytest.raises(ValueError, match="limit must be between"):
            getattr(client, name)("prod", limit=limit)


def test_canonical_registered_tools_and_resources(monkeypatch) -> None:
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params))
        return _FakeResponse({"env": params["env"], "id": "silver/orders", "next_cursor": "next"})

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    server = create_server(McpConfig(api_base_url="http://example.test"))
    for name in ["list_assets", "get_asset", "list_runs", "get_run"]:
        tool = server._tool_manager.get_tool(name)
        assert tool.parameters["properties"]["env"]["pattern"] == "^[a-z][a-z0-9_-]{0,62}$"
        assert "env" in tool.parameters["required"]
        assert "q" not in tool.parameters["properties"]
        assert "query" not in tool.parameters["properties"]
        for arguments in [{}, {"env": "bad|environment"}]:
            with pytest.raises(Exception, match="validation"):
                asyncio.run(server.call_tool(name, arguments))
    assert not calls
    for name, arguments in [
        ("list_assets", {"env": "analytics", "cursor": "next"}),
        ("get_asset", {"env": "analytics", "asset_id": "silver/orders"}),
        ("list_runs", {"env": "analytics", "status": "FAILURE"}),
        ("get_run", {"env": "analytics", "run_id": "run-1"}),
    ]:
        asyncio.run(server.call_tool(name, arguments))
        assert calls[-1][1]["env"] == "analytics"
    for suffix in ["assets", "assets/silver%2Forders", "runs", "runs/run-1"]:
        result = list(
            asyncio.run(server.read_resource(f"phlo://runtime/environments/analytics/{suffix}"))
        )
        assert json.loads(result[0].content)["env"] == "analytics"
        assert calls[-1][0].endswith(f"/api/v1/{suffix.replace('%2F', '/')}")
    count = len(calls)
    with pytest.raises(Exception, match="string_pattern_mismatch"):
        asyncio.run(server.read_resource("phlo://runtime/environments/PROD/assets"))
    assert len(calls) == count
    tools_resource = next(
        r for r in server._resource_manager._resources.values() if r.name == "mcp_tools"
    )
    contracts = {tool["name"]: tool for tool in tools_resource.fn()}
    for name in ["list_assets", "get_asset", "list_runs", "get_run"]:
        assert contracts[name]["required_scope"] == "lakehouse:read"


@pytest.mark.parametrize(
    "name,args",
    [("list_assets", ()), ("get_asset", ("orders",)), ("list_runs", ()), ("get_run", ("run-1",))],
)
@pytest.mark.parametrize(
    "status,code",
    [
        (400, "phlo.api.bad_request"),
        (401, "phlo.auth.unauthorized"),
        (403, "phlo.auth.forbidden"),
        (404, "phlo.resource.not_found"),
        (503, "phlo.api.unavailable"),
    ],
)
def test_canonical_reads_preserve_http_errors(monkeypatch, name, args, status, code) -> None:
    def fake_get(url, **kwargs):
        return httpx.Response(
            status, json={"detail": "Explicit API failure"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    result = getattr(PhloApiClient(McpConfig()), name)(*args, env="staging")
    assert result["error"]["code"] == code
    assert result["error"]["message"] == "Explicit API failure"
    assert "items" not in result


def test_canonical_unreachable_api_is_not_empty_inventory(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    assert (
        PhloApiClient(McpConfig()).list_assets("staging")["error"]["code"] == "phlo.api.unreachable"
    )


@pytest.mark.parametrize(
    "name,write_tool",
    [
        ("search_assets", False),
        ("search_runs", False),
        ("get_quality_results", False),
        ("get_lineage", False),
        ("diff_schema", False),
        ("get_materialization_history", False),
        ("inspect_materialization", False),
        ("get_asset_materialization_trace", False),
        ("render_materialization_trace_tree", False),
        ("materialize_asset", True),
        ("retry_failed_run", True),
        ("cancel_run", True),
        ("backfill_asset", True),
        ("list_partitions", True),
        ("get_run_status", True),
    ],
)
def test_environment_scoped_tools_require_a_valid_env(name, write_tool) -> None:
    server = create_server(McpConfig(api_token="secret", enable_write_tools=write_tool))
    tool = server._tool_manager.get_tool(name)

    assert "env" in tool.parameters["required"]
    assert tool.parameters["properties"]["env"]["pattern"] == "^[a-z][a-z0-9_-]{0,62}$"


def test_environment_schema_resource_projects_asset_columns(monkeypatch) -> None:
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params))
        if url.endswith("/missing"):
            return httpx.Response(404, json={"detail": "nope"}, request=httpx.Request("GET", url))
        return _FakeResponse(
            {
                "id": "silver/orders",
                "columns": [{"name": "id"}],
                "column_lineage": {"id": []},
                "schema_source": "catalog",
                "schema_observed_at": None,
                "description": "not part of the schema view",
            }
        )

    monkeypatch.setattr("phlo_mcp.api_client.httpx.get", fake_get)
    server = create_server(McpConfig(api_base_url="http://example.test"))

    def read(uri: str) -> dict[str, object]:
        return json.loads(list(asyncio.run(server.read_resource(uri)))[0].content)

    assert read("phlo://runtime/environments/staging/schemas/silver%2Forders") == {
        "env": "staging",
        "asset_id": "silver/orders",
        "columns": [{"name": "id"}],
        "column_lineage": {"id": []},
        "schema_source": "catalog",
        "schema_observed_at": None,
    }
    assert calls[-1] == ("http://example.test/api/v1/assets/silver/orders", {"env": "staging"})
    assert read("phlo://runtime/environments/staging/schemas/missing")["error"]["code"] == (
        "phlo.resource.not_found"
    )
