"""Environment-scoped asset and overview read models for /api/v1."""

from __future__ import annotations

import base64
import json
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
      metadataEntries { label description ... on TableSchemaMetadataEntry { schema { columns { name type description } } } }
      assetMaterializations(limit: 1) {
        timestamp runId
        metadataEntries { label ... on TableSchemaMetadataEntry { schema { columns { name type description } } } }
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


class AssetColumn(WireModel):
    name: str
    type: str | None
    description: str | None


class Materialization(WireModel):
    timestamp: AwareDatetime
    run_id: str
    step_key: str | None = None


class AssetRuns(WireModel):
    env: Environment
    asset_id: str
    items: list[Materialization]


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
    return AssetDetail(
        **detail.model_dump(),
        columns=columns,
        schema_observed_at=detail.last_materialization_at if latest_schema else None,
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
    from phlo_api.incidents import incident_stats, list_asset_incident_policies

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
    return OverviewResponse(
        env=env,
        asset_count=len(assets),
        materialized_asset_count=sum(asset.last_materialization_at is not None for asset in assets),
        latest_materialization_at=latest,
        incident_counts=stats["counts"],
        freshness_counts=FreshnessCounts(**freshness),
    )
