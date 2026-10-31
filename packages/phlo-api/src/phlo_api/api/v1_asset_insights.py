"""Environment-scoped asset quality, partition, lineage, and schema-comparison reads.

Every resource resolves the selected environment to its Dagster code location
and Nessie ref and reads only definitions and evidence bound to that pair.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime

from phlo.config.settings import get_settings
from phlo_api.api import v1_assets as assets
from phlo_api.api.v1 import _run_on_ref, _target
from phlo_api.api.v1_pages import decode_cursor, decode_offset, encode_cursor
from phlo_api.errors import (
    BackendUnavailableError,
    BadGatewayError,
    BadInputError,
    ConflictError,
    NotFoundError,
)
from phlo_api.run_evidence import get_run_evidence_store
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 asset insights"])

Direction = Literal["upstream", "downstream", "both"]
QualityStatus = Literal["passed", "failed", "unevaluated", "unverifiable"]
_ICEBERG_READ_TIMEOUT_SECONDS = 5
_MATERIALIZATION_WINDOW = 100


class QualityCheckState(WireModel):
    asset_id: str
    check_name: str
    description: str | None
    status: QualityStatus
    latest: assets.CheckExecution | None


class QualityCheckPage(WireModel):
    env: Environment
    items: list[QualityCheckState]
    unverified_assets: list[str]
    next_cursor: str | None


class AssetPartition(WireModel):
    partition_key: str


class AssetPartitionPage(WireModel):
    env: Environment
    asset_id: str
    partitioned: bool
    partition_type: str | None
    items: list[AssetPartition]
    next_cursor: str | None


class AssetMaterialization(WireModel):
    run_id: str
    job_id: str | None
    partition_key: str | None
    timestamp: AwareDatetime


class AssetMaterializationPage(WireModel):
    env: Environment
    asset_id: str
    items: list[AssetMaterialization]
    history_window: int
    history_truncated: bool
    next_cursor: str | None


class AssetGraphNode(WireModel):
    id: str
    key: list[str]
    description: str | None
    group_name: str | None
    compute_kind: str | None
    layer: Literal["bronze", "silver", "gold"] | None
    is_source: bool
    distance: int


class AssetGraphEdge(WireModel):
    source: str
    target: str


class AssetNeighbors(WireModel):
    env: Environment
    asset_id: str
    direction: Direction
    depth: int
    nodes: list[AssetGraphNode]
    edges: list[AssetGraphEdge]


class SchemaField(WireModel):
    field_id: int
    name: str
    type: str
    required: bool


class SchemaVersionRef(WireModel):
    run_id: str | None
    snapshot_id: str
    schema_id: int
    observed_at: AwareDatetime | None


class SchemaChange(WireModel):
    change: Literal["added", "removed", "renamed", "type_changed", "requirement_changed"]
    field_id: int
    name: str
    previous_name: str | None = None
    type: str | None = None
    previous_type: str | None = None
    required: bool | None = None
    previous_required: bool | None = None


class AssetSchemaDiff(WireModel):
    env: Environment
    asset_id: str
    table_name: str
    nessie_ref: str
    from_schema: SchemaVersionRef
    to_schema: SchemaVersionRef
    changes: list[SchemaChange]
    fields: list[SchemaField]


async def _asset(request: Request, env: Environment, asset_id: str, allowed: frozenset[str]):
    for item in await assets._assets(request, env, allowed_query=allowed):
        if item.id == asset_id:
            return item
    raise NotFoundError("Asset was not found.")


@router.get("/quality-checks", response_model=QualityCheckPage)
async def v1_quality_checks(
    request: Request,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> QualityCheckPage:
    """List every declared asset check with its latest environment-scoped evaluation.

    Items are ordered by asset ID, then check name. Assets whose check
    definitions cannot be bound to the selected code location are listed in
    ``unverified_assets`` instead of being omitted silently.
    """
    allowed = frozenset({"env", "limit", "cursor"})
    target = _target(request, env, allowed_query=allowed)
    scope = ["quality-checks", env, target.dagster_location, target.nessie_ref]
    offset = decode_offset(cursor, scope)
    inventory = await assets._assets(request, env, allowed_query=allowed)
    ambiguous_repositories = {
        asset.repository_name for asset in inventory if asset.check_definition_scope != "unique"
    }
    nodes_by_repository: dict[str | None, list[dict[str, Any]]] = {}
    declared: list[tuple[Any, assets.CheckDefinition]] = []
    unverified: list[str] = []
    for asset in inventory:
        if asset.repository_name not in nodes_by_repository:
            nodes_by_repository[asset.repository_name] = await assets._asset_check_inventory(
                target.dagster_location,
                asset.repository_name,
                include_legacy=asset.repository_name not in ambiguous_repositories,
            )
        nodes = nodes_by_repository[asset.repository_name]
        try:
            unique = asset.check_definition_scope == "unique"
            if not unique and not assets._has_local_check_export(nodes, asset.key):
                unverified.append(asset.id)
                continue
            definitions = assets._check_definitions(nodes, asset.key, allow_legacy=unique)
        except BackendUnavailableError:
            unverified.append(asset.id)
            continue
        declared.extend(
            (asset, definition) for definition in sorted(definitions, key=lambda d: d.name)
        )

    page = declared[offset : offset + limit]
    by_asset: dict[str, tuple[Any, list[assets.CheckDefinition]]] = {}
    for asset, definition in page:
        by_asset.setdefault(asset.id, (asset, []))[1].append(definition)
    items: list[QualityCheckState] = []
    for asset, definitions in by_asset.values():
        histories, raw_counts, unverifiable = await assets._asset_check_execution_groups(
            asset.key,
            [definition.name for definition in definitions],
            target.dagster_location,
            asset.repository_name,
            target.nessie_ref,
            assets._CHECK_EXECUTION_SCAN_LIMIT,
        )
        for definition in definitions:
            name = definition.name
            latest = max(histories[name], key=lambda execution: execution.timestamp, default=None)
            status: QualityStatus
            if name in unverifiable:
                status, latest = "unverifiable", None
            elif (
                latest is not None
                and latest.passed is not None
                and latest.status in {"SUCCEEDED", "FAILED"}
            ):
                status = "passed" if latest.passed else "failed"
            elif raw_counts[name] >= assets._CHECK_EXECUTION_SCAN_LIMIT:
                status, latest = "unverifiable", None
            else:
                status = "unevaluated"
            items.append(
                QualityCheckState(
                    asset_id=asset.id,
                    check_name=name,
                    description=definition.description,
                    status=status,
                    latest=latest,
                )
            )
    following = offset + limit
    return QualityCheckPage(
        env=env,
        items=items,
        unverified_assets=unverified,
        next_cursor=encode_cursor(scope, following) if following < len(declared) else None,
    )


_PARTITIONS_QUERY = """query V1AssetPartitions($selector: RepositorySelector!, $limit: Int!, $cursor: String) {
  repositoryOrError(repositorySelector: $selector) {
    __typename
    ... on Repository {
      name location { name }
      assetNodes {
        assetKey { path } isPartitioned
        repository { name location { name } }
        partitionDefinition { type }
        partitionKeyConnection(limit: $limit, ascending: false, cursor: $cursor) { results cursor hasMore }
      }
    }
  }
}"""


@router.get("/assets/{asset_id:path}/partitions", response_model=AssetPartitionPage)
async def v1_asset_partitions(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> AssetPartitionPage:
    """List the asset's partition keys, newest first, from its environment's code location."""
    allowed = frozenset({"env", "limit", "cursor"})
    target = _target(request, env, allowed_query=allowed)
    asset_id = asset_id.strip("/")
    asset = await _asset(request, env, asset_id, allowed)
    scope = ["partitions", env, target.dagster_location, asset_id]
    dagster_cursor = decode_cursor(cursor, scope) if cursor is not None else None
    if not isinstance(dagster_cursor, str | None):
        raise BadInputError("Cursor is malformed or was issued for another scope.")
    if not asset.repository_name:
        raise BackendUnavailableError("Dagster asset repository identity is unavailable.")
    repository = assets._response_field(
        await assets._graphql(
            _PARTITIONS_QUERY,
            {
                "selector": {
                    "repositoryLocationName": target.dagster_location,
                    "repositoryName": asset.repository_name,
                },
                "limit": limit,
                "cursor": dagster_cursor,
            },
        ),
        "repositoryOrError",
    )
    nodes = repository.get("assetNodes")
    if repository.get("__typename") != "Repository" or not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned an invalid partition response.")
    matches = [
        node
        for node in nodes
        if isinstance(node, dict)
        and assets._key_path(node.get("assetKey")) == asset.key
        and assets._repository_location(node) == target.dagster_location
    ]
    if len(matches) != 1:
        raise BackendUnavailableError("Asset has no unique definition in this code location.")
    node = matches[0]
    if node.get("isPartitioned") is not True:
        return AssetPartitionPage(
            env=env,
            asset_id=asset_id,
            partitioned=False,
            partition_type=None,
            items=[],
            next_cursor=None,
        )
    connection = node.get("partitionKeyConnection")
    definition = node.get("partitionDefinition")
    keys = connection.get("results") if isinstance(connection, dict) else None
    following = connection.get("cursor") if isinstance(connection, dict) else None
    has_more = connection.get("hasMore") if isinstance(connection, dict) else None
    partition_type = definition.get("type") if isinstance(definition, dict) else None
    if (
        not isinstance(keys, list)
        or any(not isinstance(key, str) or not key for key in keys)
        or not isinstance(has_more, bool)
        or (has_more and (not isinstance(following, str) or following == dagster_cursor))
        or not isinstance(partition_type, str | None)
    ):
        raise BadGatewayError("Dagster returned an invalid partition response.")
    return AssetPartitionPage(
        env=env,
        asset_id=asset_id,
        partitioned=True,
        partition_type=partition_type,
        items=[AssetPartition(partition_key=key) for key in keys],
        next_cursor=encode_cursor(scope, following)
        if has_more and isinstance(following, str)
        else None,
    )


@router.get("/assets/{asset_id:path}/materializations", response_model=AssetMaterializationPage)
async def v1_asset_materializations(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> AssetMaterializationPage:
    """List successful materializations recorded on the environment's location and ref.

    Dagster reports the newest ``history_window`` materialization events per
    asset; events from other code locations or refs are excluded before
    pagination, and ``history_truncated`` reports that older events exist.
    """
    allowed = frozenset({"env", "limit", "cursor"})
    target = _target(request, env, allowed_query=allowed)
    asset_id = asset_id.strip("/")
    scope = ["materializations", env, target.dagster_location, target.nessie_ref, asset_id]
    offset = decode_offset(cursor, scope)
    nodes = [
        node
        for node in await assets._asset_nodes(request, env, allowed_query=allowed)
        if assets._key_path(node.get("assetKey")) == asset_id.split("/")
    ]
    if not nodes:
        raise NotFoundError("Asset was not found.")
    events = nodes[0].get("assetMaterializations")
    if not isinstance(events, list):
        raise BadGatewayError("Dagster returned invalid materialization history.")
    items: list[AssetMaterialization] = []
    try:
        for event in events:
            run = event.get("runOrError")
            origin = run.get("repositoryOrigin") if isinstance(run, dict) else None
            if not (
                isinstance(run, dict)
                and run.get("__typename") == "Run"
                and run.get("status") == "SUCCESS"
                and run.get("runId") == event.get("runId")
                and isinstance(origin, dict)
                and origin.get("repositoryLocationName") == target.dagster_location
                and _run_on_ref(run, target.nessie_ref)
            ):
                continue
            items.append(
                AssetMaterialization(
                    run_id=event["runId"],
                    job_id=run.get("pipelineName"),
                    partition_key=event.get("partition"),
                    timestamp=datetime.fromtimestamp(float(event["timestamp"]) / 1000, UTC),
                )
            )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BadGatewayError("Dagster returned invalid materialization history.") from exc
    items.sort(key=lambda item: (item.timestamp, item.run_id), reverse=True)
    following = offset + limit
    return AssetMaterializationPage(
        env=env,
        asset_id=asset_id,
        items=items[offset:following],
        history_window=_MATERIALIZATION_WINDOW,
        history_truncated=len(events) >= _MATERIALIZATION_WINDOW,
        next_cursor=encode_cursor(scope, following) if following < len(items) else None,
    )


@router.get("/assets/{asset_id:path}/neighbors", response_model=AssetNeighbors)
async def v1_asset_neighbors(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    direction: Direction = "both",
    depth: Annotated[int, Query(ge=1, le=10)] = 1,
) -> AssetNeighbors:
    """Return the asset's upstream and/or downstream lineage within ``depth`` dependency hops.

    ``both`` walks ancestors and descendants separately, so siblings that
    only share an upstream are not included. Edges point from the upstream
    asset to the dependent asset and join only returned nodes.
    """
    allowed = frozenset({"env", "direction", "depth"})
    asset_id = asset_id.strip("/")
    inventory = {
        item.id: item for item in await assets._assets(request, env, allowed_query=allowed)
    }
    if asset_id not in inventory:
        raise NotFoundError("Asset was not found.")
    upstream = {
        item.id: ["/".join(key) for key in item.dependencies if "/".join(key) in inventory]
        for item in inventory.values()
    }
    downstream: dict[str, list[str]] = {item_id: [] for item_id in inventory}
    for item_id, parents in upstream.items():
        for parent in parents:
            downstream[parent].append(item_id)

    distances = {asset_id: 0}
    walks = (
        (upstream, downstream)
        if direction == "both"
        else (upstream,)
        if direction == "upstream"
        else (downstream,)
    )
    for graph in walks:
        queue = deque([(asset_id, 0)])
        seen = {asset_id}
        while queue:
            current, distance = queue.popleft()
            if distance == depth:
                continue
            for neighbour in graph[current]:
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                distances[neighbour] = min(distances.get(neighbour, distance + 1), distance + 1)
                queue.append((neighbour, distance + 1))

    nodes = [
        AssetGraphNode(
            id=item.id,
            key=item.key,
            description=item.description,
            group_name=item.group_name,
            compute_kind=item.compute_kind,
            layer=item.layer,
            is_source=item.is_source,
            distance=distances[item.id],
        )
        for item_id, item in sorted(inventory.items())
        if item_id in distances
    ]
    edges = [
        AssetGraphEdge(source=parent, target=child)
        for child in sorted(distances)
        for parent in sorted(upstream[child])
        if parent in distances
    ]
    return AssetNeighbors(
        env=env, asset_id=asset_id, direction=direction, depth=depth, nodes=nodes, edges=edges
    )


def _load_table(table_name: str, ref: str) -> Any:
    from phlo_iceberg.catalog import get_catalog

    return get_catalog(ref=ref).load_table(table_name)


def _schema_versions(
    table_name: str, ref: str, snapshot_ids: tuple[int, int | None]
) -> list[tuple[int, int, list[SchemaField]] | None]:
    """Resolve each snapshot on ``ref`` to its schema; ``None`` selects the current snapshot."""
    table = _load_table(table_name, ref)
    schemas = table.schemas()
    resolved: list[tuple[int, int, list[SchemaField]] | None] = []
    for snapshot_id in snapshot_ids:
        snapshot = (
            table.current_snapshot() if snapshot_id is None else table.snapshot_by_id(snapshot_id)
        )
        schema = schemas.get(snapshot.schema_id) if snapshot is not None else None
        if snapshot is None or schema is None:
            resolved.append(None)
            continue
        resolved.append(
            (
                int(snapshot.snapshot_id),
                int(schema.schema_id),
                [
                    SchemaField(
                        field_id=field.field_id,
                        name=field.name,
                        type=str(field.field_type),
                        required=field.required,
                    )
                    for field in schema.fields
                ],
            )
        )
    return resolved


def _run_snapshot(request: Request, run_id: str, table_name: str) -> tuple[int, Any]:
    """Return the final snapshot a run wrote to ``table_name`` and when the run finished."""
    project_id = (get_settings().phlo_project or "").strip()
    if not project_id:
        raise BackendUnavailableError("Run evidence project identity is not configured.")
    store = get_run_evidence_store(request)
    try:
        run = store.get_run(project_id, run_id)
        resources = store.list_resources(project_id, run_id) if run is not None else []
    except Exception as exc:
        raise BackendUnavailableError("Run evidence storage is unavailable.") from exc
    if run is None:
        raise NotFoundError(f"Run {run_id} has no recorded evidence.")
    writes = [
        item
        for item in resources
        if item.get("role") == "output"
        and item.get("table_name") == table_name
        and item.get("snapshot_after")
    ]
    if writes:
        latest_attempt = max(int(item.get("attempt") or 1) for item in writes)
        writes = [item for item in writes if int(item.get("attempt") or 1) == latest_attempt]
    after = {str(item["snapshot_after"]) for item in writes}
    final = after - {str(item.get("snapshot_before")) for item in writes}
    if not after:
        raise NotFoundError(f"Run {run_id} recorded no snapshot for this asset's table.")
    if len(final) != 1:
        raise ConflictError(f"Run {run_id} recorded ambiguous snapshots for this asset's table.")
    try:
        snapshot_id = int(final.pop())
    except ValueError as exc:
        raise BadGatewayError("Run evidence recorded an invalid snapshot ID.") from exc
    finished = run.get("finished_at")
    try:
        observed = datetime.fromisoformat(finished) if isinstance(finished, str) else None
    except ValueError:
        observed = None
    return snapshot_id, observed if observed is not None and observed.tzinfo else None


def _schema_changes(before: list[SchemaField], after: list[SchemaField]) -> list[SchemaChange]:
    old = {field.field_id: field for field in before}
    new = {field.field_id: field for field in after}
    changes: list[SchemaChange] = []
    for field_id in sorted(old.keys() | new.keys()):
        previous, current = old.get(field_id), new.get(field_id)
        if previous is None and current is not None:
            changes.append(
                SchemaChange(
                    change="added",
                    field_id=field_id,
                    name=current.name,
                    type=current.type,
                    required=current.required,
                )
            )
        elif current is None and previous is not None:
            changes.append(
                SchemaChange(
                    change="removed",
                    field_id=field_id,
                    name=previous.name,
                    previous_type=previous.type,
                    previous_required=previous.required,
                )
            )
        elif previous is not None and current is not None:
            if previous.name != current.name:
                changes.append(
                    SchemaChange(
                        change="renamed",
                        field_id=field_id,
                        name=current.name,
                        previous_name=previous.name,
                    )
                )
            if previous.type != current.type:
                changes.append(
                    SchemaChange(
                        change="type_changed",
                        field_id=field_id,
                        name=current.name,
                        type=current.type,
                        previous_type=previous.type,
                    )
                )
            if previous.required != current.required:
                changes.append(
                    SchemaChange(
                        change="requirement_changed",
                        field_id=field_id,
                        name=current.name,
                        required=current.required,
                        previous_required=previous.required,
                    )
                )
    return changes


@router.get("/assets/{asset_id:path}/schema-diff", response_model=AssetSchemaDiff)
async def v1_asset_schema_diff(
    request: Request,
    asset_id: str,
    from_run: Annotated[str, Query(min_length=1, max_length=256)],
    env: Environment = Query(),
    to_run: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> AssetSchemaDiff:
    """Compare the table schema a run wrote with a later run's, or with the current schema.

    Each run resolves through its recorded run evidence to the final Iceberg
    snapshot it wrote to the asset's table. The snapshot must be part of the
    table history on the environment's Nessie ref; fields are matched by
    Iceberg field ID, so a rename is reported once, not as a removal and an
    addition.
    """
    allowed = frozenset({"env", "from_run", "to_run"})
    target = _target(request, env, allowed_query=allowed)
    asset_id = asset_id.strip("/")
    asset = await _asset(request, env, asset_id, allowed)
    if asset.relation is None:
        raise NotFoundError("Asset has no declared physical table.")
    table_name = asset.relation
    from_snapshot, from_observed = await asyncio.to_thread(
        _run_snapshot, request, from_run, table_name
    )
    to_snapshot, to_observed = (
        await asyncio.to_thread(_run_snapshot, request, to_run, table_name)
        if to_run is not None
        else (None, None)
    )
    try:
        resolved = await asyncio.wait_for(
            asyncio.to_thread(
                _schema_versions, table_name, target.nessie_ref, (from_snapshot, to_snapshot)
            ),
            timeout=_ICEBERG_READ_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise BackendUnavailableError("Iceberg schema read exceeded the time budget.") from exc
    except Exception as exc:
        raise BackendUnavailableError("Ref-scoped Iceberg table metadata is unavailable.") from exc
    before, after = resolved
    if before is None:
        raise NotFoundError(f"Run {from_run} wrote no snapshot on this environment's ref.")
    if after is None:
        raise NotFoundError(
            f"Run {to_run} wrote no snapshot on this environment's ref."
            if to_run is not None
            else "The table has no current snapshot on this environment's ref."
        )
    return AssetSchemaDiff(
        env=env,
        asset_id=asset_id,
        table_name=table_name,
        nessie_ref=target.nessie_ref,
        from_schema=SchemaVersionRef(
            run_id=from_run,
            snapshot_id=str(before[0]),
            schema_id=before[1],
            observed_at=from_observed,
        ),
        to_schema=SchemaVersionRef(
            run_id=to_run, snapshot_id=str(after[0]), schema_id=after[1], observed_at=to_observed
        ),
        changes=_schema_changes(before[2], after[2]),
        fields=after[2],
    )
