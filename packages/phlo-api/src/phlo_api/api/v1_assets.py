"""Environment-scoped asset and overview read models for /api/v1."""

from __future__ import annotations

import base64
import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from phlo_api.api.v1 import _target
from phlo_api.errors import BackendUnavailableError, BadGatewayError, NotFoundError
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 assets"])
Limit = Annotated[int, Query(ge=1, le=500)]
CheckLimit = Annotated[int, Query(ge=1, le=100)]

ASSET_QUERY = """query V1Assets {
  assetNodes {
    id assetKey { path } description computeKind groupName isSource
    repository { name location { name } }
    dependencyKeys { path }
    assetMaterializations(limit: 1) { timestamp runId }
  }
}"""
ASSET_DETAIL_QUERY = """query V1AssetDetail($assetKey: AssetKeyInput!) {
  assetNodeOrError(assetKey: $assetKey) {
    __typename
    ... on AssetNode {
      id assetKey { path } description computeKind groupName isSource
      repository { name location { name } }
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
ASSET_RUNS_QUERY = """query V1AssetRuns($assetKey: AssetKeyInput!, $limit: Int!) {
  assetOrError(assetKey: $assetKey) {
    __typename
    ... on Asset { assetMaterializations(limit: $limit) { timestamp runId stepKey } }
    ... on AssetNotFoundError { message }
  }
}"""
ASSET_CHECKS_QUERY = """query V1AssetChecks {
  assetNodes {
    assetKey { path }
    repository { location { name } }
    assetChecksOrError {
      __typename
      ... on AssetChecks { checks { name description } }
      ... on AssetCheckNeedsMigrationError { message }
    }
  }
}"""
ASSET_CHECK_EXECUTIONS_QUERY = """query V1AssetCheckExecutions($assetKey: AssetKeyInput!, $limit: Int!) {
  assetCheckExecutions(assetKey: $assetKey, limit: $limit) {
    status runId timestamp checkName
    evaluation {
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
  }
}"""
ASSET_CHECK_RUN_QUERY = """query V1AssetCheckRunLocation($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run { runId repositoryOrigin { repositoryLocationName } }
    ... on RunNotFoundError { message }
    ... on PythonError { message }
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


class Materialization(WireModel):
    timestamp: AwareDatetime
    run_id: str
    step_key: str | None = None


class AssetRuns(WireModel):
    env: Environment
    asset_id: str
    items: list[Materialization]


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
    cost_status: str = "unavailable: no cost source is configured"


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


class OverviewResponse(WireModel):
    env: Environment
    asset_count: int = Field(ge=0)
    materialized_asset_count: int = Field(ge=0)
    latest_materialization_at: AwareDatetime | None
    incident_counts: dict[str, int]
    freshness_counts: FreshnessCounts
    run_status_counts: dict[str, int]
    run_history_truncated: bool
    audit_counts: None = None


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
        is_source=node.get("isSource") is True,
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
    request: Request, asset_id: str, env: Environment = Query(), limit: Limit = 100
) -> AssetRuns:
    asset_id = asset_id.strip("/")
    matches = [
        item
        for item in await _assets(request, env, allowed_query=frozenset({"env", "limit"}))
        if item.id == asset_id
    ]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].history_scoped:
        raise BackendUnavailableError("Dagster history cannot be scoped to this environment.")
    result = await _graphql(
        ASSET_RUNS_QUERY,
        {"assetKey": {"path": asset_id.split("/")}, "limit": limit},
    )
    data = _response_field(result, "assetOrError")
    rows = data.get("assetMaterializations")
    if result.get("errors") or not isinstance(rows, list) or data.get("__typename") != "Asset":
        raise BadGatewayError("Dagster returned invalid asset history.")
    try:
        items = [
            Materialization(
                timestamp=datetime.fromtimestamp(float(row["timestamp"]), UTC),
                run_id=row["runId"],
                step_key=row.get("stepKey"),
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise BadGatewayError("Dagster returned invalid asset history.") from exc
    return AssetRuns(env=env, asset_id=asset_id, items=items)


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


async def _check_run_location(run_id: str) -> str:
    result = await _graphql(ASSET_CHECK_RUN_QUERY, {"runId": run_id})
    payload = _response_field(result, "runOrError")
    origin = payload.get("repositoryOrigin")
    location = origin.get("repositoryLocationName") if isinstance(origin, dict) else None
    if payload.get("__typename") != "Run" or payload.get("runId") != run_id:
        raise BadGatewayError("Dagster could not resolve an asset-check run.")
    if not isinstance(location, str):
        raise BadGatewayError("Dagster asset-check run has no repository location.")
    return location


@router.get("/assets/{asset_id:path}/checks", response_model=AssetChecks)
async def v1_asset_checks(
    request: Request, asset_id: str, env: Environment = Query(), limit: CheckLimit = 100
) -> AssetChecks:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    asset_id = asset_id.strip("/")
    key = asset_id.split("/")
    definitions_result = await _graphql(ASSET_CHECKS_QUERY)
    data = definitions_result.get("data")
    nodes = data.get("assetNodes") if isinstance(data, dict) else None
    if definitions_result.get("errors") or not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned invalid asset-check definitions.")
    scoped_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            raise BadGatewayError("Dagster returned invalid asset-check definitions.")
        repository = node.get("repository")
        location = repository.get("location") if isinstance(repository, dict) else None
        if _key_path(node.get("assetKey")) == key and isinstance(location, dict):
            if location.get("name") == target.dagster_location:
                scoped_nodes.append(node)
    if not scoped_nodes:
        raise NotFoundError("Asset was not found.")

    checks: list[CheckDefinition] = []
    for node in scoped_nodes:
        response = node.get("assetChecksOrError")
        if not isinstance(response, dict) or response.get("__typename") != "AssetChecks":
            raise BackendUnavailableError("Dagster asset-check definitions are unavailable.")
        raw_checks = response.get("checks")
        if not isinstance(raw_checks, list):
            raise BadGatewayError("Dagster returned invalid asset-check definitions.")
        try:
            checks.extend(CheckDefinition.model_validate(item) for item in raw_checks)
        except (TypeError, ValueError) as exc:
            raise BadGatewayError("Dagster returned invalid asset-check definitions.") from exc

    execution_result = await _graphql(
        ASSET_CHECK_EXECUTIONS_QUERY,
        {"assetKey": {"path": key}, "limit": limit},
    )
    data = execution_result.get("data")
    executions = data.get("assetCheckExecutions") if isinstance(data, dict) else None
    if execution_result.get("errors") or not isinstance(executions, list):
        raise BadGatewayError("Dagster returned invalid asset-check history.")
    raw_executions = executions[:limit]
    run_ids = list(
        dict.fromkeys(
            execution.get("runId")
            for execution in raw_executions
            if isinstance(execution, dict) and isinstance(execution.get("runId"), str)
        )
    )
    semaphore = asyncio.Semaphore(8)

    async def get_run_location(run_id: str) -> tuple[str, str]:
        async with semaphore:
            return run_id, await _check_run_location(run_id)

    run_locations = dict(await asyncio.gather(*(get_run_location(run_id) for run_id in run_ids)))
    scoped_executions: list[CheckExecution] = []
    for execution in raw_executions:
        if not isinstance(execution, dict):
            raise BadGatewayError("Dagster returned invalid asset-check history.")
        run_id = execution.get("runId")
        # Runless evaluations have no repository identity; omit rather than risk
        # disclosing another location's same-key check evaluation.
        if not isinstance(run_id, str) or run_locations.get(run_id) != target.dagster_location:
            continue
        evaluation = execution.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        metadata_entries = evaluation.get("metadataEntries") or []
        if not isinstance(metadata_entries, list):
            raise BadGatewayError("Dagster returned invalid asset-check metadata.")
        metadata = []
        for entry in metadata_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
                raise BadGatewayError("Dagster returned invalid asset-check metadata.")
            metadata.append(CheckMetadata(label=entry["label"], value=_check_metadata_value(entry)))
        if not isinstance(execution.get("checkName"), str) or not isinstance(
            execution.get("status"), str
        ):
            raise BadGatewayError("Dagster returned invalid asset-check history.")
        scoped_executions.append(
            CheckExecution(
                status=execution["status"],
                run_id=run_id,
                timestamp=_check_timestamp(execution.get("timestamp")),
                check_name=execution["checkName"],
                severity=evaluation.get("severity"),
                metadata=metadata,
            )
        )
    return AssetChecks(
        env=env,
        asset_id=asset_id,
        definitions=checks,
        executions=scoped_executions,
    )


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

    def column_lineage(entries: list[Any]) -> dict[str, list[ColumnLineageDependency]] | None:
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

    observed_lineage = column_lineage(materialization_entries) or column_lineage(definition_entries)
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
    )
