"""HTTP client helpers for phlo-mcp.

PhloApiClient wraps phlo-api routes for MCP tool calls. Asset, run, search,
quality, lineage, operation, and package-install calls use the canonical
/api/v1 resources; it no longer calls the deprecated /api/observatory routes.
Transport failures are never raised: they come back as {"error": ...}
envelopes so each tool call yields a structured result the agent can
act on. Requests carry a bearer token when configured, span OpenTelemetry
traces, and use fixed timeouts (10s GET, 30s POST).
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx

from phlo.config_schema import EnvironmentId
from phlo_mcp.config import McpConfig
from phlo_mcp.errors import map_httpx_error
from phlo_mcp.tracing import CanonicalTracer, get_tracer


Environment = EnvironmentId


def _environment_params(env: Environment) -> dict[str, Any]:
    if not isinstance(env, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", env):
        raise ValueError("env must be a configured environment ID")
    return {"env": env}


def _asset_path(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id.strip('/'), safe='/')}"


def _idempotency_key(key: str | None) -> str:
    """Return the caller's key, or a fresh one: every v1 action requires a key."""
    return key or f"phlo-mcp-{uuid4().hex}"


class PhloApiClient:
    """Small typed wrapper around phlo-api routes."""

    def __init__(self, config: McpConfig) -> None:
        self._config = config
        self._tracer: CanonicalTracer = get_tracer()

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
        """Plan a trusted package install, then apply exactly that plan.

        The apply request confirms the plan with its ``plan_token`` and binds an
        idempotency key derived from it, so a transport retry replays the same
        install instead of starting another.
        """
        path = f"/api/v1/admin/packages/{quote(package_name, safe='')}"
        planned = self._post_json(f"{path}/install-plan", json={})
        if not isinstance(planned, dict) or "error" in planned:
            return planned
        return self._post_json(
            f"{path}/install",
            json={
                "plan": planned["plan"],
                "confirmation_token": planned["plan_token"],
                "idempotency_key": f"phlo-mcp-install-{planned['plan_token']}",
            },
        )

    def get_services(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch the registry of managed services."""
        return self._get_json("/api/services")

    def get_service_info(self, service_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Fetch detailed information for one managed service."""
        return self._get_json(f"/api/services/{service_name}")

    def list_assets(
        self, env: Environment, *, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        """Read a canonical asset page without unwrapping its environment or cursor."""
        params = _environment_params(env)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return self._get_object("/api/v1/assets", params=params)

    def get_asset(self, asset_id: str, env: Environment) -> dict[str, Any]:
        """Read canonical asset detail in the explicitly selected environment."""
        return self._get_object(_asset_path(asset_id), params=_environment_params(env))

    def list_runs(
        self,
        env: Environment,
        *,
        limit: int = 100,
        job_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Read one canonical run page using only supported API filters."""
        params = _environment_params(env)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params["limit"] = limit
        for key, value in {"job_id": job_id, "status": status, "cursor": cursor}.items():
            if value is not None:
                params[key] = value
        return self._get_object("/api/v1/runs", params=params)

    def get_run(self, run_id: str, env: Environment) -> dict[str, Any]:
        """Read canonical run detail in the explicitly selected environment."""
        return self._get_object(
            f"/api/v1/runs/{quote(run_id, safe='')}", params=_environment_params(env)
        )

    def list_operations(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List project operations, newest first, filtered before pagination."""
        params = {
            key: value
            for key, value in {
                "status": status,
                "kind": kind,
                "q": query,
                "limit": limit,
                "cursor": cursor,
            }.items()
            if value is not None
        }
        return self._get_object("/api/v1/operations", params=params)

    def get_operation_context(self, operation_id: str) -> dict[str, Any]:
        """Fetch the agent-oriented context document for one operation."""
        return self._get_object(f"/api/v1/operations/{quote(operation_id, safe='')}/agent-context")

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
        self, query: str, env: Environment, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """Search one environment's assets by case-insensitive substring."""
        return self._search(query, env, kind="asset", limit=limit, cursor=cursor)

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
        self,
        env: Environment,
        query: str | None = None,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search one environment's newest runs, or list them when no query is given."""
        if not query:
            return self.list_runs(env, limit=limit, cursor=cursor)
        return self._search(query, env, kind="run", limit=limit, cursor=cursor)

    def _search(
        self,
        query: str,
        env: Environment,
        *,
        kind: Literal["asset", "run"],
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        params = {**_environment_params(env), "q": query, "kind": kind, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get_object("/api/v1/search", params=params)

    def get_quality_results(
        self, env: Environment, *, asset_key: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        """Read every declared quality check, optionally narrowed to one asset or latest run.

        The canonical resource is paged; this reads all pages so the filters
        apply to the whole inventory, as the aggregate quality read did.
        """
        params: dict[str, Any] = {**_environment_params(env), "limit": 100}
        items: list[dict[str, Any]] = []
        while True:
            page = self._get_object("/api/v1/quality-checks", params=params)
            if "error" in page:
                return page
            items.extend(page["items"])
            if not page["next_cursor"]:
                break
            params["cursor"] = page["next_cursor"]
        if asset_key:
            items = [item for item in items if item["asset_id"] == asset_key]
        if run_id:
            items = [item for item in items if (item.get("latest") or {}).get("run_id") == run_id]
        return {**page, "items": items, "next_cursor": None}

    def get_lineage(
        self, asset_key: str, env: Environment, *, direction: str = "both", depth: int = 1
    ) -> dict[str, Any]:
        """Fetch the asset's lineage neighbourhood within ``depth`` dependency hops."""
        return self._get_object(
            f"{_asset_path(asset_key)}/neighbors",
            params={**_environment_params(env), "direction": direction, "depth": depth},
        )

    def diff_schema(
        self, asset_key: str, env: Environment, *, from_run: str, to_run: str | None = None
    ) -> dict[str, Any]:
        """Diff the schema a run wrote with a later run's, or with the current schema."""
        params = {**_environment_params(env), "from_run": from_run}
        if to_run:
            params["to_run"] = to_run
        return self._get_object(f"{_asset_path(asset_key)}/schema-diff", params=params)

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
        env: Environment,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Read one page of an asset's successful materializations, newest first."""
        params: dict[str, Any] = {**_environment_params(env), "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get_object(f"{_asset_path(asset_key_path)}/materializations", params=params)

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
        env: Environment,
        *,
        job_name: str,
        dry_run: bool = True,
        partition_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Request a materialization of an asset through one of its jobs, dry-run by default."""
        payload: dict[str, Any] = {
            "job_name": job_name,
            "dry_run": dry_run,
            "idempotency_key": _idempotency_key(idempotency_key),
        }
        if partition_key:
            payload["partition_key"] = partition_key
        return self._post_object(
            f"{_asset_path(asset_key_path)}/materialize",
            params=_environment_params(env),
            json=payload,
        )

    def retry_run(
        self,
        run_id: str,
        env: Environment,
        *,
        dry_run: bool = True,
        confirmed: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed run from its failure, dry-run by default."""
        return self._run_action(
            run_id,
            env,
            "retry",
            # The API only retries a run whose status is FAILURE, so this is the
            # only expected status that can succeed; it still rejects a run that
            # changed state before the action.
            {"expected_status": "FAILURE", "dry_run": dry_run, "confirmed": confirmed},
            idempotency_key,
        )

    def cancel_run(
        self,
        run_id: str,
        env: Environment,
        *,
        dry_run: bool = True,
        confirmed: bool = False,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a started run, optionally recording a reason; dry-run by default."""
        payload: dict[str, Any] = {
            "expected_status": "STARTED",
            "dry_run": dry_run,
            "confirmed": confirmed,
        }
        if reason:
            payload["reason"] = reason
        return self._run_action(run_id, env, "cancel", payload, idempotency_key)

    def _run_action(
        self,
        run_id: str,
        env: Environment,
        action: Literal["retry", "cancel"],
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return self._post_object(
            f"/api/v1/runs/{quote(run_id, safe='')}/{action}",
            params=_environment_params(env),
            json={**payload, "idempotency_key": _idempotency_key(idempotency_key)},
        )

    def backfill_asset(
        self,
        asset_key_path: str,
        env: Environment,
        *,
        job_name: str,
        partition_set_name: str,
        dry_run: bool = True,
        confirmed: bool = False,
        selection: Literal["explicit", "latest", "all"] = "explicit",
        partitions: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Request a partitioned backfill of an asset, dry-run by default."""
        payload: dict[str, Any] = {
            "job_name": job_name,
            "partition_set_name": partition_set_name,
            "selection": selection,
            "dry_run": dry_run,
            "confirmed": confirmed,
            "idempotency_key": _idempotency_key(idempotency_key),
        }
        if partitions:
            payload["partitions"] = partitions
        return self._post_object(
            f"{_asset_path(asset_key_path)}/backfill",
            params=_environment_params(env),
            json=payload,
        )

    def list_partitions(
        self,
        asset_key_path: str,
        env: Environment,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List one page of an asset's partition keys, newest first."""
        params: dict[str, Any] = {**_environment_params(env), "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._get_object(f"{_asset_path(asset_key_path)}/partitions", params=params)

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
        params: dict[str, Any] | None = None,
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
                response = httpx.post(
                    url, params=params, json=json, headers=self.headers, timeout=30.0
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                return {"error": map_httpx_error(exc).to_payload()}

    def _post_object(
        self,
        path: str,
        *,
        json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Post to an endpoint whose response contract is a JSON object."""
        payload = self._post_json(path, json=json, params=params)
        if not isinstance(payload, dict):
            raise TypeError(f"Expected JSON object from {path}, got {type(payload).__name__}")
        return payload
