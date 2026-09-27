"""Dagster GraphQL operations used by the Observatory API."""

from __future__ import annotations

from typing import Any

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
from phlo_api.observatory_api.http_client import backend_client

logger = get_logger(__name__)

DEFAULT_DAGSTER_URL = "http://dagster:3000/graphql"


def resolve_dagster_url() -> str:
    """Resolve the Dagster GraphQL URL from the project environment or default."""
    env_url = project_env_value("DAGSTER_GRAPHQL_URL")
    return resolve_url(env_url or DEFAULT_DAGSTER_URL, port_env_var="DAGSTER_PORT")


# --- GraphQL Queries ---

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

# --- Pydantic Models ---


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
    all_partitions: bool = False
    job_name: str | None = None
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

    async with backend_client() as client:
        response = await client.post(
            url,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


async def get_assets() -> list[Asset] | dict[str, str]:
    """List all Dagster assets with their latest materialization.

    Errors return {'error': message} instead of raising.
    """
    url = resolve_dagster_url()

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


async def get_materialization_history(
    asset_key_path: str,
    limit: int = 20,
) -> list[MaterializationEvent] | dict[str, str]:
    """Return up to limit recent materializations for the asset at asset_key_path.

    Errors return {'error': message} instead of raising.
    """
    url = resolve_dagster_url()
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


async def materialize_asset(
    asset_key_path: str,
    payload: MaterializeAssetRequest,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request materialization for a Dagster asset."""
    if not asset_key_path:
        return {"error": "Asset key is required"}

    details = await get_asset_details(asset_key_path)
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
            dagster_url=resolve_dagster_url(),
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


async def list_partitions(
    asset_key_path: str,
) -> list[DagsterPartitionStatus] | dict[str, str]:
    """List partition keys for an asset."""
    try:
        from phlo_dagster.operations import list_partitions as list_dagster_partitions

        partitions = await list_dagster_partitions(
            dagster_url=resolve_dagster_url(),
            asset_key_path=asset_key_path,
        )
        return [DagsterPartitionStatus(**partition) for partition in partitions]
    except Exception as exc:
        return {"error": str(exc)}


async def get_asset_details(asset_key_path: str) -> AssetDetails | dict[str, str]:
    """Describe a single asset, including schema, column lineage, and metadata.

    Schema and lineage come from the latest materialization's metadata when
    present, falling back to the asset definition. Errors return
    {'error': message} instead of raising.
    """
    if not asset_key_path:
        return {"error": "Asset key is required"}

    url = resolve_dagster_url()
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


async def get_run_status(
    run_id: str,
) -> DagsterRunStatus | dict[str, str]:
    """Get current status for a Dagster run."""
    url = resolve_dagster_url()

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
) -> list[dict[str, Any]]:
    """Get recent Dagster runs for provider-neutral adapters."""
    url = resolve_dagster_url()

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


async def retry_run(
    run_id: str,
    payload: RetryRunRequest,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request retry for a Dagster run."""
    status = await get_run_status(run_id)
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
            dagster_url=resolve_dagster_url(),
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


async def cancel_run(
    run_id: str,
    payload: CancelRunRequest,
) -> DagsterOperationResponse | dict[str, str]:
    """Request cancellation for a Dagster run."""
    from phlo_dagster.operations import terminate

    result = await terminate(
        dagster_url=resolve_dagster_url(),
        run_id=run_id,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
    )
    return DagsterOperationResponse(**result.to_dict())


async def backfill_asset(
    asset_key_path: str,
    payload: BackfillAssetRequest,
) -> DagsterOperationResponse | dict[str, str]:
    """Validate or request a partition backfill for one asset."""
    partition_keys = _backfill_partition_keys(payload)
    if not partition_keys and not payload.all_partitions:
        return DagsterOperationResponse(
            operation="backfill_asset",
            dry_run=payload.dry_run,
            accepted=False,
            asset_key_path=asset_key_path,
            status="MISSING_PARTITIONS",
            message="Backfill requires explicit partitions or native all_partitions selection.",
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
            details={
                "partitions": partition_keys,
                "partition_count": None if payload.all_partitions else len(partition_keys),
                "all_partitions": payload.all_partitions,
            },
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
        dagster_url=resolve_dagster_url(),
        asset_key_path=asset_key_path,
        partition_set_name=payload.partition_set_name,
        partition_keys=partition_keys,
        all_partitions=payload.all_partitions,
        job_name=payload.job_name,
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
