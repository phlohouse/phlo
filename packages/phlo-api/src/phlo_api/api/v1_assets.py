"""Environment-scoped asset and overview read models for /api/v1."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from phlo_api.api.v1 import _target
from phlo_api.errors import BackendUnavailableError, BadGatewayError, NotFoundError
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.observatory_api.v1_preview import (
    PreviewLimitExceeded,
    PreviewUnavailable,
    execute_preview,
    preview_catalog,
    quote_table,
)
from phlo_api.api.v1_audit_proposals import (
    AssetAuditProposalRequest,
    generate_check_file,
)
from phlo_api.v1_contract import Environment, EnvironmentTarget, WireModel

router = APIRouter(tags=["v1 assets"])
Limit = Annotated[int, Query(ge=1, le=500)]
CheckLimit = Annotated[int, Query(ge=1, le=100)]
_CHECK_DEFINITION_LIMIT = 100
_CHECK_EXECUTION_SCAN_LIMIT = 101
_OVERVIEW_CHECK_ASSET_LIMIT = 50
_OVERVIEW_CHECK_LIMIT = 100

ASSET_QUERY = """query V1Assets {
  assetNodes {
    id assetKey { path } description computeKind groupName isMaterializable isObservable
    repository { name location { name } }
    dependencyKeys { path }
    assetMaterializations(limit: 1) { timestamp runId }
  }
}"""
ASSET_DETAIL_QUERY = """query V1AssetDetail($assetKey: AssetKeyInput!) {
  assetNodeOrError(assetKey: $assetKey) {
    __typename
    ... on AssetNode {
      id assetKey { path } description computeKind groupName isMaterializable isObservable
      repository { name location { name } }
      jobNames
      dependencyKeys { path }
      metadataEntries {
        label description
        ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
        ... on TableColumnLineageMetadataEntry {
          lineage { columnName columnDeps { assetKey { path } columnName } }
        }
      }
      assetMaterializations(limit: 1) {
        timestamp runId
        metadataEntries {
          label
          ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
          ... on TableColumnLineageMetadataEntry {
            lineage { columnName columnDeps { assetKey { path } columnName } }
          }
        }
      }
    }
    ... on AssetNotFoundError { message }
  }
}"""
ASSET_LATEST_PARTITION_QUERY = """query V1AssetLatestPartition($assetKey: AssetKeyInput!, $limit: Int!, $ascending: Boolean!) {
  assetNodeOrError(assetKey: $assetKey) {
    __typename
    ... on AssetNode {
      repository { location { name } }
      partitionKeyConnection(limit: $limit, ascending: $ascending) {
        results cursor hasMore
      }
    }
    ... on AssetNotFoundError { message }
  }
}"""
BACKFILL_PARTITION_SET_QUERY = """query V1BackfillPartitionSet($repositorySelector: RepositorySelector!, $partitionSetName: String!) {
  partitionSetOrError(repositorySelector: $repositorySelector, partitionSetName: $partitionSetName) {
    __typename
    ... on PartitionSet {
      pipelineName
      repositoryOrigin { repositoryLocationName repositoryName }
    }
    ... on PartitionSetNotFoundError { message }
  }
}"""
ASSET_RUNS_QUERY = """query V1AssetRuns($limit: Int!, $cursor: String) {
  runsFeedOrError(limit: $limit, cursor: $cursor, view: RUNS) {
    __typename
    ... on RunsFeedConnection {
      results {
        ... on Run {
          __typename runId status creationTime startTime endTime
          repositoryOrigin { repositoryLocationName }
          assetSelection { path }
        }
      }
      cursor hasMore
    }
    ... on PythonError { message }
  }
}"""
ASSET_CHECKS_QUERY = """query V1AssetChecks($assetKeys: [AssetKeyInput!], $limit: Int!) {
  assetNodes(assetKeys: $assetKeys) {
    assetKey { path }
    repository { location { name } }
    assetChecksOrError(limit: $limit) {
      __typename
      ... on AssetChecks { checks { name description } }
      ... on AssetCheckNeedsMigrationError { message }
    }
  }
}"""
ASSET_CHECK_EXECUTIONS_QUERY = """query V1AssetCheckExecutions($assetKey: AssetKeyInput!, $checkName: String!, $limit: Int!) {
  assetCheckExecutions(assetKey: $assetKey, checkName: $checkName, limit: $limit) {
    status runId timestamp
    evaluation {
      success
      severity
      metadataEntries {
        __typename label
        ... on TextMetadataEntry { text }
        ... on IntMetadataEntry { intValue }
        ... on FloatMetadataEntry { floatValue }
        ... on BoolMetadataEntry { boolValue }
        ... on JsonMetadataEntry { jsonString }
      }
    }
    run { runId repositoryOrigin { repositoryLocationName } }
  }
}"""


class AssetView(WireModel):
    id: str
    key: list[str]
    description: str | None
    compute_kind: str | None
    group_name: str | None
    is_source: bool
    dependencies: list[list[str]]
    last_materialization_at: datetime | None
    last_run_id: str | None
    history_scoped: bool = True


class AssetPage(WireModel):
    env: Environment
    items: list[AssetView]
    next_cursor: str | None


class AssetDetail(AssetView):
    columns: list["AssetColumn"]
    schema_observed_at: datetime | None
    column_lineage: dict[str, list["ColumnLineageDependency"]] | None = None


class AssetColumn(WireModel):
    name: str
    type: str | None
    description: str | None


class ColumnLineageDependency(WireModel):
    asset_key: list[str]
    column_name: str


class AssetRun(WireModel):
    run_id: str
    status: str
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None


class AssetRuns(WireModel):
    env: Environment
    asset_id: str
    items: list[AssetRun]
    next_cursor: str | None


class CheckDefinition(WireModel):
    name: str
    description: str | None = None


class CheckMetadata(WireModel):
    label: str
    value: str | int | float | bool | dict[str, Any] | None


class CheckExecution(WireModel):
    status: str
    run_id: str
    timestamp: AwareDatetime
    check_name: str
    passed: bool | None
    severity: str | None
    metadata: list[CheckMetadata]


class AssetChecks(WireModel):
    env: Environment
    asset_id: str
    definitions: list[CheckDefinition]
    executions: list[CheckExecution]


class IcebergSnapshot(WireModel):
    snapshot_id: int
    timestamp_ms: int
    operation: str | None
    summary: dict[str, str]
    parent_id: int | None = None


class TableHistory(WireModel):
    env: Environment
    table_name: str
    nessie_ref: str
    items: list[IcebergSnapshot]


class IcebergField(WireModel):
    field_id: int
    name: str
    type: str
    required: bool


class IcebergSchemaVersion(WireModel):
    schema_id: int
    fields: list[IcebergField]


class SchemaHistory(WireModel):
    env: Environment
    table_name: str
    nessie_ref: str
    current_schema_id: int
    items: list[IcebergSchemaVersion]


class MaterializationEstimate(WireModel):
    env: Environment
    asset_id: str
    partition_count: int
    estimated_cost: None = None
    estimated_bytes: None = None
    estimated_duration_seconds: None = None
    cost_status: str = "unavailable: no cost source is configured"
    workload_status: str = "unavailable: no workload source is configured"


class PreviewColumn(WireModel):
    name: str
    type: str | None = None


class AssetPreview(WireModel):
    env: Environment
    asset_id: str
    nessie_ref: str
    columns: list[PreviewColumn]
    rows: list[dict[str, Any]]
    has_more: bool


class MaterializeAssetAction(WireModel):
    job_name: str = Field(min_length=1)
    partition_key: str | None = None
    dry_run: bool = True
    idempotency_key: str = Field(min_length=1, max_length=128)
    run_config: dict[str, Any] | None = None


class BackfillAssetAction(WireModel):
    job_name: str = Field(min_length=1)
    partition_set_name: str = Field(min_length=1)
    selection: Literal["explicit", "latest", "all"] = "explicit"
    partitions: list[str] = Field(default_factory=list, max_length=500)
    dry_run: bool = True
    idempotency_key: str = Field(min_length=1, max_length=128)


class AssetActionResponse(WireModel):
    env: Environment
    asset_id: str
    nessie_ref: str
    result: dict[str, Any]


class LayerView(WireModel):
    group_name: str | None
    asset_count: int = Field(ge=0)
    materialized_asset_count: int = Field(ge=0)
    latest_materialization_at: datetime | None


class LayerPage(WireModel):
    env: Environment
    items: list[LayerView]
    next_cursor: str | None


class FreshnessCounts(WireModel):
    fresh: int = Field(ge=0)
    stale: int = Field(ge=0)
    unknown: int = Field(ge=0)


class QualityCheckCounts(WireModel):
    passing: int = Field(ge=0)
    total: int = Field(ge=0)
    unevaluated: int = Field(ge=0)


class QualityCheckEvidence(WireModel):
    status: Literal["available", "unknown"]
    counts: QualityCheckCounts | None
    failing_assets: list[str] | None = None
    reason: str | None


class OverviewResponse(WireModel):
    env: Environment
    asset_count: int = Field(ge=0)
    materialized_asset_count: int = Field(ge=0)
    latest_materialization_at: AwareDatetime | None
    incident_counts: dict[str, int]
    freshness_counts: FreshnessCounts
    run_status_counts: dict[str, int]
    run_history_truncated: bool
    quality_checks: QualityCheckEvidence


def _cursor(env: Environment, kind: str, offset: int) -> str:
    raw = json.dumps({"env": env, "kind": kind, "offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(cursor: str | None, env: Environment, kind: str) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        offset = value["offset"]
        if value["env"] != env or value["kind"] != kind or type(offset) is not int or offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.") from exc


def _asset_run_cursor(env: Environment, asset_id: str, dagster_cursor: str) -> str:
    value = json.dumps(
        {"env": env, "asset_id": asset_id, "dagster_cursor": dagster_cursor},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_asset_run_cursor(cursor: str | None, env: Environment, asset_id: str) -> str | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > 4096:
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        dagster_cursor = value["dagster_cursor"]
        if (
            value["env"] != env
            or value["asset_id"] != asset_id
            or not isinstance(dagster_cursor, str)
            or not dagster_cursor
        ):
            raise ValueError
        return dagster_cursor
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or cross-environment cursor.") from exc


def _asset_run_view(row: Any, asset_path: list[str], repository_location: str) -> AssetRun | None:
    if not isinstance(row, dict) or row.get("__typename") != "Run":
        raise ValueError
    selection = row.get("assetSelection")
    if selection is None:
        return None
    if not isinstance(selection, list):
        raise ValueError
    if not any(_key_path(key) == asset_path for key in selection):
        return None
    repository = row.get("repositoryOrigin")
    location = repository.get("repositoryLocationName") if isinstance(repository, dict) else None
    if not isinstance(location, str):
        raise ValueError
    if location != repository_location:
        return None
    status = row.get("status")
    run_id = row.get("runId")
    if (
        status
        not in {
            "NOT_STARTED",
            "MANAGED",
            "QUEUED",
            "STARTING",
            "STARTED",
            "SUCCESS",
            "FAILURE",
            "CANCELING",
            "CANCELED",
        }
        or not isinstance(run_id, str)
        or not run_id
    ):
        raise ValueError
    return AssetRun(
        run_id=run_id,
        status=status,
        created_at=datetime.fromtimestamp(float(row["creationTime"]), UTC),
        started_at=(
            datetime.fromtimestamp(float(row["startTime"]), UTC)
            if row.get("startTime") is not None
            else None
        ),
        ended_at=(
            datetime.fromtimestamp(float(row["endTime"]), UTC)
            if row.get("endTime") is not None
            else None
        ),
    )


def _key_path(value: Any) -> list[str]:
    path = value.get("path") if isinstance(value, dict) else None
    if not isinstance(path, list) or not path or any(not isinstance(part, str) for part in path):
        raise BadGatewayError("Dagster returned an invalid asset key.")
    return path


def _repository_location(node: dict[str, Any]) -> str:
    repository = node.get("repository")
    location = repository.get("location") if isinstance(repository, dict) else None
    if not isinstance(location, dict) or not isinstance(location.get("name"), str):
        raise BadGatewayError("Dagster asset has no location identity.")
    return location["name"]


def _asset_view(node: dict[str, Any]) -> AssetView:
    _repository_location(node)
    materializable = node.get("isMaterializable")
    if type(materializable) is not bool:
        raise BadGatewayError("Dagster returned invalid asset materializability evidence.")
    materials = node.get("assetMaterializations")
    dependencies = node.get("dependencyKeys")
    if not isinstance(materials, list) or not isinstance(dependencies, list):
        raise BadGatewayError("Dagster returned invalid asset evidence.")
    latest = materials[0] if materials else None
    try:
        observed = datetime.fromtimestamp(float(latest["timestamp"]), UTC) if latest else None
        run_id = latest.get("runId") if latest else None
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise BadGatewayError("Dagster returned invalid materialization evidence.") from exc
    key = _key_path(node.get("assetKey"))
    return AssetView(
        id="/".join(key),
        key=key,
        description=node.get("description"),
        compute_kind=node.get("computeKind"),
        group_name=node.get("groupName"),
        is_source=not materializable,
        dependencies=[_key_path(item) for item in dependencies],
        last_materialization_at=observed,
        last_run_id=run_id,
    )


async def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = await graphql_request(resolve_dagster_url(), query, variables)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        raise BackendUnavailableError("Dagster is unavailable.") from exc
    except ValueError as exc:
        raise BadGatewayError("Dagster returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise BadGatewayError("Dagster returned an invalid response.")
    return result


def _table_name(value: str) -> str:
    """Accept a single Iceberg namespace/table identifier, never a query fragment."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
        raise HTTPException(status_code=400, detail="table_name must be namespace.table.")
    return value


def _iceberg_history(table_name: str, ref: str, limit: int) -> list[dict[str, Any]]:
    from phlo_iceberg.catalog import get_catalog

    table = get_catalog(ref=ref).load_table(table_name)
    snapshots = sorted(table.snapshots(), key=lambda snapshot: snapshot.timestamp_ms, reverse=True)
    return [
        {
            "snapshot_id": int(snapshot.snapshot_id),
            "timestamp_ms": int(snapshot.timestamp_ms),
            "operation": snapshot.summary.operation.value if snapshot.summary else None,
            "summary": {
                str(key): str(value)
                for key, value in (
                    snapshot.summary.additional_properties.items() if snapshot.summary else []
                )
            },
            "parent_id": snapshot.parent_snapshot_id,
        }
        for snapshot in snapshots[:limit]
    ]


def _iceberg_schema(table_name: str, ref: str) -> tuple[int, list[dict[str, Any]]]:
    from phlo_iceberg.catalog import get_catalog

    table = get_catalog(ref=ref).load_table(table_name)
    metadata = table.metadata
    return metadata.current_schema_id, [
        {
            "schema_id": schema.schema_id,
            "fields": [
                {
                    "field_id": field.field_id,
                    "name": field.name,
                    "type": str(field.field_type),
                    "required": field.required,
                }
                for field in schema.fields
            ],
        }
        for schema in metadata.schemas
    ]


def _response_field(result: dict[str, Any], name: str) -> dict[str, Any]:
    data = result.get("data")
    value = data.get(name) if isinstance(data, dict) else None
    if result.get("errors") or not isinstance(value, dict):
        raise BadGatewayError("Dagster returned an invalid asset response.")
    return value


async def _assets(
    request: Request,
    env: Environment,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> list[AssetView]:
    location = _target(request, env, allowed_query=allowed_query).dagster_location
    result = await _graphql(ASSET_QUERY)
    data = result.get("data") if isinstance(result, dict) else None
    nodes = data.get("assetNodes") if isinstance(data, dict) else None
    if result.get("errors") or not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned an invalid asset inventory.")
    raw_nodes = [node for node in nodes if isinstance(node, dict)]
    if len(raw_nodes) != len(nodes):
        raise BadGatewayError("Dagster returned an invalid asset inventory.")
    assets = [_asset_view(node) for node in raw_nodes]
    locations = [_repository_location(node) for node in raw_nodes]
    locations_by_key: dict[str, set[str]] = {}
    for asset, node_location in zip(assets, locations, strict=True):
        locations_by_key.setdefault(asset.id, set()).add(node_location)
    # Filter on Dagster's repository location before exposing any resource data.
    return sorted(
        [
            asset.model_copy(
                update={
                    "last_materialization_at": None,
                    "last_run_id": None,
                    "history_scoped": False,
                }
            )
            if len(locations_by_key[asset.id]) > 1
            else asset
            for asset, node_location in zip(assets, locations, strict=True)
            if node_location == location
        ],
        key=lambda asset: asset.id,
    )


def _page(
    env: Environment, kind: str, items: list[AssetView], limit: int, cursor: str | None
) -> AssetPage:
    offset = _offset(cursor, env, kind)
    selected = items[offset : offset + limit]
    next_cursor = _cursor(env, kind, offset + limit) if offset + limit < len(items) else None
    return AssetPage(env=env, items=selected, next_cursor=next_cursor)


@router.get("/tables/{table_name}/snapshots", response_model=TableHistory)
async def v1_table_snapshots(
    request: Request, table_name: str, env: Environment = Query(), limit: Limit = 100
) -> TableHistory:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    name = _table_name(table_name)
    try:
        raw_items = await asyncio.wait_for(
            asyncio.to_thread(_iceberg_history, name, target.nessie_ref, limit), timeout=5
        )
        items = [IcebergSnapshot.model_validate(item) for item in raw_items]
    except TimeoutError as exc:
        raise BackendUnavailableError("Iceberg history exceeded the read time budget.") from exc
    except Exception as exc:
        raise BackendUnavailableError("Ref-scoped Iceberg history is unavailable.") from exc
    return TableHistory(env=env, table_name=name, nessie_ref=target.nessie_ref, items=items)


@router.get("/tables/{table_name}/schema-history", response_model=SchemaHistory)
async def v1_table_schema_history(
    request: Request, table_name: str, env: Environment = Query(), limit: Limit = 100
) -> SchemaHistory:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    name = _table_name(table_name)
    try:
        current_schema_id, raw = await asyncio.wait_for(
            asyncio.to_thread(_iceberg_schema, name, target.nessie_ref), timeout=5
        )
        items = [IcebergSchemaVersion.model_validate(item) for item in raw[-limit:]]
    except TimeoutError as exc:
        raise BackendUnavailableError(
            "Iceberg schema history exceeded the read time budget."
        ) from exc
    except Exception as exc:
        raise BackendUnavailableError("Ref-scoped Iceberg schema history is unavailable.") from exc
    return SchemaHistory(
        env=env,
        table_name=name,
        nessie_ref=target.nessie_ref,
        current_schema_id=current_schema_id,
        items=items,
    )


@router.get(
    "/assets/{asset_id:path}/materialization-estimate", response_model=MaterializationEstimate
)
async def v1_materialization_estimate(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    partition_count: Annotated[int, Query(ge=1, le=5000)] = 1,
) -> MaterializationEstimate:
    _target(request, env, allowed_query=frozenset({"env", "partition_count"}))
    asset_id = asset_id.strip("/")
    assets = await _assets(request, env)
    if not any(asset.id == asset_id for asset in assets):
        raise NotFoundError("Asset was not found.")
    return MaterializationEstimate(env=env, asset_id=asset_id, partition_count=partition_count)


@router.get("/assets/{asset_id:path}/preview", response_model=AssetPreview)
async def v1_asset_preview(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AssetPreview:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    asset_id = asset_id.strip("/")
    matches = [
        asset
        for asset in await _assets(request, env, allowed_query=frozenset({"env", "limit"}))
        if asset.id == asset_id
    ]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].history_scoped:
        raise BackendUnavailableError("Asset preview cannot be scoped to this environment.")
    try:
        catalog = preview_catalog(env, target.nessie_ref)
        relation = quote_table(catalog, asset_id)
        result = await execute_preview(
            f"SELECT * FROM {relation} LIMIT {limit + 1}",
            catalog=catalog,
            disconnected=request.is_disconnected,
            limit=limit,
        )
        columns = [PreviewColumn.model_validate(item) for item in result["columns"]]
    except PreviewLimitExceeded as exc:
        raise BackendUnavailableError(str(exc)) from exc
    except PreviewUnavailable as exc:
        raise BackendUnavailableError(str(exc)) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise BadGatewayError("Trino returned invalid preview data.") from exc
    return AssetPreview(
        env=env,
        asset_id=asset_id,
        nessie_ref=target.nessie_ref,
        columns=columns,
        rows=result["rows"],
        has_more=result["has_more"],
    )


@router.post("/assets/{asset_id:path}/audits")
async def v1_asset_audit_proposal(
    request: Request,
    asset_id: str,
    payload: AssetAuditProposalRequest,
    env: Environment = Query(),
) -> dict[str, Any]:
    """Validate a declarative check proposal and fail closed without Git review."""
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key

    auth = require_scope(request, "project:write")
    enforce_rate_limit(auth["subject"], "create_asset_audit_proposal")
    require_idempotency_key(payload.idempotency_key)
    target = _target(request, env)
    asset_id = asset_id.strip("/")
    assets = await _assets(request, env)
    matches = [asset for asset in assets if asset.id == asset_id]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].history_scoped:
        raise BackendUnavailableError("Asset check proposals cannot be scoped to this environment.")
    detail = await v1_asset_detail(request, asset_id, env)
    columns = {column.name for column in detail.columns}
    if not columns:
        raise BackendUnavailableError("Asset schema evidence is required to propose a check.")
    unknown_columns = sorted({rule.column for rule in payload.rules} - columns)
    if unknown_columns:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_asset_columns", "columns": unknown_columns},
        )
    try:
        file_path, source = generate_check_file(matches[0].key, payload.check_name, payload.rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    action_target = f"{env}:{asset_id}@{target.nessie_ref}:audit-proposal:{source_digest}"
    idempotency_store_ready = (
        os.environ.get("PHLO_V1_ACTIONS_SINGLE_REPLICA") == "1"
        and os.environ.get("PHLO_V1_ACTIONS_SINGLE_PROCESS") == "1"
    )
    bound_target = idempotency_key_target(payload.idempotency_key, "v1_create_asset_audit_proposal")
    if bound_target is not None and bound_target != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        reason = (
            "project_git_review_not_configured"
            if idempotency_store_ready
            else "single_replica_idempotency_gate_not_enabled"
        )
        return _blocked_audit_proposal_result(file_path, source_digest, reason)

    outcome = await replay_or_execute_async(
        idempotency_key=payload.idempotency_key,
        operation="v1_create_asset_audit_proposal",
        target=action_target,
        execute=execute,
        audit=lambda outcome: audit_operation(
            operation="v1_create_asset_audit_proposal",
            target=action_target,
            dry_run=True,
            auth=auth,
            payload={
                "check_name": payload.check_name,
                "rules": [rule.model_dump(mode="json") for rule in payload.rules],
            },
            result=outcome,
        ),
    )
    reason = {
        "project_git_review_not_configured": "Project Git review is not configured",
        "single_replica_idempotency_gate_not_enabled": "Single-replica idempotency is not enabled",
    }.get(str(outcome.get("reason")), "The proposal could not be created")
    raise BackendUnavailableError(f"{reason}; no proposal was created or check activated.")


def _blocked_audit_proposal_result(
    file_path: str, source_digest: str, reason: str
) -> dict[str, Any]:
    """Record an idempotent blocked attempt without writing generated code."""
    return {
        "file_path": file_path,
        "source_digest": source_digest,
        "status": "blocked",
        "reason": reason,
    }


async def _action_context(
    request: Request, env: Environment, asset_id: str
) -> tuple[EnvironmentTarget, str, list[str]]:
    if (
        os.environ.get("PHLO_V1_ACTIONS_SINGLE_REPLICA") != "1"
        or os.environ.get("PHLO_V1_ACTIONS_SINGLE_PROCESS") != "1"
        or os.environ.get("PHLO_V1_ACTIONS_REF_TAG_CONTRACT") != "1"
    ):
        raise BackendUnavailableError("Environment-pinned actions are not enabled.")
    target = _target(request, env)
    assets = await _assets(request, env)
    matches = [asset for asset in assets if asset.id == asset_id]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].history_scoped:
        raise BackendUnavailableError("Asset actions cannot be scoped to this environment.")
    detail = await _graphql(ASSET_DETAIL_QUERY, {"assetKey": {"path": asset_id.split("/")}})
    node = _response_field(detail, "assetNodeOrError")
    repository = node.get("repository")
    if not isinstance(repository, dict):
        raise NotFoundError("Asset was not found.")
    location = repository.get("location")
    repository_name = repository.get("name")
    job_names = node.get("jobNames")
    if (
        not isinstance(location, dict)
        or location.get("name") != target.dagster_location
        or not isinstance(repository_name, str)
        or not isinstance(job_names, list)
        or any(not isinstance(name, str) for name in job_names)
    ):
        raise NotFoundError("Asset was not found.")
    return target, repository_name, job_names


async def _latest_asset_partition(asset_id: str, repository_location: str) -> str:
    result = await _graphql(
        ASSET_LATEST_PARTITION_QUERY,
        {
            "assetKey": {"path": asset_id.split("/")},
            "limit": 1,
            "ascending": False,
        },
    )
    node = _response_field(result, "assetNodeOrError")
    if node.get("__typename") != "AssetNode":
        raise BackendUnavailableError("Dagster could not resolve the asset partition definition.")
    repository = node.get("repository")
    location = repository.get("location") if isinstance(repository, dict) else None
    if not isinstance(location, dict) or location.get("name") != repository_location:
        raise NotFoundError("Asset was not found.")
    connection = node.get("partitionKeyConnection")
    if connection is None:
        raise BackendUnavailableError("Asset has no partition-key connection.")
    if not isinstance(connection, dict):
        raise BadGatewayError("Dagster returned invalid asset partition keys.")
    keys = connection.get("results")
    if (
        not isinstance(keys, list)
        or any(not isinstance(key, str) for key in keys)
        or not isinstance(connection.get("cursor"), str)
        or not isinstance(connection.get("hasMore"), bool)
    ):
        raise BadGatewayError("Dagster returned invalid asset partition keys.")
    if not keys:
        raise BackendUnavailableError("No latest asset partition is available.")
    if len(keys) != 1 or not keys[0].strip():
        raise BadGatewayError("Dagster returned invalid latest asset partition.")
    return keys[0]


async def _validate_backfill_partition_set(
    partition_set_name: str,
    job_name: str,
    repository_location: str,
    repository_name: str,
) -> None:
    result = await _graphql(
        BACKFILL_PARTITION_SET_QUERY,
        {
            "repositorySelector": {
                "repositoryLocationName": repository_location,
                "repositoryName": repository_name,
            },
            "partitionSetName": partition_set_name,
        },
    )
    partition_set = _response_field(result, "partitionSetOrError")
    origin = partition_set.get("repositoryOrigin")
    if (
        partition_set.get("__typename") != "PartitionSet"
        or partition_set.get("pipelineName") != job_name
        or not isinstance(origin, dict)
        or origin.get("repositoryLocationName") != repository_location
        or origin.get("repositoryName") != repository_name
    ):
        raise NotFoundError("Partition set was not found for this asset job.")


def _action_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    raise BadGatewayError("Dagster returned an invalid action response.")


@router.post("/assets/{asset_id:path}/materialize", response_model=AssetActionResponse)
async def v1_asset_materialize(
    request: Request,
    asset_id: str,
    payload: MaterializeAssetAction,
    env: Environment = Query(),
) -> AssetActionResponse:
    from phlo_api.api.operation_controls import (
        audit_operation,
        enforce_rate_limit,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key
    from phlo_api.observatory_api.orchestrator_operations import resolve_orchestrator_operations

    asset_id = asset_id.strip("/")
    target, repository_name, job_names = await _action_context(request, env, asset_id)
    auth = require_scope(request, "lakehouse:operate")
    if payload.job_name not in job_names:
        raise NotFoundError("Job was not found for this asset.")
    enforce_rate_limit(auth["subject"], "materialize_asset")
    require_idempotency_key(payload.idempotency_key)
    provider = resolve_orchestrator_operations()
    tags = {"environment": env, "phlo/ref": target.nessie_ref}

    async def execute() -> dict[str, Any]:
        result = await provider.materialize_asset(
            asset_id,
            {
                **payload.model_dump(),
                "repository_location_name": target.dagster_location,
                "repository_name": repository_name,
                "tags": tags,
            },
        )
        return _action_result(result)

    action_target = f"{env}:{asset_id}@{target.nessie_ref}"
    result = await replay_or_execute_async(
        idempotency_key=payload.idempotency_key,
        operation="v1_materialize_asset",
        target=action_target,
        execute=execute,
        audit=lambda value: audit_operation(
            operation="v1_materialize_asset",
            target=action_target,
            dry_run=payload.dry_run,
            auth=auth,
            payload={"job_name": payload.job_name, "partition_key": payload.partition_key},
            result=value,
        ),
    )
    return AssetActionResponse(
        env=env, asset_id=asset_id, nessie_ref=target.nessie_ref, result=result
    )


@router.post("/assets/{asset_id:path}/backfill", response_model=AssetActionResponse)
async def v1_asset_backfill(
    request: Request,
    asset_id: str,
    payload: BackfillAssetAction,
    env: Environment = Query(),
) -> AssetActionResponse:
    from phlo_api.api.operation_controls import (
        audit_operation,
        enforce_rate_limit,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key
    from phlo_api.observatory_api.orchestrator_operations import resolve_orchestrator_operations

    asset_id = asset_id.strip("/")
    target, repository_name, job_names = await _action_context(request, env, asset_id)
    auth = require_scope(request, "lakehouse:operate")
    if payload.job_name not in job_names:
        raise NotFoundError("Job was not found for this asset.")
    if payload.selection == "explicit" and (
        not payload.partitions
        or any(not key.strip() for key in payload.partitions)
        or len(set(payload.partitions)) != len(payload.partitions)
    ):
        raise HTTPException(
            status_code=422,
            detail="Explicit backfill requires unique, non-empty partition keys.",
        )
    if payload.selection != "explicit" and payload.partitions:
        raise HTTPException(
            status_code=422,
            detail="Partition keys are only accepted for explicit backfill selection.",
        )
    await _validate_backfill_partition_set(
        payload.partition_set_name,
        payload.job_name,
        target.dagster_location,
        repository_name,
    )
    partition_keys = (
        [await _latest_asset_partition(asset_id, target.dagster_location)]
        if payload.selection == "latest"
        else payload.partitions
    )
    enforce_rate_limit(auth["subject"], "backfill_asset")
    require_idempotency_key(payload.idempotency_key)
    provider = resolve_orchestrator_operations()
    tags = {
        "environment": env,
        "phlo/ref": target.nessie_ref,
        "phlo/job": payload.job_name,
        "phlo/selection": payload.selection,
    }

    async def execute() -> dict[str, Any]:
        result = await provider.backfill_asset(
            asset_id,
            {
                **payload.model_dump(exclude={"selection"}),
                "partitions": partition_keys,
                "all_partitions": payload.selection == "all",
                "repository_location_name": target.dagster_location,
                "repository_name": repository_name,
                "tags": tags,
            },
        )
        return _action_result(result)

    intent = json.dumps(
        {
            "job_name": payload.job_name,
            "partition_set_name": payload.partition_set_name,
            "selection": payload.selection,
            "partitions": payload.partitions if payload.selection == "explicit" else [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    intent_digest = hashlib.sha256(intent).hexdigest()
    action_target = f"{env}:{asset_id}@{target.nessie_ref}:backfill:{intent_digest}"
    result = await replay_or_execute_async(
        idempotency_key=payload.idempotency_key,
        operation="v1_backfill_asset",
        target=action_target,
        execute=execute,
        audit=lambda value: audit_operation(
            operation="v1_backfill_asset",
            target=action_target,
            dry_run=payload.dry_run,
            auth=auth,
            payload={
                "job_name": payload.job_name,
                "partition_set_name": payload.partition_set_name,
                "selection": payload.selection,
                "partitions": partition_keys,
                "all_partitions": payload.selection == "all",
            },
            result=value,
        ),
    )
    return AssetActionResponse(
        env=env, asset_id=asset_id, nessie_ref=target.nessie_ref, result=result
    )


@router.get("/assets", response_model=AssetPage)
async def v1_assets(
    request: Request, env: Environment = Query(), limit: Limit = 100, cursor: str | None = None
) -> AssetPage:
    return _page(
        env,
        "assets",
        await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"})),
        limit,
        cursor,
    )


@router.get("/assets/{asset_id:path}/runs", response_model=AssetRuns)
async def v1_asset_runs(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Limit = 100,
    cursor: str | None = None,
) -> AssetRuns:
    asset_id = asset_id.strip("/")
    matches = [
        item
        for item in await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
        if item.id == asset_id
    ]
    if not matches:
        raise NotFoundError("Asset was not found.")
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    dagster_cursor = _decode_asset_run_cursor(cursor, env, asset_id)
    result = await _graphql(ASSET_RUNS_QUERY, {"limit": limit, "cursor": dagster_cursor})
    feed = _response_field(result, "runsFeedOrError")
    rows = feed.get("results")
    next_dagster_cursor = feed.get("cursor")
    has_more = feed.get("hasMore")
    if (
        feed.get("__typename") != "RunsFeedConnection"
        or not isinstance(rows, list)
        or not isinstance(next_dagster_cursor, str)
        or not isinstance(has_more, bool)
        or (has_more and next_dagster_cursor == dagster_cursor)
    ):
        raise BadGatewayError("Dagster returned invalid asset run history.")
    try:
        items = [
            item
            for row in rows
            if (item := _asset_run_view(row, asset_id.split("/"), target.dagster_location))
            is not None
        ]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise BadGatewayError("Dagster returned invalid asset run history.") from exc
    return AssetRuns(
        env=env,
        asset_id=asset_id,
        items=items,
        next_cursor=_asset_run_cursor(env, asset_id, next_dagster_cursor) if has_more else None,
    )


def _check_timestamp(value: Any) -> datetime:
    try:
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise BadGatewayError("Dagster returned an invalid asset-check timestamp.") from exc


def _check_metadata_value(
    entry: dict[str, Any],
) -> str | int | float | bool | dict[str, Any] | None:
    field_by_type = {
        "TextMetadataEntry": "text",
        "IntMetadataEntry": "intValue",
        "FloatMetadataEntry": "floatValue",
        "BoolMetadataEntry": "boolValue",
        "JsonMetadataEntry": "jsonString",
    }
    typename = entry.get("__typename")
    if not isinstance(typename, str):
        return None
    field = field_by_type.get(typename)
    if field is None:
        return None
    value = entry.get(field)
    if typename == "JsonMetadataEntry" and isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BadGatewayError("Dagster returned invalid JSON check metadata.") from exc
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded} if decoded is not None else None
    if typename == "TextMetadataEntry" and isinstance(value, str):
        return value
    if typename == "IntMetadataEntry" and type(value) is int:
        return value
    if typename == "FloatMetadataEntry" and isinstance(value, (int, float)):
        return float(value)
    if typename == "BoolMetadataEntry" and type(value) is bool:
        return value
    raise BadGatewayError("Dagster returned invalid typed asset-check metadata.")


@router.get("/assets/{asset_id:path}/checks", response_model=AssetChecks)
async def v1_asset_checks(
    request: Request, asset_id: str, env: Environment = Query(), limit: CheckLimit = 100
) -> AssetChecks:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    asset_id = asset_id.strip("/")
    key = asset_id.split("/")
    matches = [
        asset
        for asset in await _assets(request, env, allowed_query=frozenset({"env", "limit"}))
        if asset.id == asset_id
    ]
    if not matches:
        raise NotFoundError("Asset was not found.")
    definitions = await _asset_check_definitions(key, target.dagster_location)
    executions = await _asset_check_executions(
        key, [definition.name for definition in definitions], target.dagster_location, limit
    )
    return AssetChecks(env=env, asset_id=asset_id, definitions=definitions, executions=executions)


async def _asset_check_definitions(
    key: list[str], repository_location: str
) -> list[CheckDefinition]:
    result = await _graphql(
        ASSET_CHECKS_QUERY,
        {"assetKeys": [{"path": key}], "limit": _CHECK_DEFINITION_LIMIT + 1},
    )
    data = result.get("data")
    nodes = data.get("assetNodes") if isinstance(data, dict) else None
    if result.get("errors") or not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned invalid asset-check definitions.")
    scoped_nodes = _check_nodes_for_location(nodes, key, repository_location)
    if len(scoped_nodes) != 1:
        raise BackendUnavailableError("Dagster asset-check definitions are not uniquely scoped.")
    response = scoped_nodes[0].get("assetChecksOrError")
    if not isinstance(response, dict) or response.get("__typename") != "AssetChecks":
        raise BackendUnavailableError("Dagster asset-check definitions are unavailable.")
    raw_checks = response.get("checks")
    if not isinstance(raw_checks, list):
        raise BadGatewayError("Dagster returned invalid asset-check definitions.")
    if len(raw_checks) > _CHECK_DEFINITION_LIMIT:
        raise BackendUnavailableError("Asset-check definitions exceed the supported bound.")
    try:
        return [CheckDefinition.model_validate(item) for item in raw_checks]
    except (TypeError, ValueError) as exc:
        raise BadGatewayError("Dagster returned invalid asset-check definitions.") from exc


def _check_nodes_for_location(
    nodes: list[Any], key: list[str], repository_location: str
) -> list[dict[str, Any]]:
    scoped_nodes = []
    for node in nodes:
        if not isinstance(node, dict) or _key_path(node.get("assetKey")) != key:
            raise BadGatewayError("Dagster returned invalid asset-check definitions.")
        if _repository_location(node) == repository_location:
            scoped_nodes.append(node)
    return scoped_nodes


async def _asset_check_executions(
    key: list[str], check_names: list[str], repository_location: str, limit: int
) -> list[CheckExecution]:
    groups, _ = await _asset_check_execution_groups(key, check_names, repository_location, limit)
    executions = [item for group in groups.values() for item in group]
    executions.sort(key=lambda item: (item.timestamp, item.check_name, item.run_id), reverse=True)
    return executions[:limit]


async def _asset_check_execution_groups(
    key: list[str], check_names: list[str], repository_location: str, limit: int
) -> tuple[dict[str, list[CheckExecution]], dict[str, int]]:
    semaphore = asyncio.Semaphore(8)

    async def executions_for_check(
        check_name: str,
    ) -> tuple[str, int, list[CheckExecution]]:
        async with semaphore:
            result = await _graphql(
                ASSET_CHECK_EXECUTIONS_QUERY,
                {"assetKey": {"path": key}, "checkName": check_name, "limit": limit},
            )
        data = result.get("data")
        rows = data.get("assetCheckExecutions") if isinstance(data, dict) else None
        if result.get("errors") or not isinstance(rows, list) or len(rows) > limit:
            raise BadGatewayError("Dagster returned invalid asset-check history.")
        return (
            check_name,
            len(rows),
            _normalize_check_executions(rows, repository_location, check_name),
        )

    groups = await asyncio.gather(*(executions_for_check(name) for name in check_names))
    return (
        {name: executions for name, _, executions in groups},
        {name: count for name, count, _ in groups},
    )


def _normalize_check_executions(
    raw_executions: list[Any], repository_location: str, check_name: str
) -> list[CheckExecution]:
    scoped = [
        _normalize_check_execution(execution, repository_location, check_name)
        for execution in raw_executions
    ]
    return [execution for execution in scoped if execution is not None]


def _normalize_check_execution(
    execution: Any, repository_location: str, check_name: str
) -> CheckExecution | None:
    if not isinstance(execution, dict):
        raise BadGatewayError("Dagster returned invalid asset-check history.")
    run_id = execution.get("runId")
    # Runless evaluations have no repository identity; omit rather than risk
    # disclosing another location's same-key check evaluation.
    if not isinstance(run_id, str) or not run_id:
        return None
    run = execution.get("run")
    origin = run.get("repositoryOrigin") if isinstance(run, dict) else None
    location = origin.get("repositoryLocationName") if isinstance(origin, dict) else None
    if not isinstance(location, str):
        raise BadGatewayError("Dagster could not verify an asset-check run location.")
    if location != repository_location:
        return None
    evaluation = execution.get("evaluation")
    evaluation = evaluation if isinstance(evaluation, dict) else None
    passed = evaluation.get("success") if evaluation is not None else None
    if passed is not None and type(passed) is not bool:
        raise BadGatewayError("Dagster returned invalid asset-check evaluation status.")
    metadata_entries = evaluation.get("metadataEntries") or [] if evaluation else []
    if evaluation is not None and not isinstance(metadata_entries, list):
        raise BadGatewayError("Dagster returned invalid asset-check metadata.")
    metadata = []
    for entry in metadata_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
            raise BadGatewayError("Dagster returned invalid asset-check metadata.")
        metadata.append(CheckMetadata(label=entry["label"], value=_check_metadata_value(entry)))
    if not isinstance(execution.get("status"), str):
        raise BadGatewayError("Dagster returned invalid asset-check history.")
    return CheckExecution(
        status=execution["status"],
        run_id=run_id,
        timestamp=_check_timestamp(execution.get("timestamp")),
        check_name=check_name,
        passed=passed,
        severity=evaluation.get("severity") if evaluation is not None else None,
        metadata=metadata,
    )


async def _overview_quality_checks(
    assets: list[AssetView], repository_location: str
) -> QualityCheckEvidence:
    if len(assets) > _OVERVIEW_CHECK_ASSET_LIMIT:
        return QualityCheckEvidence(status="unknown", counts=None, reason="asset_limit_exceeded")
    definitions_by_asset: list[tuple[list[str], list[CheckDefinition]]] = []
    definition_count = 0
    for asset in assets:
        key = asset.id.split("/")
        definitions = await _asset_check_definitions(key, repository_location)
        definition_count += len(definitions)
        if definition_count > _OVERVIEW_CHECK_LIMIT:
            return QualityCheckEvidence(
                status="unknown", counts=None, reason="check_limit_exceeded"
            )
        definitions_by_asset.append((key, definitions))

    passing = 0
    total = 0
    unevaluated = 0
    failing_assets: list[str] = []
    for key, definitions in definitions_by_asset:
        names = [definition.name for definition in definitions]
        if not names:
            continue
        histories, raw_counts = await _asset_check_execution_groups(
            key, names, repository_location, _CHECK_EXECUTION_SCAN_LIMIT
        )
        for name in names:
            latest_evaluation = max(
                (
                    execution
                    for execution in histories[name]
                    if execution.status == "SUCCEEDED" and execution.passed is not None
                ),
                key=lambda execution: execution.timestamp,
                default=None,
            )
            if latest_evaluation is None:
                if raw_counts[name] >= _CHECK_EXECUTION_SCAN_LIMIT:
                    return QualityCheckEvidence(
                        status="unknown", counts=None, reason="check_history_limit_exceeded"
                    )
                unevaluated += 1
                continue
            total += 1
            if latest_evaluation.passed:
                passing += 1
            else:
                asset_id = "/".join(key)
                if asset_id not in failing_assets:
                    failing_assets.append(asset_id)
    return QualityCheckEvidence(
        status="available",
        counts=QualityCheckCounts(passing=passing, total=total, unevaluated=unevaluated),
        failing_assets=failing_assets,
        reason=None,
    )


def _column_lineage(entries: list[Any]) -> dict[str, list[ColumnLineageDependency]] | None:
    for entry in entries:
        lineage = entry.get("lineage") if isinstance(entry, dict) else None
        if not isinstance(lineage, list):
            continue
        result: dict[str, list[ColumnLineageDependency]] = {}
        try:
            for item in lineage:
                if not isinstance(item, dict) or not isinstance(item.get("columnDeps"), list):
                    raise ValueError
                dependencies = []
                for dependency in item["columnDeps"]:
                    if not isinstance(dependency, dict) or not isinstance(
                        dependency.get("columnName"), str
                    ):
                        raise ValueError
                    dependencies.append(
                        ColumnLineageDependency(
                            asset_key=_key_path(dependency.get("assetKey")),
                            column_name=dependency["columnName"],
                        )
                    )
                result[item["columnName"]] = dependencies
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise BadGatewayError("Dagster returned invalid column-lineage metadata.") from exc
    return None


@router.get("/assets/{asset_id:path}", response_model=AssetDetail)
async def v1_asset_detail(
    request: Request, asset_id: str, env: Environment = Query()
) -> AssetDetail:
    asset_id = asset_id.strip("/")
    matches = [item for item in await _assets(request, env) if item.id == asset_id]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].history_scoped:
        raise BackendUnavailableError("Dagster metadata cannot be scoped to this environment.")
    result = await _graphql(ASSET_DETAIL_QUERY, {"assetKey": {"path": asset_id.split("/")}})
    payload = _response_field(result, "assetNodeOrError")
    if (
        result.get("errors")
        or not isinstance(payload, dict)
        or payload.get("__typename") != "AssetNode"
    ):
        raise BadGatewayError("Dagster returned invalid asset detail.")
    repository = payload.get("repository")
    location = repository.get("location") if isinstance(repository, dict) else None
    if (
        not isinstance(location, dict)
        or location.get("name") != _target(request, env).dagster_location
    ):
        raise NotFoundError("Asset was not found.")
    detail = _asset_view(payload)
    definition_entries = payload.get("metadataEntries") or []
    materials = payload.get("assetMaterializations") or []
    materialization_entries = materials[0].get("metadataEntries") or [] if materials else []

    def schema_columns(entries: list[Any]) -> list[AssetColumn]:
        for entry in entries:
            schema = entry.get("schema") if isinstance(entry, dict) else None
            if isinstance(schema, dict) and isinstance(schema.get("columns"), list):
                try:
                    return [AssetColumn.model_validate(col) for col in schema["columns"]]
                except (TypeError, ValueError) as exc:
                    raise BadGatewayError(
                        "Dagster returned invalid asset schema metadata."
                    ) from exc
        return []

    latest_schema = schema_columns(materialization_entries)
    columns = latest_schema or schema_columns(definition_entries)

    observed_lineage = _column_lineage(materialization_entries) or _column_lineage(
        definition_entries
    )
    return AssetDetail(
        **detail.model_dump(),
        columns=columns,
        schema_observed_at=detail.last_materialization_at if latest_schema else None,
        column_lineage=observed_lineage,
    )


@router.get("/sources", response_model=AssetPage)
async def v1_sources(
    request: Request, env: Environment = Query(), limit: Limit = 100, cursor: str | None = None
) -> AssetPage:
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    return _page(env, "sources", [asset for asset in assets if asset.is_source], limit, cursor)


@router.get("/layers", response_model=LayerPage)
async def v1_layers(
    request: Request, env: Environment = Query(), limit: Limit = 100, cursor: str | None = None
) -> LayerPage:
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    groups: dict[str | None, list[AssetView]] = {}
    for asset in assets:
        groups.setdefault(asset.group_name, []).append(asset)
    layers = [
        LayerView(
            group_name=name,
            asset_count=len(group),
            materialized_asset_count=sum(
                asset.last_materialization_at is not None for asset in group
            ),
            latest_materialization_at=max(
                (asset.last_materialization_at for asset in group if asset.last_materialization_at),
                default=None,
            ),
        )
        for name, group in sorted(groups.items(), key=lambda item: item[0] or "")
    ]
    offset = _offset(cursor, env, "layers")
    selected = layers[offset : offset + limit]
    next_cursor = _cursor(env, "layers", offset + limit) if offset + limit < len(layers) else None
    return LayerPage(env=env, items=selected, next_cursor=next_cursor)


@router.get("/overview", response_model=OverviewResponse)
async def v1_overview(request: Request, env: Environment = Query()) -> OverviewResponse:
    assets = await _assets(request, env)
    from phlo_api.api.v1 import _runs
    from phlo_api.incidents import incident_stats, list_asset_incident_policies

    target = _target(request, env)
    runs = await _runs(target.dagster_location)
    try:
        quality_checks = await _overview_quality_checks(assets, target.dagster_location)
    except (BackendUnavailableError, BadGatewayError, NotFoundError):
        quality_checks = QualityCheckEvidence(
            status="unknown", counts=None, reason="source_unavailable"
        )
    stats = incident_stats(request, env)
    policies: dict[str, int] = {}
    cursor = None
    while True:
        page = list_asset_incident_policies(request, env, limit=500, cursor=cursor)
        policies.update(
            {
                item["asset_id"]: item["freshness_sla_seconds"]
                for item in page.items
                if item["freshness_sla_seconds"] is not None
            }
        )
        cursor = page.next_cursor
        if cursor is None:
            break
    freshness = {"fresh": 0, "stale": 0, "unknown": 0}
    now = datetime.now(UTC)
    for asset in assets:
        sla = policies.get(asset.id)
        observed = asset.last_materialization_at
        if sla is None or observed is None:
            freshness["unknown"] += 1
        elif (now - observed).total_seconds() > sla:
            freshness["stale"] += 1
        else:
            freshness["fresh"] += 1
    latest = max(
        (asset.last_materialization_at for asset in assets if asset.last_materialization_at),
        default=None,
    )
    run_status_counts: dict[str, int] = {}
    for status in runs.values():
        run_status_counts[status] = run_status_counts.get(status, 0) + 1
    return OverviewResponse(
        env=env,
        asset_count=len(assets),
        materialized_asset_count=sum(asset.last_materialization_at is not None for asset in assets),
        latest_materialization_at=latest,
        incident_counts=stats["counts"],
        freshness_counts=FreshnessCounts(**freshness),
        run_status_counts=run_status_counts,
        run_history_truncated=len(runs) == 100,
        quality_checks=quality_checks,
    )
