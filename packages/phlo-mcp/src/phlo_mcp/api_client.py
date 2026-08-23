"""HTTP client helpers for phlo-mcp.

PhloApiClient wraps phlo-api and Observatory routes for MCP tool calls.
Transport failures are never raised: they come back as {"error": ...}
envelopes so each tool call yields a structured result the agent can
act on. Requests carry a bearer token when configured, span OpenTelemetry
traces, and use fixed timeouts (10s GET, 30s POST).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from opentelemetry import trace

from phlo_mcp.config import McpConfig
from phlo_mcp.errors import map_httpx_error


class PhloApiClient:
    """Small typed wrapper around phlo-api routes."""

    _OBSERVATORY_PREFIX = "/api/observatory"

    def __init__(self, config: McpConfig, *, tracer_name: str = "phlo.mcp") -> None:
        self._config = config
        self._tracer = trace.get_tracer(tracer_name)

    @property
    def api_base_url(self) -> str:
        """Return the base URL of the configured phlo-api instance."""
        return self._config.api_base_url

    @property
    def headers(self) -> dict[str, str]:
        """Return request headers, including the bearer token when an API token is configured."""
        if self._config.api_token:
            return {"Authorization": f"Bearer {self._config.api_token}"}
        return {}

    def get_platform_health(self) -> dict[str, Any]:
        """Fetch the overall platform health report from the observability API."""
        return self._get_object("/api/observability/health")

    def get_config(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the deployed platform configuration."""
        return self._get_json("/api/config")

    def get_plugins(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the installed plugin inventory."""
        return self._get_json("/api/plugins")

    def install_plugin(self, package_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Install a plugin package into the platform via Observatory."""
        return self._post_json(
            f"{self._OBSERVATORY_PREFIX}/packages/install", json={"package_name": package_name}
        )

    def get_services(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the registry of managed services."""
        return self._get_json("/api/services")

    def get_service_info(self, service_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch detailed information for one managed service."""
        return self._get_json(f"/api/services/{service_name}")

    def get_assets(self) -> dict[str, Any] | list[dict[str, Any]]:
        """List assets, unwrapping the items envelope into a bare list when present."""
        return self._observatory_items("/assets")

    def get_asset_details(self, asset_key_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch full metadata for a single asset by key path."""
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/assets/{asset_key_path}")

    def list_operations(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """List operations, optionally filtered by status, kind, or free-text query."""
        params = {
            key: value
            for key, value in {
                "status": status,
                "kind": kind,
                "q": query,
                "limit": limit,
            }.items()
            if value is not None
        }
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/operations", params=params)

    def get_operation_context(self, operation_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the agent-oriented context document for one operation."""
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/operations/{operation_id}/agent-context")

    def get_contracts(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch all declared data contracts."""
        return self._get_json("/api/contracts")

    def get_contract(self, table_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the data contract bound to one table."""
        return self._get_json(f"/api/contracts/{table_name}")

    def create_workflow(
        self,
        *,
        domain: str,
        table: str,
        unique_key: str,
        cron: str = "0 */1 * * *",
        api_base_url: str | None = None,
        fields: list[str] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Create a scheduled authoring workflow for a table keyed by its unique key."""
        payload: dict[str, Any] = {
            "domain": domain,
            "table": table,
            "unique_key": unique_key,
            "cron": cron,
            "fields": fields or [],
        }
        if api_base_url:
            payload["api_base_url"] = api_base_url
        if provider:
            payload["provider"] = provider
        return self._post_json("/api/authoring/workflows", json=payload)

    def validate_workflow(self, workflow_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Validate a workflow definition file through the authoring API."""
        return self._post_json(
            "/api/authoring/workflows/validate", json={"workflow_path": workflow_path}
        )

    def validate_schema(self, schema_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Validate a schema definition file through the authoring API."""
        return self._post_json("/api/authoring/schemas/validate", json={"schema_path": schema_path})

    def list_templates(self) -> dict[str, Any] | list[dict[str, Any]]:
        """List the authoring templates available for workflow creation."""
        return self._get_json("/api/authoring/templates")

    def list_workflows(
        self,
        *,
        search: str | None = None,
        group: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Page through workflows, optionally narrowed by search text or group."""
        params = {
            key: value
            for key, value in {
                "search": search,
                "group": group,
                "limit": limit,
                "cursor": cursor,
            }.items()
            if value
        }
        return self._get_json("/api/authoring/workflows", params=params or None)

    def lint_project(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Run the authoring linter over the current project."""
        return self._post_json("/api/authoring/project/lint", json={})

    def run_doctor(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Run the authoring doctor diagnostics over the deployment."""
        return self._get_json("/api/authoring/doctor")

    def search_assets(
        self, query: str, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Search assets by free-text query with limit and cursor pagination."""
        params: dict[str, Any] = {"q": query, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/search", params=params)

    def search_contracts(
        self, query: str, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Filter contracts client-side by case-insensitive substring match on the query."""
        contracts = self.get_contracts()
        if isinstance(contracts, dict) and "error" in contracts:
            return contracts
        items = contracts if isinstance(contracts, list) else contracts.get("items", [])
        filtered = [item for item in items if query.lower() in str(item).lower()]
        return {"items": filtered[:limit], "next_cursor": None}

    def search_runs(
        self, query: str | None = None, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Search runs by optional query with limit and cursor pagination."""
        params: dict[str, Any] = {"limit": limit}
        if query:
            params["q"] = query
        if cursor:
            params["cursor"] = cursor
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/runs", params=params)

    def get_quality_results(
        self, asset_key: str | None = None, run_id: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch quality results, optionally narrowed to a single asset or run."""
        payload = self._get_json(f"{self._OBSERVATORY_PREFIX}/quality")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return payload
        items = payload["items"]
        if asset_key:
            items = [item for item in items if item.get("asset_id") == asset_key]
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        return {**payload, "items": items}

    def get_lineage(
        self, asset_key: str, *, direction: str = "both", depth: int = 1
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch neighboring lineage edges for an asset up to the given depth."""
        return self._get_json(
            f"{self._OBSERVATORY_PREFIX}/asset-graph/neighbors",
            params={"asset_key": asset_key, "direction": direction, "depth": depth},
        )

    def diff_schema(
        self, asset_key: str, *, from_run: str | None = None, to_run: str | None = None
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Diff an asset's schema between two runs."""
        return self._post_json(
            f"{self._OBSERVATORY_PREFIX}/schemas/diff",
            json={"asset_key": asset_key, "from_run": from_run, "to_run": to_run},
        )

    def get_service_status(self) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch per-service health status from the observability API."""
        return self._get_json("/api/observability/services")

    def get_recent_alerts(
        self, limit: int = 5, cursor: str | None = None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch the most recent alerts with cursor pagination."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._get_json("/api/observability/alerts", params=params)

    def get_dashboard_links(self) -> list[dict[str, Any]] | dict[str, Any]:
        """Fetch links to the observability dashboards."""
        return self._get_json("/api/observability/dashboards")

    def get_run_logs(
        self,
        run_id: str,
        *,
        level: str | None = None,
        limit: int = 200,
        query: str | None = None,
        regex: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch log entries for a run; unavailable logs degrade to an empty-entries envelope."""
        params: dict[str, Any] = {"limit": limit}
        for key, value in {
            "level": level,
            "query": query,
            "regex": regex,
            "since": since,
            "until": until,
            "cursor": cursor,
        }.items():
            if value:
                params[key] = value
        payload = self._get_json(f"/api/loki/runs/{run_id}", params=params)
        if isinstance(payload, dict) and payload.get("error"):
            return {"entries": [], "has_more": False, "unavailable": payload["error"]}
        return payload

    def search_run_logs(
        self,
        run_id: str,
        *,
        query: str,
        regex: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch run logs constrained by a required query term."""
        return self.get_run_logs(
            run_id,
            query=query,
            regex=regex,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        )

    def follow_run_logs(
        self,
        run_id: str,
        *,
        timeout_seconds: int = 30,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Stream run logs over SSE until timeout, returning events or an unavailable envelope."""
        url = f"{self.api_base_url}/api/loki/runs/{run_id}/stream"
        params = {"timeout_seconds": timeout_seconds, "limit": limit}
        events: list[dict[str, Any]] = []
        with self._tracer.start_as_current_span(
            "http.client GET",
            attributes={"http.request.method": "GET", "url.full": url},
        ):
            try:
                with httpx.stream(
                    "GET", url, params=params, headers=self.headers, timeout=timeout_seconds + 5
                ) as response:
                    response.raise_for_status()
                    event_name = "message"
                    data_lines: list[str] = []
                    for line in response.iter_lines():
                        if line.startswith("event: "):
                            event_name = line[7:]
                        elif line.startswith("data: "):
                            data_lines.append(line[6:])
                        elif line == "" and data_lines:
                            raw_data = "\n".join(data_lines)
                            try:
                                data: Any = json.loads(raw_data)
                            except json.JSONDecodeError:
                                data = raw_data
                            events.append({"event": event_name, "data": data})
                            data_lines = []
                            event_name = "message"
                    if data_lines:
                        raw_data = "\n".join(data_lines)
                        try:
                            data = json.loads(raw_data)
                        except json.JSONDecodeError:
                            data = raw_data
                        events.append({"event": event_name, "data": data})
                return {"run_id": run_id, "events": events}
            except httpx.HTTPError as exc:
                return {
                    "run_id": run_id,
                    "events": [],
                    "unavailable": map_httpx_error(exc).to_payload(),
                }

    def get_materialization_history(
        self,
        asset_key_path: str,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch recent materializations for an asset with cursor pagination."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get_json(
            f"{self._OBSERVATORY_PREFIX}/assets/{asset_key_path}/materializations",
            params=params,
        )

    def get_run_trace_spans(
        self,
        run_id: str,
        *,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch trace spans for a run; failures collapse to an empty list."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        payload = self._get_json(f"/api/observability/traces/runs/{run_id}", params=params)
        if isinstance(payload, dict) and payload.get("error"):
            return []
        return payload

    def get_logs_query_link(self, service: str | None = None) -> dict[str, Any]:
        """Build a deep link into the logs explorer, optionally scoped to a service."""
        params = {"service": service} if service else None
        return self._get_object("/api/observability/links/logs", params=params)

    def get_trace_spans(
        self,
        *,
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
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Query trace spans filtered by run, asset, job, service, name, status, or time window."""
        params: dict[str, Any] = {"limit": limit}
        for key, value in {
            "run_id": run_id,
            "asset_key": asset_key,
            "job_name": job_name,
            "service_name": service_name,
            "span_name": span_name,
            "status_code": status_code,
            "start_time": start_time,
            "end_time": end_time,
            "cursor": cursor,
        }.items():
            if value:
                params[key] = value
        payload = self._get_json("/api/observability/traces", params=params)
        if isinstance(payload, dict) and payload.get("error"):
            return []
        return payload

    def get_metrics_query_link(self, metric: str | None = None) -> dict[str, Any]:
        """Build a deep link into the metrics explorer, optionally scoped to a metric."""
        params = {"metric": metric} if metric else None
        return self._get_object("/api/observability/links/metrics", params=params)

    def materialize_asset(
        self,
        asset_key_path: str,
        *,
        dry_run: bool = True,
        partition_key: str | None = None,
        job_name: str | None = None,
        repository_location_name: str | None = None,
        repository_name: str | None = None,
        idempotency_key: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Request a materialization of an asset, dry-run by default."""
        payload: dict[str, Any] = {"dry_run": dry_run}
        if partition_key:
            payload["partition_key"] = partition_key
        if job_name:
            payload["job_name"] = job_name
        if repository_location_name:
            payload["repository_location_name"] = repository_location_name
        if repository_name:
            payload["repository_name"] = repository_name
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if tags:
            payload["tags"] = tags
        return self._post_json(
            f"{self._OBSERVATORY_PREFIX}/assets/{asset_key_path}/materialize", json=payload
        )

    def retry_run(
        self,
        run_id: str,
        *,
        dry_run: bool = True,
        strategy: str = "FROM_FAILURE",
        idempotency_key: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Retry a run from its first failure, dry-run by default."""
        payload: dict[str, Any] = {"dry_run": dry_run}
        if strategy != "FROM_FAILURE":
            payload["strategy"] = strategy
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if tags:
            payload["tags"] = tags
        return self._post_json(f"{self._OBSERVATORY_PREFIX}/runs/{run_id}/retry", json=payload)

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Cancel a run, optionally recording a reason."""
        payload: dict[str, Any] = {}
        if reason:
            payload["reason"] = reason
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return self._post_json(f"{self._OBSERVATORY_PREFIX}/runs/{run_id}/cancel", json=payload)

    def backfill_asset(
        self,
        asset_key_path: str,
        *,
        dry_run: bool = True,
        partitions: list[str] | None = None,
        partition_range: dict[str, str] | None = None,
        partition_set_name: str | None = None,
        repository_location_name: str | None = None,
        repository_name: str | None = None,
        idempotency_key: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Request a partitioned backfill of an asset, dry-run by default."""
        payload: dict[str, Any] = {"dry_run": dry_run}
        if partitions:
            payload["partitions"] = partitions
        if partition_range:
            payload["partition_range"] = partition_range
        if partition_set_name:
            payload["partition_set_name"] = partition_set_name
        if repository_location_name:
            payload["repository_location_name"] = repository_location_name
        if repository_name:
            payload["repository_name"] = repository_name
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if tags:
            payload["tags"] = tags
        return self._post_json(
            f"{self._OBSERVATORY_PREFIX}/assets/{asset_key_path}/backfill", json=payload
        )

    def list_partitions(self, asset_key_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """List the partitions defined for an asset."""
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/assets/{asset_key_path}/partitions")

    def get_run_status(self, run_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the current execution status of a run."""
        return self._get_json(f"{self._OBSERVATORY_PREFIX}/runs/{run_id}/status")

    def _observatory_items(self, path: str) -> dict[str, Any] | list[dict[str, Any]]:
        payload = self._get_json(f"{self._OBSERVATORY_PREFIX}{path}")
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        return payload

    # Transport failures are returned as {"error": ...} payloads instead of being
    # raised, so every tool call yields a structured envelope the agent can act
    # on rather than crashing the MCP request.
    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        url = f"{self.api_base_url}{path}"
        with self._tracer.start_as_current_span(
            "http.client GET",
            attributes={
                "http.request.method": "GET",
                "url.full": url,
            },
        ):
            try:
                response = httpx.get(url, params=params, headers=self.headers, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                return {"error": map_httpx_error(exc).to_payload()}

    def _get_object(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch an endpoint whose response contract is a JSON object."""
        payload = self._get_json(path, params=params)
        if not isinstance(payload, dict):
            raise TypeError(f"Expected JSON object from {path}, got {type(payload).__name__}")
        return payload

    def _post_json(
        self,
        path: str,
        *,
        json: dict[str, Any],
    ) -> dict[str, Any] | list[dict[str, Any]]:
        url = f"{self.api_base_url}{path}"
        with self._tracer.start_as_current_span(
            "http.client POST",
            attributes={
                "http.request.method": "POST",
                "url.full": url,
            },
        ):
            try:
                response = httpx.post(url, json=json, headers=self.headers, timeout=30.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                return {"error": map_httpx_error(exc).to_payload()}
