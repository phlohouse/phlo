"""Dagster API Router.

Endpoints for interacting with the Dagster GraphQL API.
Provides health metrics, asset listing, and materialization history.

This module acts as a proxy to the Dagster GraphQL API, translating
requests/responses into Observatory-compatible formats and providing
higher-level operations like asset graph construction and impact analysis.

Key Endpoints:
    GET /connection: Check Dagster connectivity.
    GET /health: Get platform health metrics.
    GET /assets: List all assets.
    GET /assets/{key}: Get asset details.
    GET /assets/{key}/history: Get materialization history.
    GET /graph: Get full asset dependency graph.
    GET /graph/neighbors: Get subgraph around an asset.
    GET /graph/impact: Get downstream impact analysis.

Environment Variables:
    DAGSTER_GRAPHQL_URL: URL for Dagster GraphQL endpoint.

Example:
    Fetching the asset graph:

    .. code-block:: bash

        curl http://localhost:4000/api/dagster/graph

"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.helpers.partitions import partition_range as _partition_range
from phlo.logging import get_bound_correlation_context, get_logger
from phlo.security.mode import requires_http_authorization
from phlo.security.service_identity import (
    build_scoped_service_headers,
    build_service_headers,
    load_service_identity_credentials,
)
from phlo_api.observatory_api.quality import fetch_quality_snapshot

logger = get_logger(__name__)

router = APIRouter(tags=["dagster"])

DEFAULT_DAGSTER_URL = "http://dagster:3000/graphql"


def resolve_dagster_url(override: str | None = None) -> str:
    """Resolve the Dagster GraphQL URL from override, environment, or default.

    A localhost override is replaced by the project environment URL when
    DAGSTER_GRAPHQL_URL is set, so requests follow the deployed Dagster port.
    """
    env_url = project_env_value("DAGSTER_GRAPHQL_URL")
    if override and override.strip():
        if env_url and override.strip() == "http://localhost:3000/graphql":
            return resolve_url(env_url, port_env_var="DAGSTER_PORT")
        return resolve_url(override, port_env_var="DAGSTER_PORT")
    return resolve_url(env_url or DEFAULT_DAGSTER_URL, port_env_var="DAGSTER_PORT")


# --- GraphQL Queries ---

VERSION_QUERY = """
query Version {
    version
}
"""

HEALTH_QUERY = """
query HealthMetrics {
    assetsOrError {
        ... on AssetConnection {
            nodes {
                key { path }
                assetMaterializations(limit: 1) {
                    timestamp
                }
            }
        }
        ... on PythonError { message }
    }
    runsOrError(filter: { statuses: [FAILURE] }, limit: 100) {
        ... on Runs {
            results {
                id
                status
                startTime
                endTime
            }
        }
        ... on PythonError { message }
    }
}
"""

ASSETS_QUERY = """
query AssetsQuery {
    assetsOrError {
        __typename
        ... on AssetConnection {
            nodes {
                id
                key { path }
                definition {
                    description
                    computeKind
                    groupName
                    hasMaterializePermission
                    opNames
                }
                assetMaterializations(limit: 1) {
                    timestamp
                    runId
                }
            }
        }
        ... on PythonError { message }
    }
}
"""

ASSET_GRAPH_QUERY = """
query AssetGraphQuery {
    assetsOrError {
        __typename
        ... on AssetConnection {
            nodes {
                id
                key { path }
                definition {
                    description
                    computeKind
                    groupName
                    dependencyKeys { path }
                    dependedByKeys { path }
                }
                assetMaterializations(limit: 1) {
                    timestamp
                }
            }
        }
        ... on PythonError { message }
    }
}
"""

ASSET_DETAILS_QUERY = """
query AssetDetailsQuery($assetKey: AssetKeyInput!) {
    assetOrError(assetKey: $assetKey) {
        ... on Asset {
            id
            key { path }
            definition {
                description
                computeKind
                groupName
                hasMaterializePermission
                opNames
                metadataEntries {
                    label
                    description
                    ... on TextMetadataEntry { text }
                    ... on TableSchemaMetadataEntry {
                        schema {
                            columns { name type description }
                        }
                    }
                    ... on TableColumnLineageMetadataEntry {
                        lineage {
                            columnName
                            columnDeps {
                                assetKey { path }
                                columnName
                            }
                        }
                    }
                }
                partitionDefinition { description }
            }
            assetMaterializations(limit: 1) {
                timestamp
                runId
                metadataEntries {
                    label
                    __typename
                    ... on TableSchemaMetadataEntry {
                        schema {
                            columns { name type description }
                        }
                    }
                    ... on TableColumnLineageMetadataEntry {
                        lineage {
                            columnName
                            columnDeps {
                                assetKey { path }
                                columnName
                            }
                        }
                    }
                }
            }
        }
        ... on AssetNotFoundError { message }
    }
}
"""

MATERIALIZATION_HISTORY_QUERY = """
query MaterializationHistory($assetKey: AssetKeyInput!, $limit: Int!) {
    assetOrError(assetKey: $assetKey) {
        ... on Asset {
            assetMaterializations(limit: $limit) {
                timestamp
                runId
                stepKey
                metadataEntries {
                    label
                    ... on TextMetadataEntry { text }
                    ... on IntMetadataEntry { intValue }
                    ... on FloatMetadataEntry { floatValue }
                }
            }
        }
        ... on AssetNotFoundError { message }
    }
}
"""

RUN_STATUS_QUERY = """
query RunStatus($runId: ID!) {
    runOrError(runId: $runId) {
        __typename
        ... on Run {
            runId
            status
            startTime
            endTime
            pipelineName
            tags {
                key
                value
            }
        }
        ... on RunNotFoundError { message }
        ... on PythonError { message }
    }
}
"""

RUNS_QUERY = """
query Runs($limit: Int!) {
    runsOrError(limit: $limit) {
        __typename
        ... on Runs {
            results {
                runId
                status
                startTime
                endTime
                pipelineName
                assetSelection {
                    path
                }
                tags {
                    key
                    value
                }
            }
        }
        ... on PythonError { message }
    }
}
"""

PARTITION_KEYS_QUERY = """
query PartitionKeys($assetKey: AssetKeyInput!) {
    assetNodeOrError(assetKey: $assetKey) {
        __typename
        ... on AssetNode {
            partitionKeysByDimension { name partitionKeys }
        }
        ... on AssetNotFoundError { message }
    }
}
"""

TERMINATE_RUN_MUTATION = """
mutation TerminateRun($runId: String!) {
    terminateRun(runId: $runId) {
        __typename
        ... on TerminateRunSuccess {
            run { runId status }
        }
        ... on TerminateRunFailure { message }
        ... on RunNotFoundError { message }
        ... on PythonError { message }
    }
}
"""

LAUNCH_PARTITION_BACKFILL_MUTATION = """
mutation LaunchPartitionBackfill($backfillParams: LaunchBackfillParams!) {
    launchPartitionBackfill(backfillParams: $backfillParams) {
        __typename
        ... on LaunchBackfillSuccess {
            backfillId
        }
        ... on PartitionSetNotFoundError { message }
        ... on PythonError { message }
    }
}
"""


# --- Pydantic Models ---


class DagsterConnectionStatus(BaseModel):
    """Connection status and server version metadata for Dagster."""

    connected: bool
    error: str | None = None
    version: str | None = None


class HealthMetrics(BaseModel):
    """Aggregated platform health metrics exposed to Observatory."""

    assets_total: int
    assets_healthy: int
    failed_jobs_24h: int
    quality_checks_passing: int
    quality_checks_total: int
    stale_assets: int
    last_updated: str


class LastMaterialization(BaseModel):
    """Timestamp and run id for the most recent materialization."""

    timestamp: str
    run_id: str


class Asset(BaseModel):
    """Dagster asset summary used in list views."""

    id: str
    key: list[str]
    key_path: str
    description: str | None = None
    compute_kind: str | None = None
    group_name: str | None = None
    last_materialization: LastMaterialization | None = None
    has_materialize_permission: bool = False


class ColumnSchema(BaseModel):
    """Column definition extracted from metadata."""

    name: str
    type: str
    description: str | None = None


class ColumnLineageDep(BaseModel):
    """Single upstream column dependency for lineage rendering."""

    asset_key: list[str]
    column_name: str


class AssetDetails(Asset):
    """Extended asset payload used on detail pages."""

    op_names: list[str] = []
    metadata: list[dict[str, str]] = []
    columns: list[ColumnSchema] | None = None
    column_lineage: dict[str, list[ColumnLineageDep]] | None = None
    partition_definition: dict[str, str] | None = None


class MaterializationEvent(BaseModel):
    """Single historical materialization event for an asset."""

    timestamp: str
    run_id: str
    status: str = "SUCCESS"
    step_key: str | None = None
    metadata: list[dict[str, str]] = []
    duration: int | None = None


class MaterializeAssetRequest(BaseModel):
    """Request to materialize one Dagster asset."""

    dry_run: bool = True
    partition_key: str | None = None
    job_name: str | None = None
    repository_location_name: str | None = None
    repository_name: str | None = None
    run_config: dict[str, Any] | None = None
    idempotency_key: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class RetryRunRequest(BaseModel):
    """Request to retry one Dagster run."""

    dry_run: bool = True
    strategy: str = "FROM_FAILURE"
    idempotency_key: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class CancelRunRequest(BaseModel):
    """Request to cancel one Dagster run."""

    reason: str | None = None
    idempotency_key: str | None = None


class BackfillAssetRequest(BaseModel):
    """Request to launch or plan a partition backfill for one asset."""

    dry_run: bool = True
    partitions: list[str] = Field(default_factory=list)
    partition_range: dict[str, str] | None = None
    partition_set_name: str | None = None
    repository_location_name: str | None = None
    repository_name: str | None = None
    idempotency_key: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class DagsterOperationResponse(BaseModel):
    """Structured response for Dagster operational API actions."""

    operation: str
    dry_run: bool
    accepted: bool
    run_id: str | None = None
    asset_key_path: str | None = None
    partition_key: str | None = None
    status: str
    message: str
    details: dict[str, Any] = {}


class DagsterRunStatus(BaseModel):
    """Current Dagster run status."""

    run_id: str
    status: str | None = None
    pipeline_name: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    tags: dict[str, str] = {}


class DagsterPartitionStatus(BaseModel):
    """Materialization status for one asset partition."""

    partition_key: str
    status: str = "UNKNOWN"


class GraphNode(BaseModel):
    """Graph node payload used by Observatory graph views."""

    id: str
    key: list[str]
    key_path: str
    label: str
    description: str | None = None
    compute_kind: str | None = None
    group_name: str | None = None
    layer: str
    last_materialization: str | None = None
    upstream_count: int
    downstream_count: int


class GraphEdge(BaseModel):
    """Directed edge between two graph nodes."""

    source: str
    target: str


class AssetGraphPayload(BaseModel):
    """Full asset graph payload for Observatory."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ImpactedAsset(BaseModel):
    """Single downstream impact result for an asset."""

    key_path: str
    label: str
    layer: str
    depth: int


# --- Helper Functions ---


async def graphql_request(
    url: str,
    query: str,
    variables: dict[str, Any] | None = None,
    timeout: float = 10.0,
    initiator: str | None = None,
) -> dict[str, Any]:
    """POST a GraphQL query to Dagster and return the parsed JSON response.

    Sends service identity headers carrying the correlation id and optional
    initiator for audit attribution. Raises httpx.HTTPStatusError when the
    HTTP request fails.
    """
    headers = {"Content-Type": "application/json"}
    correlation_id = get_bound_correlation_context().request_id
    if requires_http_authorization():
        # Production control-plane calls must carry a verified workload
        # identity; missing credentials fail before any HTTP request is made.
        headers.update(
            build_scoped_service_headers(
                "phlo-api",
                audience="phlo-dagster",
                scp=("dagster:control",),
                credentials=load_service_identity_credentials(),
                initiator=initiator,
                correlation_id=correlation_id,
            )
        )
    else:
        try:
            headers.update(
                build_service_headers(
                    "phlo-api", initiator=initiator, correlation_id=correlation_id
                )
            )
        except RuntimeError:
            logger.debug("dagster_graphql_service_auth_unavailable")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            json={"query": query, "variables": variables or {}},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


def _tags_for_execution(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": str(key), "value": str(value)} for key, value in tags.items()]


def _launch_error_message(result: dict[str, Any]) -> str:
    if result.get("message"):
        return str(result["message"])
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first_error = errors[0]
        if isinstance(first_error, dict) and first_error.get("message"):
            return str(first_error["message"])
    invalid_step = result.get("invalidStepKey")
    if invalid_step:
        return f"Invalid step key: {invalid_step}"
    return str(result.get("__typename") or "Dagster launch failed")


def _launch_operation_response(
    *,
    operation: str,
    dry_run: bool,
    launch_result: dict[str, Any],
    asset_key_path: str | None = None,
    partition_key: str | None = None,
    fallback_run_id: str | None = None,
) -> DagsterOperationResponse:
    typename = str(launch_result.get("__typename") or "DagsterLaunchResult")
    run = launch_result.get("run") if isinstance(launch_result.get("run"), dict) else {}
    if typename in {"LaunchRunSuccess", "LaunchPipelineRunSuccess"} and run:
        run_id = str(run.get("runId") or fallback_run_id or "")
        status = str(run.get("status") or "STARTED")
        return DagsterOperationResponse(
            operation=operation,
            dry_run=dry_run,
            accepted=True,
            run_id=run_id or None,
            asset_key_path=asset_key_path,
            partition_key=partition_key,
            status=status,
            message=f"Dagster accepted {operation}.",
            details={"typename": typename},
        )

    message = _launch_error_message(launch_result)
    return DagsterOperationResponse(
        operation=operation,
        dry_run=dry_run,
        accepted=False,
        run_id=fallback_run_id,
        asset_key_path=asset_key_path,
        partition_key=partition_key,
        status=typename,
        message=message,
        details={"typename": typename},
    )


def infer_layer(key_path: str) -> str:
    """Infer the logical data layer from an asset key path such as "bronze/raw_events".

    Returns "source", "bronze", "silver", "gold", "marts", "publish", or
    "unknown", matched on path substrings and naming prefixes.
    """
    path = key_path.lower()
    if "publish" in path or path.startswith("publish_"):
        return "publish"
    if "mart" in path or path.startswith("mrt_"):
        return "marts"
    if "gold" in path or path.startswith("dim_") or path.startswith("fct_"):
        return "gold"
    if "silver" in path or "stg_" in path:
        return "silver"
    if "bronze" in path or "raw" in path:
        return "bronze"
    if path.startswith("dlt_") or "ingest" in path:
        return "bronze"
    if path.startswith("src_") or "source" in path:
        return "source"
    return "unknown"


def build_asset_graph_payload(asset_nodes: list[dict[str, Any]]) -> AssetGraphPayload:
    """Normalize raw Dagster asset nodes into the Observatory graph payload."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    known_nodes: set[str] = set()

    for asset in asset_nodes:
        key = asset["key"]["path"]
        key_path = "/".join(key)
        definition = asset.get("definition") or {}
        known_nodes.add(key_path)
        nodes.append(
            GraphNode(
                id=asset["id"],
                key=key,
                key_path=key_path,
                label=key[-1] if key else key_path,
                description=definition.get("description"),
                compute_kind=definition.get("computeKind"),
                group_name=definition.get("groupName"),
                layer=infer_layer(key_path),
                last_materialization=((asset.get("assetMaterializations") or [None])[0] or {}).get(
                    "timestamp"
                ),
                upstream_count=len(definition.get("dependencyKeys") or []),
                downstream_count=len(definition.get("dependedByKeys") or []),
            )
        )

    # Dependencies may point at assets outside the fetched node set; emit an
    # edge only when both endpoints exist, so the UI never renders dangling
    # arrows.
    for asset in asset_nodes:
        target_key_path = "/".join(asset["key"]["path"])
        dependencies = (asset.get("definition") or {}).get("dependencyKeys") or []
        for dependency in dependencies:
            source_key_path = "/".join(dependency["path"])
            if source_key_path in known_nodes and target_key_path in known_nodes:
                edges.append(GraphEdge(source=source_key_path, target=target_key_path))

    return AssetGraphPayload(nodes=nodes, edges=edges)


def filter_asset_neighbors(
    graph: AssetGraphPayload,
    asset_key: str,
    direction: str,
    depth: int,
) -> AssetGraphPayload:
    """Return the subgraph within depth hops of asset_key, traversing per direction.

    direction is "upstream", "downstream", or "both". Edges are kept only
    between included nodes.
    """
    upstream: dict[str, list[str]] = {}
    downstream: dict[str, list[str]] = {}

    for edge in graph.edges:
        upstream.setdefault(edge.target, []).append(edge.source)
        downstream.setdefault(edge.source, []).append(edge.target)

    included_nodes = {asset_key}

    def bfs(start_key: str, adjacency: dict[str, list[str]], max_depth: int) -> None:
        """Mark neighbors of start_key reachable within max_depth hops of adjacency."""
        queue: list[tuple[str, int]] = [(start_key, 0)]
        visited = {start_key}

        while queue:
            current, current_depth = queue.pop(0)
            if current_depth >= max_depth:
                continue

            for neighbor in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                included_nodes.add(neighbor)
                queue.append((neighbor, current_depth + 1))

    if direction in {"upstream", "both"}:
        bfs(asset_key, upstream, depth)
    if direction in {"downstream", "both"}:
        bfs(asset_key, downstream, depth)

    return AssetGraphPayload(
        nodes=[node for node in graph.nodes if node.key_path in included_nodes],
        edges=[
            edge
            for edge in graph.edges
            if edge.source in included_nodes and edge.target in included_nodes
        ],
    )


def compute_asset_impact(
    graph: AssetGraphPayload, asset_key: str, max_depth: int
) -> list[ImpactedAsset]:
    """List downstream assets impacted by asset_key up to max_depth edges away.

    Results are sorted by depth, then label.
    """
    node_map = {node.key_path: node for node in graph.nodes}
    downstream: dict[str, list[str]] = {}
    for edge in graph.edges:
        downstream.setdefault(edge.source, []).append(edge.target)

    impacted: list[ImpactedAsset] = []
    visited = {asset_key}
    queue: list[tuple[str, int]] = [(asset_key, 0)]

    while queue:
        current, current_depth = queue.pop(0)
        if current_depth > max_depth:
            continue

        for child in downstream.get(current, []):
            if child in visited:
                continue
            visited.add(child)
            node = node_map.get(child)
            if node is not None:
                impacted.append(
                    ImpactedAsset(
                        key_path=node.key_path,
                        label=node.label,
                        layer=node.layer,
                        depth=current_depth + 1,
                    )
                )
            queue.append((child, current_depth + 1))

    return sorted(impacted, key=lambda item: (item.depth, item.label))


# --- API Endpoints ---


@router.get("/connection", response_model=DagsterConnectionStatus)
async def check_connection(dagster_url: str | None = None) -> DagsterConnectionStatus:
    """Report whether the Dagster GraphQL endpoint answers a version query.

    Connection failures and GraphQL errors are returned as an error string
    instead of raised.
    """
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, VERSION_QUERY, timeout=5.0)

        if result.get("errors"):
            return DagsterConnectionStatus(
                connected=False, error=result["errors"][0].get("message", "GraphQL error")
            )

        return DagsterConnectionStatus(
            connected=True, version=result.get("data", {}).get("version")
        )
    except Exception as e:
        return DagsterConnectionStatus(connected=False, error=str(e))


@router.get("/health", response_model=HealthMetrics | dict)
async def get_health_metrics(
    dagster_url: str | None = None,
) -> HealthMetrics | dict[str, str]:
    """Aggregate platform health metrics from Dagster assets, runs, and quality checks.

    Assets are stale after 24 hours without a materialization; failed jobs
    are counted over the same window. Errors return {'error': message}
    instead of raising.
    """
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, HEALTH_QUERY)

        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        data = result.get("data", {})
        assets_or_error = data.get("assetsOrError", {})
        runs_or_error = data.get("runsOrError", {})

        # Handle asset data
        assets_total = 0
        stale_assets = 0
        now = time.time() * 1000
        stale_threshold = 24 * 60 * 60 * 1000  # 24 hours

        if assets_or_error.get("nodes"):
            nodes = assets_or_error["nodes"]
            assets_total = len(nodes)

            for asset in nodes:
                last_mat = (asset.get("assetMaterializations") or [None])[0]
                if last_mat:
                    mat_time = float(last_mat.get("timestamp", 0)) * 1000
                    if now - mat_time > stale_threshold:
                        stale_assets += 1
                else:
                    stale_assets += 1

        # Handle run data
        failed_jobs_24h = 0
        one_day_ago = now - stale_threshold

        if runs_or_error.get("results"):
            for run in runs_or_error["results"]:
                start_time = float(run.get("startTime") or 0) * 1000
                if start_time > one_day_ago:
                    failed_jobs_24h += 1

        quality_checks_passing = 0
        quality_checks_total = 0
        quality_snapshot = await fetch_quality_snapshot(url)
        if quality_snapshot:
            quality_checks_passing = quality_snapshot["passing_checks"]
            quality_checks_total = quality_snapshot["total_checks"]

        return HealthMetrics(
            assets_total=assets_total,
            assets_healthy=assets_total - stale_assets,
            failed_jobs_24h=failed_jobs_24h,
            quality_checks_passing=quality_checks_passing,
            quality_checks_total=quality_checks_total,
            stale_assets=stale_assets,
            last_updated=datetime.now().isoformat(),
        )
    except Exception as e:
        logger.exception("Failed to get health metrics")
        return {"error": str(e)}


@router.get("/assets", response_model=list[Asset] | dict)
async def get_assets(dagster_url: str | None = None) -> list[Asset] | dict[str, str]:
    """List all Dagster assets with their latest materialization.

    Errors return {'error': message} instead of raising.
    """
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, ASSETS_QUERY)

        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        data = result.get("data", {})
        assets_or_error = data.get("assetsOrError", {})

        if assets_or_error.get("message"):  # PythonError
            return {"error": assets_or_error["message"]}

        assets = []
        for node in assets_or_error.get("nodes", []):
            definition = node.get("definition") or {}
            mats = node.get("assetMaterializations") or []

            last_mat = None
            if mats:
                last_mat = LastMaterialization(
                    timestamp=mats[0]["timestamp"], run_id=mats[0]["runId"]
                )

            assets.append(
                Asset(
                    id=node["id"],
                    key=node["key"]["path"],
                    key_path="/".join(node["key"]["path"]),
                    description=definition.get("description"),
                    compute_kind=definition.get("computeKind"),
                    group_name=definition.get("groupName"),
                    has_materialize_permission=definition.get("hasMaterializePermission", False),
                    last_materialization=last_mat,
                )
            )

        return assets
    except Exception as e:
        logger.exception("Failed to get assets")
        return {"error": str(e)}


@router.get(
    "/assets/{asset_key_path:path}/history", response_model=list[MaterializationEvent] | dict
)
async def get_materialization_history(
    asset_key_path: str,
    limit: int = Query(default=20, le=100),
    dagster_url: str | None = None,
) -> list[MaterializationEvent] | dict[str, str]:
    """Return up to limit recent materializations for the asset at asset_key_path.

    Errors return {'error': message} instead of raising.
    """
    url = resolve_dagster_url(dagster_url)
    asset_key = asset_key_path.split("/")

    try:
        result = await graphql_request(
            url,
            MATERIALIZATION_HISTORY_QUERY,
            {"assetKey": {"path": asset_key}, "limit": limit},
        )

        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        asset_or_error = result.get("data", {}).get("assetOrError", {})

        if asset_or_error.get("message"):
            return {"error": asset_or_error["message"]}

        events = []
        for mat in asset_or_error.get("assetMaterializations", []):
            events.append(
                MaterializationEvent(
                    timestamp=mat["timestamp"],
                    run_id=mat["runId"],
                    status="SUCCESS",
                    step_key=mat.get("stepKey"),
                    metadata=[
                        {
                            "key": e["label"],
                            "value": e.get("text")
                            or str(e.get("intValue", ""))
                            or str(e.get("floatValue", ""))
                            or "",
                        }
                        for e in mat.get("metadataEntries", [])
                    ],
                )
            )

        return events
    except Exception as e:
        logger.exception("Failed to get materialization history")
        return {"error": str(e)}


@router.post(
    "/assets/{asset_key_path:path}/materialize",
    response_model=DagsterOperationResponse | dict,
)
async def materialize_asset(
    asset_key_path: str,
    payload: MaterializeAssetRequest,
    dagster_url: str | None = None,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request materialization for a Dagster asset."""
    if not asset_key_path:
        return {"error": "Asset key is required"}

    details = await get_asset_details(asset_key_path, dagster_url=dagster_url)
    if isinstance(details, dict) and details.get("error"):
        return details
    if isinstance(details, dict):
        has_permission = bool(details.get("has_materialize_permission"))
        op_names = details.get("op_names") or []
    else:
        has_permission = details.has_materialize_permission
        op_names = details.op_names

    if not payload.dry_run:
        if not has_permission:
            return DagsterOperationResponse(
                operation="materialize_asset",
                dry_run=False,
                accepted=False,
                asset_key_path=asset_key_path,
                partition_key=payload.partition_key,
                status="NOT_MATERIALIZABLE",
                message="Dagster reports this asset is not materializable by the current principal.",
                details={"op_names": op_names},
            )
        if not payload.job_name:
            return DagsterOperationResponse(
                operation="materialize_asset",
                dry_run=False,
                accepted=False,
                asset_key_path=asset_key_path,
                partition_key=payload.partition_key,
                status="MISSING_JOB_NAME",
                message="Dagster job_name is required to launch live asset materialization.",
                details={"op_names": op_names},
            )

        from phlo_dagster.operations import launch_materialize

        result = await launch_materialize(
            dagster_url=resolve_dagster_url(dagster_url),
            asset_key_path=asset_key_path,
            job_name=payload.job_name,
            repository_location_name=payload.repository_location_name,
            repository_name=payload.repository_name,
            partition_key=payload.partition_key,
            run_config=payload.run_config,
            idempotency_key=payload.idempotency_key,
            tags=payload.tags,
        )
        return DagsterOperationResponse(**result.to_dict())

    return DagsterOperationResponse(
        operation="materialize_asset",
        dry_run=True,
        accepted=has_permission,
        asset_key_path=asset_key_path,
        partition_key=payload.partition_key,
        status="DRY_RUN",
        message=(
            "Materialization request is valid."
            if has_permission
            else "Dagster reports this asset is not materializable by the current principal."
        ),
        details={"op_names": op_names},
    )


@router.get(
    "/assets/{asset_key_path:path}/partitions",
    response_model=list[DagsterPartitionStatus] | dict,
)
async def list_partitions(
    asset_key_path: str,
    dagster_url: str | None = None,
) -> list[DagsterPartitionStatus] | dict[str, str]:
    """List partition keys for an asset."""
    try:
        from phlo_dagster.operations import list_partitions as list_dagster_partitions

        partitions = await list_dagster_partitions(
            dagster_url=resolve_dagster_url(dagster_url),
            asset_key_path=asset_key_path,
        )
        return [DagsterPartitionStatus(**partition) for partition in partitions]
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/assets/{asset_key_path:path}", response_model=AssetDetails | dict)
async def get_asset_details(
    asset_key_path: str, dagster_url: str | None = None
) -> AssetDetails | dict[str, str]:
    """Describe a single asset, including schema, column lineage, and metadata.

    Schema and lineage come from the latest materialization's metadata when
    present, falling back to the asset definition. Errors return
    {'error': message} instead of raising.
    """
    if not asset_key_path:
        return {"error": "Asset key is required"}

    url = resolve_dagster_url(dagster_url)
    asset_key = asset_key_path.split("/")

    try:
        result = await graphql_request(url, ASSET_DETAILS_QUERY, {"assetKey": {"path": asset_key}})

        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        asset_or_error = result.get("data", {}).get("assetOrError", {})

        if asset_or_error.get("message"):  # AssetNotFoundError
            return {"error": asset_or_error["message"]}

        definition = asset_or_error.get("definition") or {}
        mats = asset_or_error.get("assetMaterializations") or []

        # Extract columns from metadata
        columns = None
        column_lineage = None

        # Check materialization metadata first, then definition
        for source in [mats[0] if mats else None, definition]:
            if not source:
                continue
            for entry in source.get("metadataEntries", []):
                if entry.get("schema") and not columns:
                    columns = [
                        ColumnSchema(
                            name=c["name"],
                            type=c["type"],
                            description=c.get("description"),
                        )
                        for c in entry["schema"].get("columns", [])
                    ]
                if entry.get("lineage") and not column_lineage:
                    column_lineage = {}
                    for lin in entry["lineage"]:
                        column_lineage[lin["columnName"]] = [
                            ColumnLineageDep(
                                asset_key=dep["assetKey"]["path"],
                                column_name=dep["columnName"],
                            )
                            for dep in lin.get("columnDeps", [])
                        ]

        last_mat = None
        if mats:
            last_mat = LastMaterialization(timestamp=mats[0]["timestamp"], run_id=mats[0]["runId"])

        return AssetDetails(
            id=asset_or_error["id"],
            key=asset_or_error["key"]["path"],
            key_path="/".join(asset_or_error["key"]["path"]),
            description=definition.get("description"),
            compute_kind=definition.get("computeKind"),
            group_name=definition.get("groupName"),
            has_materialize_permission=definition.get("hasMaterializePermission", False),
            op_names=definition.get("opNames", []),
            metadata=[
                {"key": e["label"], "value": e.get("text") or e.get("description") or ""}
                for e in definition.get("metadataEntries", [])
                if not e.get("schema")
            ],
            columns=columns,
            column_lineage=column_lineage,
            partition_definition=(
                {"description": definition["partitionDefinition"]["description"]}
                if definition.get("partitionDefinition")
                else None
            ),
            last_materialization=last_mat,
        )
    except Exception as e:
        logger.exception("Failed to get asset details")
        return {"error": str(e)}


@router.get("/runs/{run_id}/status", response_model=DagsterRunStatus | dict)
async def get_run_status(
    run_id: str,
    dagster_url: str | None = None,
) -> DagsterRunStatus | dict[str, str]:
    """Get current status for a Dagster run."""
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, RUN_STATUS_QUERY, {"runId": run_id})
        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        run_or_error = result.get("data", {}).get("runOrError", {})
        if run_or_error.get("message"):
            return {"error": run_or_error["message"]}

        tags = {
            str(tag.get("key")): str(tag.get("value"))
            for tag in run_or_error.get("tags", [])
            if tag.get("key") is not None
        }
        return DagsterRunStatus(
            run_id=run_or_error.get("runId") or run_id,
            status=run_or_error.get("status"),
            pipeline_name=run_or_error.get("pipelineName"),
            start_time=run_or_error.get("startTime"),
            end_time=run_or_error.get("endTime"),
            tags=tags,
        )
    except Exception as e:
        logger.exception("Failed to get run status")
        return {"error": str(e)}


async def get_runs(
    limit: int = 100,
    dagster_url: str | None = None,
) -> list[dict[str, Any]]:
    """Get recent Dagster runs for provider-neutral adapters."""
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, RUNS_QUERY, {"limit": limit})
        if result.get("errors"):
            return []

        runs_or_error = result.get("data", {}).get("runsOrError", {})
        if runs_or_error.get("message"):
            return []

        runs = runs_or_error.get("results", [])
        if not isinstance(runs, list):
            return []

        return [_normalize_run_payload(run) for run in runs if isinstance(run, dict)]
    except Exception:
        logger.exception("Failed to get runs")
        return []


def _normalize_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Dagster GraphQL run payload for v2 adapters."""
    asset_selection = run.get("assetSelection")
    if isinstance(asset_selection, list):
        asset_keys = [
            item.get("path")
            for item in asset_selection
            if isinstance(item, dict) and isinstance(item.get("path"), list)
        ]
        if asset_keys:
            return {**run, "assetKeys": asset_keys}
    return run


@router.post("/runs/{run_id}/retry", response_model=DagsterOperationResponse | dict)
async def retry_run(
    run_id: str,
    payload: RetryRunRequest,
    dagster_url: str | None = None,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request retry for a Dagster run."""
    status = await get_run_status(run_id, dagster_url=dagster_url)
    if isinstance(status, dict) and status.get("error"):
        return status

    run_status = status.get("status") if isinstance(status, dict) else status.status
    if not payload.dry_run:
        if run_status != "FAILURE":
            return DagsterOperationResponse(
                operation="retry_failed_run",
                dry_run=False,
                accepted=False,
                run_id=run_id,
                status=str(run_status),
                message=f"Run status is {run_status}; only FAILURE runs are retry candidates.",
                details={"run_status": run_status},
            )

        from phlo_dagster.operations import launch_retry

        result = await launch_retry(
            dagster_url=resolve_dagster_url(dagster_url),
            run_id=run_id,
            strategy=payload.strategy,
            idempotency_key=payload.idempotency_key,
            tags=payload.tags,
        )
        return DagsterOperationResponse(**result.to_dict())

    return DagsterOperationResponse(
        operation="retry_failed_run",
        dry_run=True,
        accepted=run_status == "FAILURE",
        run_id=run_id,
        status="DRY_RUN",
        message=(
            "Run retry request is valid."
            if run_status == "FAILURE"
            else f"Run status is {run_status}; only FAILURE runs are retry candidates."
        ),
        details={"run_status": run_status},
    )


@router.post("/runs/{run_id}/cancel", response_model=DagsterOperationResponse | dict)
async def cancel_run(
    run_id: str,
    payload: CancelRunRequest,
    dagster_url: str | None = None,
) -> DagsterOperationResponse | dict[str, str]:
    """Request cancellation for a Dagster run."""
    from phlo_dagster.operations import terminate

    result = await terminate(
        dagster_url=resolve_dagster_url(dagster_url),
        run_id=run_id,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
    )
    return DagsterOperationResponse(**result.to_dict())


@router.post(
    "/assets/{asset_key_path:path}/backfill", response_model=DagsterOperationResponse | dict
)
async def backfill_asset(
    asset_key_path: str,
    payload: BackfillAssetRequest,
    dagster_url: str | None = None,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request a partition backfill for one asset."""
    partition_keys = _backfill_partition_keys(payload)
    if not partition_keys:
        return DagsterOperationResponse(
            operation="backfill_asset",
            dry_run=payload.dry_run,
            accepted=False,
            asset_key_path=asset_key_path,
            status="MISSING_PARTITIONS",
            message="Backfill requires explicit partitions or a partition_range with start and end.",
            details={},
        )

    if payload.dry_run:
        return DagsterOperationResponse(
            operation="backfill_asset",
            dry_run=True,
            accepted=True,
            asset_key_path=asset_key_path,
            status="DRY_RUN",
            message="Backfill request is valid.",
            details={"partitions": partition_keys, "partition_count": len(partition_keys)},
        )

    if not payload.partition_set_name:
        return DagsterOperationResponse(
            operation="backfill_asset",
            dry_run=False,
            accepted=False,
            asset_key_path=asset_key_path,
            status="MISSING_PARTITION_SET_NAME",
            message="partition_set_name is required to launch a live Dagster partition backfill.",
            details={"partitions": partition_keys, "partition_count": len(partition_keys)},
        )

    from phlo_dagster.operations import launch_backfill

    result = await launch_backfill(
        dagster_url=resolve_dagster_url(dagster_url),
        asset_key_path=asset_key_path,
        partition_set_name=payload.partition_set_name,
        partition_keys=partition_keys,
        repository_location_name=payload.repository_location_name,
        repository_name=payload.repository_name,
        idempotency_key=payload.idempotency_key,
        tags=payload.tags,
    )
    return DagsterOperationResponse(**result.to_dict())


def _backfill_partition_keys(payload: BackfillAssetRequest) -> list[str]:
    if payload.partitions:
        return [str(partition) for partition in payload.partitions]
    if not payload.partition_range:
        return []
    start = payload.partition_range.get("start")
    end = payload.partition_range.get("end")
    if not start or not end:
        return []
    try:
        return _partition_range(start, end)
    except ValueError:
        return []


@router.get("/graph", response_model=AssetGraphPayload | dict)
async def get_asset_graph(
    dagster_url: str | None = None,
) -> AssetGraphPayload | dict[str, str]:
    """Fetch the full asset dependency graph from Dagster.

    Errors return {'error': message} instead of raising.
    """
    url = resolve_dagster_url(dagster_url)

    try:
        result = await graphql_request(url, ASSET_GRAPH_QUERY, timeout=15.0)
        if result.get("errors"):
            return {"error": result["errors"][0].get("message", "GraphQL error")}

        assets_or_error = result.get("data", {}).get("assetsOrError", {})
        if assets_or_error.get("__typename") == "PythonError" or assets_or_error.get("message"):
            return {"error": assets_or_error.get("message", "Dagster error")}

        return build_asset_graph_payload(assets_or_error.get("nodes", []))
    except Exception as e:
        logger.exception("Failed to get asset graph")
        return {"error": str(e)}


@router.get("/graph/neighbors", response_model=AssetGraphPayload | dict)
async def get_asset_neighbors(
    asset_key: str,
    direction: str = Query(default="both", pattern="^(upstream|downstream|both)$"),
    depth: int = Query(default=2, ge=1, le=10),
    dagster_url: str | None = None,
) -> AssetGraphPayload | dict[str, str]:
    """Return the asset graph filtered to neighbors within depth hops of asset_key."""
    graph = await get_asset_graph(dagster_url=dagster_url)
    if isinstance(graph, dict):
        return graph
    return filter_asset_neighbors(graph, asset_key=asset_key, direction=direction, depth=depth)


@router.get("/graph/impact", response_model=list[ImpactedAsset] | dict)
async def get_asset_impact(
    asset_key: str,
    max_depth: int = Query(default=99, ge=1, le=100),
    dagster_url: str | None = None,
) -> list[ImpactedAsset] | dict[str, str]:
    """Analyze the downstream impact of asset_key across the fetched graph."""
    graph = await get_asset_graph(dagster_url=dagster_url)
    if isinstance(graph, dict):
        return graph
    return compute_asset_impact(graph, asset_key=asset_key, max_depth=max_depth)
