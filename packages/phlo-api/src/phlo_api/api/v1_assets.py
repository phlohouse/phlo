"""Environment-scoped asset and overview read models for /api/v1."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from phlo.plugins.observatory_settings import (
    OperationalSettings,
    get_operational_settings,
    StorageUnavailableError,
)
from phlo_api.api.v1 import _run_on_ref, _target
from phlo_api.api.v1_query import QuerySessionView, start_exact_asset_count
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.asset_preview_filters import preview_filters, preview_where
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
    AssetAuditProposal,
    AssetAuditProposalRequest,
    AssetAuditTestResult,
    audit_schema_digest,
    create_audit_proposal,
    evaluate_audit_rules,
    get_audit_proposal,
    generate_check_file,
)
from phlo_api.api.v1_git_review import (
    AssetAuditDraftPullRequest,
    AssetAuditPublishRequest,
    GitReviewConflict,
    GitReviewUnavailable,
    project_git_review_client,
    project_git_review_config,
    publish_project_draft_pr,
)
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation
from phlo_api.usage import QueryUsagePage, read_query_usage
from phlo_api.v1_contract import Environment, EnvironmentTarget, WireModel

router = APIRouter(tags=["v1 assets"])
Limit = Annotated[int, Query(ge=1, le=500)]
CheckLimit = Annotated[int, Query(ge=1, le=100)]
_CHECK_DEFINITION_LIMIT = 100
_CHECK_EXECUTION_SCAN_LIMIT = 101
_OVERVIEW_CHECK_ASSET_LIMIT = 50
_OVERVIEW_CHECK_LIMIT = 100
_CHECK_INVENTORY_METADATA_KEY = "phlo/asset-check-inventory"
_CHECK_INVENTORY_VERSION = 1
_CHECK_INVENTORY_MAX_BYTES = 16_384
_EXACT_COUNT_SNAPSHOT_LOOKUP_TIMEOUT_SECONDS = 5

ASSET_QUERY = """query V1Assets {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        name
        location { name }
        assetNodes {
          id assetKey { path } description computeKind kinds groupName isMaterializable isObservable isPartitioned
          repository { name location { name } }
          hasAssetChecks
          tags { key value }
          jobNames
          dependencyKeys { path }
          internalFreshnessPolicy { __typename ... on TimeWindowFreshnessPolicy { failWindowSeconds } }
          metadataEntries {
            __typename label
            ... on TextMetadataEntry { text }
            ... on JsonMetadataEntry { jsonString }
            ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
            ... on TableColumnLineageMetadataEntry {
              lineage { columnName columnDeps { assetKey { path } columnName } }
            }
          }
          assetMaterializations(limit: 100) {
            timestamp runId partition
            runOrError {
              __typename
              ... on Run { runId status pipelineName tags { key value } repositoryOrigin { repositoryLocationName } }
            }
            metadataEntries {
              label
              ... on IntMetadataEntry { intValue intRepr }
              ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
              ... on TableColumnLineageMetadataEntry {
                lineage { columnName columnDeps { assetKey { path } columnName } }
              }
            }
          }
        }
      }
    }
  }
}"""
ASSET_DETAIL_QUERY = """query V1AssetDetail($assetKey: AssetKeyInput!) {
  assetNodeOrError(assetKey: $assetKey) {
    __typename
    ... on AssetNode {
      id assetKey { path } description computeKind kinds groupName isMaterializable isObservable isPartitioned
      repository { name location { name } }
      jobNames
      dependencyKeys { path }
      internalFreshnessPolicy { __typename ... on TimeWindowFreshnessPolicy { failWindowSeconds } }
      metadataEntries {
        __typename label description
        ... on TextMetadataEntry { text }
        ... on JsonMetadataEntry { jsonString }
        ... on TableSchemaMetadataEntry { schema { columns { name type description } } }
        ... on TableColumnLineageMetadataEntry {
          lineage { columnName columnDeps { assetKey { path } columnName } }
        }
      }
      assetMaterializations(limit: 1) {
        timestamp runId partition
        runOrError {
          __typename
          ... on Run { runId status pipelineName tags { key value } repositoryOrigin { repositoryLocationName } }
        }
        metadataEntries {
          label
          ... on IntMetadataEntry { intValue intRepr }
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
          __typename runId status creationTime startTime endTime pipelineName
          repositoryOrigin { repositoryName repositoryLocationName }
          tags { key value }
          assetSelection { path }
        }
      }
      cursor hasMore
    }
    ... on PythonError { message }
  }
}"""
ASSET_CHECKS_QUERY = """query V1AssetChecks($repositorySelector: RepositorySelector!, $limit: Int!, $includeLegacy: Boolean!) {
  repositoriesOrError(repositorySelector: $repositorySelector) {
    __typename
    ... on RepositoryConnection {
      nodes {
        name
        location { name }
        assetNodes {
          assetKey { path }
          repository { name location { name } }
          metadataEntries {
            label
            ... on JsonMetadataEntry { jsonString }
          }
          assetChecksOrError(limit: $limit) @include(if: $includeLegacy) {
            __typename
            ... on AssetChecks { checks { name description } }
            ... on AssetCheckNeedsMigrationError { message }
          }
        }
      }
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
    run { runId tags { key value } repositoryOrigin { repositoryLocationName repositoryName } }
  }
}"""


class AssetWriteCount(WireModel):
    run_id: str
    job_id: str | None = None
    timestamp: datetime
    rows_inserted: int | None = Field(default=None, ge=0)
    rows_deleted: int | None = Field(default=None, ge=0)


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
    freshness_observed_at: datetime | None = None
    freshness_source: Literal["dagster_materialization", "iceberg_snapshot"] | None = None
    freshness_status: Literal["fresh", "stale", "unknown"] = "unknown"
    freshness_reason: (
        Literal[
            "missing_sla",
            "no_observation",
            "future_observation",
            "invalid_observation",
            "catalog_unavailable",
            "ambiguous_observation",
        ]
        | None
    ) = None
    relation: str | None = None
    history_scoped: bool = True
    reports: list[str] = Field(default_factory=list)
    owner: str | None = None
    source_name: str | None = None
    schema_contract: str | None = None
    row_count: int | None = Field(default=None, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    table_metadata_error: str | None = None
    materializations: list[AssetWriteCount] = Field(default_factory=list)
    materialization_history_truncated: bool = False
    layer: Literal["bronze", "silver", "gold"] | None = None
    freshness_sla_seconds: float | None = Field(default=None, gt=0)
    repository_name: str | None = Field(default=None, exclude=True)
    job_names: list[str] = Field(default_factory=list, exclude=True)
    is_ingestion: bool = Field(default=False, exclude=True)
    check_definition_scope: Literal["unique", "ambiguous", "unknown"] = Field(
        default="unknown", exclude=True
    )


class AssetPage(WireModel):
    env: Environment
    items: list[AssetView]
    next_cursor: str | None


class AssetDetail(AssetView):
    columns: list["AssetColumn"]
    schema_observed_at: datetime | None
    schema_source: Literal["materialization", "definition", "catalog", "unavailable"] = (
        "unavailable"
    )
    current_snapshot_id: str | None = None
    sort_order: list[str] | None = None
    column_lineage: dict[str, list["ColumnLineageDependency"]] | None = None
    downstream: list[AssetView] = Field(default_factory=list)


class AssetExactCount(WireModel):
    env: Environment
    nessie_ref: str
    source: Literal["trino_count_star"]
    observed_at: datetime
    snapshot_id: str
    query: QuerySessionView


class AssetColumn(WireModel):
    name: str
    type: str | None
    description: str | None
    nullable: bool | None = None


class ColumnLineageDependency(WireModel):
    asset_key: list[str]
    column_name: str


class AssetRun(WireModel):
    run_id: str
    job_id: str
    status: str
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    duration_seconds: float | None = Field(ge=0)
    selected_assets: list[list[str]] = Field(default_factory=list)


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


class CheckHistoryEvidence(WireModel):
    status: Literal["complete", "partial"]
    unverifiable_checks: list[str]


class AssetChecks(WireModel):
    env: Environment
    asset_id: str
    definitions: list[CheckDefinition]
    executions: list[CheckExecution]
    history: CheckHistoryEvidence


class IcebergSnapshot(WireModel):
    snapshot_id: int
    timestamp_ms: int
    operation: str | None
    summary: dict[str, str]
    parent_id: int | None = None
    schema_id: int | None = None
    author: str | None = None
    sequence_number: int | None = None
    manifest_list: str | None = None


class TableHistory(WireModel):
    env: Environment
    table_name: str
    nessie_ref: str
    items: list[IcebergSnapshot]
    current_snapshot_id: int | None = None
    metadata_location: str | None = None


class SnapshotRollback(WireModel):
    snapshot_id: str = Field(pattern=r"^[0-9]{1,19}$")
    expected_metadata_location: str = Field(min_length=1, max_length=4096)
    nessie_ref: str = Field(min_length=1, max_length=128)
    confirmed: Literal[True]
    idempotency_key: str = Field(min_length=1, max_length=128)


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
    plan_hash: str | None = None
    nessie_ref: str | None = None
    ref_hash: str | None = None
    job_name: str | None = None
    job_selection: Literal["automatic", "explicit"] | None = None
    job_snapshot_id: str | None = None
    selected_assets: list[str] = Field(default_factory=list)
    partition_keys: list[str] = Field(default_factory=list)
    estimated_rows: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    estimated_bytes: int | None = Field(default=None, ge=0)
    estimated_duration_seconds: float | None = Field(default=None, ge=0)
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
    sql: str


class PreviewAccess(WireModel):
    observed_at: AwareDatetime
    returned_row_count: int = Field(ge=0)
    has_more: bool


class AssetUsage(WireModel):
    env: Environment
    asset_id: str
    nessie_ref: str
    status: Literal["partial", "unavailable"]
    source: Literal["api_preview"] = "api_preview"
    reason: Literal["no_retained_preview_evidence"] | None = None
    items: list[PreviewAccess]
    next_cursor: str | None


class MaterializeAssetAction(WireModel):
    job_name: str | None = Field(default=None, min_length=1)
    partition_key: str | None = None
    dry_run: bool = True
    idempotency_key: str = Field(min_length=1, max_length=128)
    run_config: dict[str, Any] | None = None
    mode: Literal["latest", "backfill", "full"] | None = None
    from_time: str | None = None
    to_time: str | None = None
    write_ref: str | None = Field(default=None, min_length=1, max_length=128)
    rebuild_downstream: bool = False
    plan_hash: str | None = None
    confirmed: bool = False


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
    layer: Literal["bronze", "silver", "gold"] | None = None
    asset_count: int = Field(ge=0)
    materialized_asset_count: int = Field(ge=0)
    latest_materialization_at: datetime | None
    freshness_counts: FreshnessCounts | None = None


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


def _asset_run_view(
    row: Any, asset: AssetView, repository_location: str, ref: str
) -> AssetRun | None:
    if not isinstance(row, dict) or row.get("__typename") != "Run":
        raise ValueError
    repository = row.get("repositoryOrigin")
    location = repository.get("repositoryLocationName") if isinstance(repository, dict) else None
    repository_name = repository.get("repositoryName") if isinstance(repository, dict) else None
    if not isinstance(location, str) or not isinstance(repository_name, str):
        raise ValueError
    if location != repository_location or repository_name != asset.repository_name:
        return None
    if not _run_on_ref(row, ref, required=True):
        return None
    selection = row.get("assetSelection")
    if selection is None:
        if not asset.job_names or row.get("pipelineName") not in asset.job_names:
            return None
    else:
        if not isinstance(selection, list):
            raise ValueError
        if not any(_key_path(key) == asset.key for key in selection):
            return None
    status = row.get("status")
    run_id = row.get("runId")
    job_id = row.get("pipelineName")
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
        or not isinstance(job_id, str)
        or not job_id
    ):
        raise ValueError
    created_at = datetime.fromtimestamp(float(row["creationTime"]), UTC)
    started_at = (
        datetime.fromtimestamp(float(row["startTime"]), UTC)
        if row.get("startTime") is not None
        else None
    )
    ended_at = (
        datetime.fromtimestamp(float(row["endTime"]), UTC)
        if row.get("endTime") is not None
        else None
    )
    duration_seconds = (
        (ended_at - started_at).total_seconds()
        if started_at is not None and ended_at is not None
        else None
    )
    if duration_seconds is not None and duration_seconds < 0:
        raise ValueError
    selected_assets = [_key_path(key) for key in selection] if selection is not None else []
    return AssetRun(
        run_id=run_id,
        job_id=job_id,
        status=status,
        created_at=created_at,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        selected_assets=selected_assets,
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


def _declared_relation(entries: Any) -> str | None:
    if not isinstance(entries, list):
        raise BadGatewayError("Dagster returned invalid asset relation metadata.")
    values = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("label") in {"phlo/relation", "target_table"}
    ]
    if not values:
        return None
    if (
        len(values) > 2
        or len({value["label"] for value in values}) != len(values)
        or any(
            not isinstance(value.get("text"), str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value["text"])
            for value in values
        )
        or len({value["text"] for value in values}) != 1
    ):
        raise BackendUnavailableError("Asset has no valid declared physical relation.")
    return values[0]["text"]


def _declared_asset_policy(
    node: dict[str, Any],
) -> tuple[Literal["bronze", "silver", "gold"] | None, float | None]:
    """Read emitted DSL evidence, without guessing layers from model names."""
    entries = node.get("metadataEntries") or []
    layer_values = [
        entry.get("text")
        for entry in entries
        if isinstance(entry, dict) and entry.get("label") == "phlo/layer"
    ]
    if len(layer_values) > 1 or any(
        value not in {"bronze", "silver", "gold"} for value in layer_values
    ):
        raise BadGatewayError("Asset has invalid declared layer metadata.")
    layer: Literal["bronze", "silver", "gold"] | None = layer_values[0] if layer_values else None
    if layer is None:
        key = _key_path(node.get("assetKey"))
        candidates: set[Literal["bronze", "silver", "gold"]] = {
            value
            for value in ("bronze", "silver", "gold")
            if value in (node.get("groupName"), key[0])
        }
        layer = next(iter(candidates)) if len(candidates) == 1 else None
    slas = [entry for entry in entries if isinstance(entry, dict) and entry.get("label") == "sla"]
    if len(slas) > 1:
        raise BadGatewayError("Asset has ambiguous SLA metadata.")
    seconds = None
    if slas:
        try:
            raw = slas[0].get("jsonString")
            sla = json.loads(raw) if isinstance(raw, str) else None
            hours = sla.get("freshness_hours") if isinstance(sla, dict) else None
            if hours is not None:
                if type(hours) not in {int, float} or not math.isfinite(hours) or hours <= 0:
                    raise ValueError
                seconds = float(hours * 3600)
        except (TypeError, ValueError) as exc:
            raise BadGatewayError("Asset has invalid declared freshness SLA.") from exc
    policy = node.get("internalFreshnessPolicy")
    if (
        seconds is None
        and isinstance(policy, dict)
        and policy.get("__typename") == "TimeWindowFreshnessPolicy"
    ):
        seconds = policy.get("failWindowSeconds")
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds <= 0:
            raise BadGatewayError("Asset has invalid freshness policy.")
    return layer, float(seconds) if seconds is not None else None


def _scoped_materialization(node: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Find the newest verified event within the bounded recent event window."""
    location = _repository_location(node)
    materials = node.get("assetMaterializations")
    if not isinstance(materials, list):
        raise BadGatewayError("Dagster returned invalid asset evidence.")
    for event in materials:
        run = event.get("runOrError") if isinstance(event, dict) else None
        origin = run.get("repositoryOrigin") if isinstance(run, dict) else None
        if (
            node.get("isPartitioned") is False
            and isinstance(event, dict)
            and "partition" in event
            and event["partition"] is None
            and isinstance(run, dict)
            and run.get("__typename") == "Run"
            and run.get("status") == "SUCCESS"
            and isinstance(event.get("runId"), str)
            and event["runId"]
            and run.get("runId") == event["runId"]
            and isinstance(origin, dict)
            and origin.get("repositoryLocationName") == location
            and _run_on_ref(run, ref)
        ):
            return event
    return None


def _declared_text(entries: list[Any], label: str) -> str | None:
    matches = [
        entry for entry in entries if isinstance(entry, dict) and entry.get("label") == label
    ]
    if len(matches) > 1:
        raise BadGatewayError(f"Asset has invalid {label} metadata.")
    if not matches:
        return None
    entry = matches[0]
    if entry.get("__typename") == "NullMetadataEntry" or entry.get("jsonString") == "null":
        return None
    value = entry.get("text")
    if not isinstance(value, str) or not value.strip():
        raise BadGatewayError(f"Asset has invalid {label} metadata.")
    return value


def _compute_kind(node: dict[str, Any]) -> str | None:
    kinds = node.get("kinds") or []
    if not isinstance(kinds, list) or any(not isinstance(kind, str) or not kind for kind in kinds):
        raise BadGatewayError("Asset has invalid compute kinds.")
    return node.get("computeKind") or " · ".join(sorted(kinds)) or None


def _asset_view(node: dict[str, Any], ref: str) -> AssetView:
    materializable = node.get("isMaterializable")
    if type(materializable) is not bool:
        raise BadGatewayError("Dagster returned invalid asset materializability evidence.")
    dependencies = node.get("dependencyKeys")
    if not isinstance(dependencies, list):
        raise BadGatewayError("Dagster returned invalid asset evidence.")
    latest = _scoped_materialization(node, ref)
    try:
        observed = (
            datetime.fromtimestamp(float(latest["timestamp"]) / 1000, UTC)
            if latest is not None
            else None
        )
        run_id = latest["runId"] if latest is not None else None
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise BadGatewayError("Dagster returned invalid materialization evidence.") from exc
    key = _key_path(node.get("assetKey"))
    job_names = node.get("jobNames") or []
    if not isinstance(job_names, list) or any(not isinstance(name, str) for name in job_names):
        raise BadGatewayError("Dagster returned invalid asset job membership.")
    materializations = node.get("assetMaterializations") or []
    if not isinstance(materializations, list):
        raise BadGatewayError("Dagster returned invalid materialization history.")
    recent_writes = [
        item
        for item in _asset_write_counts(node, ref)
        if item.timestamp >= datetime.now(UTC) - timedelta(days=7)
    ]
    layer, freshness_sla = _declared_asset_policy(node)
    reports: list[str] = []
    for entry in node.get("metadataEntries") or []:
        if entry.get("label") == "phlo/reports":
            try:
                declared = json.loads(entry.get("jsonString", entry.get("text")))
                if not isinstance(declared, list) or any(
                    not isinstance(name, str) or not name.strip() for name in declared
                ):
                    raise ValueError
                reports.extend(declared)
            except (KeyError, TypeError, ValueError) as exc:
                raise BadGatewayError("Invalid declared report metadata.") from exc
    return AssetView(
        id="/".join(key),
        key=key,
        description=node.get("description"),
        compute_kind=_compute_kind(node),
        group_name=node.get("groupName"),
        layer=layer,
        freshness_sla_seconds=freshness_sla,
        is_source=not materializable,
        dependencies=[_key_path(item) for item in dependencies],
        relation=_declared_relation(node.get("metadataEntries") or []),
        last_materialization_at=observed,
        last_run_id=run_id,
        history_scoped=latest is not None,
        freshness_observed_at=observed,
        freshness_source="dagster_materialization" if observed is not None else None,
        reports=reports,
        owner=_declared_text(node.get("metadataEntries") or [], "owner"),
        source_name=_declared_text(node.get("metadataEntries") or [], "source_name"),
        schema_contract=_declared_text(node.get("metadataEntries") or [], "schema_ref"),
        materializations=recent_writes,
        materialization_history_truncated=len(materializations) >= 100,
        repository_name=node["repository"].get("name"),
        job_names=job_names,
        is_ingestion=any(
            tag.get("key") == "asset_type" and tag.get("value") == "ingestion"
            for tag in node.get("tags") or []
        ),
        check_definition_scope=node.get("_phlo_check_definition_scope", "unknown"),
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


def _iceberg_history(table_name: str, ref: str, limit: int) -> dict[str, Any]:
    from phlo_iceberg.catalog import get_catalog

    table = get_catalog(ref=ref).load_table(table_name)
    snapshots = sorted(table.snapshots(), key=lambda snapshot: snapshot.timestamp_ms, reverse=True)
    items = [
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
            "schema_id": snapshot.schema_id,
            "sequence_number": snapshot.sequence_number,
            "manifest_list": snapshot.manifest_list,
            "author": snapshot.summary.additional_properties.get("author")
            if snapshot.summary
            else None,
        }
        for snapshot in snapshots[:limit]
    ]
    return {
        "items": items,
        "current_snapshot_id": table.metadata.current_snapshot_id,
        "metadata_location": table.metadata_location,
    }


def _iceberg_current_snapshot_id(table_name: str, ref: str) -> str | None:
    from phlo_iceberg.catalog import get_catalog

    snapshot = get_catalog(ref=ref).load_table(table_name).current_snapshot()
    snapshot_id = snapshot.snapshot_id if snapshot is not None else None
    return str(snapshot_id) if snapshot_id is not None else None


def _nonnegative_integer(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise BadGatewayError("Asset has invalid row-count or size metadata.")


def _iceberg_asset_metadata(table_name: str, ref: str) -> dict[str, Any]:
    """Read current table evidence from the selected ref without scanning data files."""
    from phlo_iceberg.catalog import get_catalog

    table = get_catalog(ref=ref).load_table(table_name)
    snapshot = table.current_snapshot()
    snapshot_summary = getattr(snapshot, "summary", None)
    summary = snapshot_summary.additional_properties if snapshot_summary is not None else {}
    raw_snapshot_id = getattr(snapshot, "snapshot_id", None)
    snapshot_id = str(raw_snapshot_id) if type(raw_snapshot_id) is int else None
    freshness_observed_at = None
    freshness_reason = "no_observation" if snapshot is None else None
    if snapshot is not None:
        timestamp_ms = getattr(snapshot, "timestamp_ms", None)
        if type(timestamp_ms) is int:
            try:
                freshness_observed_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
                    milliseconds=timestamp_ms
                )
            except OverflowError:
                freshness_reason = "invalid_observation"
        else:
            freshness_reason = "invalid_observation"
        if snapshot_id is None:
            freshness_reason = "invalid_observation"
            freshness_observed_at = None
    row_count = None
    if (
        summary.get("total-position-deletes") == "0"
        and summary.get("total-equality-deletes") == "0"
    ):
        row_count = _nonnegative_integer(summary.get("total-records"))
    return {
        "columns": [
            {
                "name": field.name,
                "type": str(field.field_type),
                "description": field.doc,
                "nullable": not field.required,
            }
            for field in table.schema().fields
        ],
        "row_count": row_count,
        "size_bytes": _nonnegative_integer(summary.get("total-files-size")),
        "sort_order": [str(field) for field in table.sort_order().fields],
        "snapshot_id": snapshot_id,
        "freshness_observed_at": freshness_observed_at,
        "freshness_reason": freshness_reason,
    }


def _table_stats_reason(metadata: dict[str, Any]) -> str | None:
    reasons = []
    if metadata.get("row_count") is None:
        reasons.append("Exact row count is not provable from the current snapshot metadata.")
    if metadata.get("size_bytes") is None:
        reasons.append("Table size is unavailable from the current snapshot metadata.")
    return " ".join(reasons) or None


def _asset_write_counts(node: dict[str, Any], ref: str) -> list[AssetWriteCount]:
    counts = []
    for event in node.get("assetMaterializations") or []:
        if _scoped_materialization({**node, "assetMaterializations": [event]}, ref) is None:
            continue
        run = event.get("runOrError") if isinstance(event, dict) else None
        job_id = None
        if (
            isinstance(run, dict)
            and run.get("__typename") == "Run"
            and run.get("runId") == event.get("runId")
            and isinstance(run.get("pipelineName"), str)
            and run["pipelineName"].strip()
        ):
            job_id = run["pipelineName"]
        entries = event.get("metadataEntries") or []
        metrics = {}
        for label in ("rows_inserted", "rows_deleted"):
            values = [
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("label") == label
            ]
            if len(values) > 1:
                raise BadGatewayError("Asset has ambiguous write-count metadata.")
            entry = values[0] if values else {}
            metrics[label] = _nonnegative_integer(
                entry.get("intValue") if entry.get("intValue") is not None else entry.get("intRepr")
            )
        if any(value is not None for value in metrics.values()):
            counts.append(
                AssetWriteCount(
                    run_id=event["runId"],
                    job_id=job_id,
                    timestamp=datetime.fromtimestamp(float(event["timestamp"]) / 1000, UTC),
                    **metrics,
                )
            )
    return counts


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


def _check_definition_scope(
    selected: dict[str, Any], repositories: list[dict[str, Any]]
) -> Literal["unique", "ambiguous", "unknown"]:
    key = _key_path(selected.get("assetKey"))
    identity = (selected["repository"].get("name"), _repository_location(selected))
    if any(not isinstance(value, str) or not value for value in identity):
        return "unknown"
    for repository in repositories:
        location = repository.get("location")
        if not isinstance(location, dict) or not isinstance(repository.get("name"), str):
            return "unknown"
        origin = (repository["name"], location.get("name"))
        if not all(isinstance(value, str) and value for value in origin):
            return "unknown"
        for node in repository["assetNodes"]:
            if _key_path(node.get("assetKey")) != key:
                continue
            declared = node.get("repository")
            if (
                not isinstance(declared, dict)
                or declared.get("name") != origin[0]
                or _repository_location(node) != origin[1]
                or type(node.get("hasAssetChecks")) is not bool
            ):
                return "unknown"
            if node["hasAssetChecks"] and origin != identity:
                return "ambiguous"
    return "unique"


async def _asset_nodes(
    request: Request,
    env: Environment,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> list[dict[str, Any]]:
    """Read repository-bound definitions, never globally preferred duplicate keys."""
    target = _target(request, env, allowed_query=allowed_query)
    location = target.dagster_location
    result = await _graphql(ASSET_QUERY)
    data = result.get("data") if isinstance(result, dict) else None
    repositories = data.get("repositoriesOrError") if isinstance(data, dict) else None
    repositories_nodes = (
        repositories.get("nodes")
        if isinstance(repositories, dict)
        and repositories.get("__typename") == "RepositoryConnection"
        else None
    )
    if result.get("errors") or not isinstance(repositories_nodes, list):
        raise BadGatewayError("Dagster returned an invalid asset inventory.")
    if not any(
        isinstance(repository, dict)
        and isinstance(repository.get("location"), dict)
        and repository["location"].get("name") == location
        for repository in repositories_nodes
    ):
        raise BackendUnavailableError("The selected Dagster code location is unavailable.")
    raw_nodes = [
        asset
        for repository in repositories_nodes
        if isinstance(repository, dict) and isinstance(repository.get("assetNodes"), list)
        for asset in repository["assetNodes"]
    ]
    if any(
        not isinstance(repository, dict) or not isinstance(repository.get("assetNodes"), list)
        for repository in repositories_nodes
    ) or any(not isinstance(node, dict) for node in raw_nodes):
        raise BadGatewayError("Dagster returned an invalid asset inventory.")
    selected = []
    for repository in repositories_nodes:
        repository_location = repository.get("location")
        if not isinstance(repository_location, dict) or repository_location.get("name") != location:
            continue
        for node in repository["assetNodes"]:
            if _repository_location(node) != location:
                raise BadGatewayError("Dagster asset definition does not match its repository.")
            selected.append(
                {
                    **node,
                    "_phlo_check_definition_scope": _check_definition_scope(
                        node, repositories_nodes
                    ),
                }
            )
    keys = [tuple(_key_path(node.get("assetKey"))) for node in selected]
    if len(keys) != len(set(keys)):
        raise BackendUnavailableError("Asset keys are ambiguous within the selected code location.")
    return selected


async def _assets(
    request: Request,
    env: Environment,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> list[AssetView]:
    target = _target(request, env, allowed_query=allowed_query)
    nodes = await _asset_nodes(request, env, allowed_query=allowed_query)
    return sorted(
        (_asset_view(node, target.nessie_ref) for node in nodes), key=lambda asset: asset.id
    )


def _page(
    env: Environment, kind: str, items: list[AssetView], limit: int, cursor: str | None
) -> AssetPage:
    offset = _offset(cursor, env, kind)
    selected = items[offset : offset + limit]
    next_cursor = _cursor(env, kind, offset + limit) if offset + limit < len(items) else None
    return AssetPage(env=env, items=selected, next_cursor=next_cursor)


_ASSET_PAGE_STATS_TIMEOUT_SECONDS = 5


async def _asset_page_with_table_stats(page: AssetPage, ref: str) -> AssetPage:
    """Enrich only the returned page using the selected Nessie ref."""
    semaphore = asyncio.Semaphore(8)

    async def enrich(asset: AssetView) -> AssetView:
        if asset.relation is None:
            return asset.model_copy(
                update={
                    "table_metadata_error": "No declared table relation is available.",
                    "freshness_reason": "no_observation"
                    if asset.freshness_source is None
                    else asset.freshness_reason,
                }
            )
        try:
            async with semaphore:
                metadata = await asyncio.wait_for(
                    asyncio.to_thread(_iceberg_asset_metadata, _table_name(asset.relation), ref),
                    timeout=5,
                )
        except Exception:
            return asset.model_copy(
                update={
                    "table_metadata_error": "Ref-scoped table statistics are unavailable. Check the catalog connection.",
                    "freshness_reason": "catalog_unavailable"
                    if asset.freshness_source is None
                    else asset.freshness_reason,
                }
            )
        observed = metadata.get("freshness_observed_at")
        use_snapshot = asset.freshness_source is None
        return asset.model_copy(
            update={
                "row_count": metadata.get("row_count"),
                "size_bytes": metadata.get("size_bytes"),
                "table_metadata_error": _table_stats_reason(metadata),
                "freshness_observed_at": observed if use_snapshot else asset.freshness_observed_at,
                "freshness_source": "iceberg_snapshot"
                if use_snapshot and observed is not None
                else asset.freshness_source,
                "freshness_reason": metadata.get("freshness_reason")
                if use_snapshot and observed is None
                else asset.freshness_reason,
            }
        )

    try:
        items = await asyncio.wait_for(
            asyncio.gather(*(enrich(item) for item in page.items)),
            timeout=_ASSET_PAGE_STATS_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        items = [
            item.model_copy(
                update={
                    "row_count": None,
                    "size_bytes": None,
                    "table_metadata_error": "Ref-scoped table statistics exceeded the page read time budget.",
                    "freshness_reason": "catalog_unavailable"
                    if item.freshness_source is None
                    else item.freshness_reason,
                }
            )
            for item in page.items
        ]
    return page.model_copy(update={"items": items})


_AGGREGATE_FRESHNESS_METADATA_LIMIT = 100


async def _assets_with_freshness_evidence(
    assets: list[AssetView], env: Environment, ref: str
) -> list[AssetView]:
    """Read selected-ref snapshots within the normal first-page metadata budget."""
    selected = assets[:_AGGREGATE_FRESHNESS_METADATA_LIMIT]
    page = await _asset_page_with_table_stats(
        AssetPage(env=env, items=selected, next_cursor=None), ref
    )
    return page.items + [
        asset.model_copy(
            update={
                "freshness_reason": "catalog_unavailable"
                if asset.freshness_source is None
                else asset.freshness_reason
            }
        )
        for asset in assets[len(selected) :]
    ]


@router.get("/tables/{table_name}/snapshots", response_model=TableHistory)
async def v1_table_snapshots(
    request: Request, table_name: str, env: Environment = Query(), limit: Limit = 100
) -> TableHistory:
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    name = _table_name(table_name)
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_iceberg_history, name, target.nessie_ref, limit), timeout=5
        )
        history = TableHistory(env=env, table_name=name, nessie_ref=target.nessie_ref, **raw)
    except TimeoutError as exc:
        raise BackendUnavailableError("Iceberg history exceeded the read time budget.") from exc
    except Exception as exc:
        raise BackendUnavailableError("Ref-scoped Iceberg history is unavailable.") from exc
    return history


@router.post("/tables/{table_name}/rollback")
async def v1_table_rollback(
    request: Request, table_name: str, payload: SnapshotRollback, env: Environment = Query()
) -> dict[str, Any]:
    from phlo.compliance.signatures.step_up import RecentMfaClaimsChallenge
    from phlo_api.api.authentication import authenticate_request
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_iceberg.tables import (
        StaleTableRevision,
        _require_direct_write,
        rollback_table_to_snapshot,
    )
    from pyiceberg.exceptions import CommitFailedException

    target = _target(request, env)
    name = _table_name(table_name)
    if payload.nessie_ref != target.nessie_ref:
        raise HTTPException(
            status_code=409, detail="Environment reference changed; refresh history."
        )
    authentication = authenticate_request(request)
    if not authentication.authenticated or authentication.session is None:
        raise HTTPException(status_code=401, detail="Verified authentication session is required.")
    if not RecentMfaClaimsChallenge().challenge(authentication.session).success:
        raise HTTPException(status_code=403, detail="Recent verified MFA is required for rollback.")
    auth = require_scope(request, "lakehouse:operate")
    enforce_rate_limit(auth["subject"], "snapshot_rollback")
    intent = payload.model_dump(exclude={"idempotency_key"})
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    action_target = f"{auth['subject']}:{env}:{name}@{target.nessie_ref}:rollback:{digest}"
    bound = idempotency_key_target(payload.idempotency_key, "v1_table_rollback")
    if bound is not None and bound != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        try:
            await asyncio.to_thread(_require_direct_write, target.nessie_ref)
            result = await asyncio.to_thread(
                rollback_table_to_snapshot,
                name,
                int(payload.snapshot_id),
                target.nessie_ref,
                expected_metadata_location=payload.expected_metadata_location,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="Direct writes to main are protected; use a branch and signed merge.",
            ) from exc
        except (StaleTableRevision, CommitFailedException) as exc:
            raise HTTPException(
                status_code=409, detail="Table revision changed; refresh history."
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Snapshot is not a current ancestor."
            ) from exc
        except Exception as exc:
            raise BackendUnavailableError(
                "Snapshot rollback failed; inspect history before retrying."
            ) from exc
        return {
            "env": env,
            "table_name": name,
            "nessie_ref": target.nessie_ref,
            "rolled_back_to": str(result["rolled_back_to"]),
        }

    return await replay_or_execute_async(
        idempotency_key=payload.idempotency_key,
        operation="v1_table_rollback",
        target=action_target,
        execute=execute,
        audit=lambda result: audit_operation(
            operation="v1_table_rollback",
            target=action_target,
            dry_run=False,
            auth=auth,
            payload=intent,
            result=result,
        ),
    )


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
    job_name: str | None = None,
    mode: Literal["latest", "backfill", "full"] = "latest",
    from_time: str | None = None,
    to_time: str | None = None,
    write_ref: str | None = None,
    rebuild_downstream: bool = False,
) -> MaterializationEstimate:
    allowed = frozenset(
        {
            "env",
            "partition_count",
            "job_name",
            "mode",
            "from_time",
            "to_time",
            "write_ref",
            "rebuild_downstream",
        }
    )
    _target(request, env, allowed_query=allowed)
    asset_id = asset_id.strip("/")
    if job_name is not None or "partition_count" not in request.query_params:
        plan = await _materialization_plan(
            request,
            env,
            asset_id,
            MaterializeAssetAction(
                job_name=job_name,
                idempotency_key="estimate",
                mode=mode,
                from_time=from_time,
                to_time=to_time,
                write_ref=write_ref,
                rebuild_downstream=rebuild_downstream,
            ),
            allowed_query=allowed,
        )
        return MaterializationEstimate(
            env=env,
            asset_id=asset_id,
            partition_count=len(plan["runs"]),
            plan_hash=_plan_hash(plan),
            nessie_ref=plan["write_ref"],
            ref_hash=plan["ref_hash"],
            job_name=plan["job_name"],
            job_selection="explicit" if job_name else "automatic",
            job_snapshot_id=plan["job_snapshot_id"],
            selected_assets=plan["selected_assets"],
            partition_keys=[run["partition_key"] for run in plan["runs"] if run["partition_key"]],
            workload_status="Run count, partitions and asset selection verified with Dagster. No future workload or cost estimates are supported.",
        )
    assets = await _assets(request, env, allowed_query=allowed)
    if not any(asset.id == asset_id for asset in assets):
        raise NotFoundError("Asset was not found.")
    return MaterializationEstimate(
        env=env,
        asset_id=asset_id,
        partition_count=partition_count,
        workload_status="Caller-supplied run count; no launch plan has been verified and no workload source is configured.",
    )


@router.get("/assets/{asset_id:path}/preview", response_model=AssetPreview)
async def v1_asset_preview(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    filters: Annotated[str, Query(max_length=16384)] = "[]",
) -> AssetPreview:
    allowed = frozenset({"env", "limit", "filters"})
    target = _target(request, env, allowed_query=allowed)
    if len(request.query_params.getlist("filters")) > 1:
        raise HTTPException(status_code=422, detail="Provide one filter list.")
    try:
        predicates = preview_filters.validate_json(filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid preview filters.") from exc
    asset_id = asset_id.strip("/")
    matches = [
        asset
        for asset in await _assets(request, env, allowed_query=allowed)
        if asset.id == asset_id
    ]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].relation:
        raise BackendUnavailableError("Asset has no declared physical relation for preview.")
    where = ""
    if predicates:
        try:
            current_id, schemas = await asyncio.wait_for(
                asyncio.to_thread(_iceberg_schema, matches[0].relation, target.nessie_ref),
                timeout=5,
            )
            columns = {
                field["name"]
                for schema in schemas
                if schema["schema_id"] == current_id
                for field in schema["fields"]
            }
            where = preview_where(predicates, columns)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Invalid preview filter column or value."
            ) from exc
        except Exception as exc:
            raise BackendUnavailableError("Ref-scoped filter schema is unavailable.") from exc
    try:
        catalog = preview_catalog(env, target.nessie_ref)
        relation = quote_table(catalog, matches[0].relation.replace(".", "/"))
        sql = f"SELECT * FROM {relation}{where} LIMIT {limit + 1}"
        result = await execute_preview(
            sql,
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
    try:
        preview = AssetPreview(
            env=env,
            asset_id=asset_id,
            nessie_ref=target.nessie_ref,
            columns=columns,
            rows=result["rows"],
            has_more=result["has_more"],
            sql=sql,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise BadGatewayError("Trino returned invalid preview data.") from exc
    from phlo_api.api.operation_controls import audit_operation

    principal = get_request_principal(request)
    assert principal is not None  # The v1 security manifest authenticated this request.
    try:
        await asyncio.to_thread(
            audit_operation,
            operation="v1_asset_preview",
            target=f"{env}:{asset_id}@{target.nessie_ref}",
            dry_run=False,
            auth={"subject": principal.subject, "scopes": []},
            payload={"env": env, "asset_id": asset_id, "nessie_ref": target.nessie_ref},
            result={"returned_row_count": len(preview.rows), "has_more": preview.has_more},
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BackendUnavailableError("Preview access could not be recorded.") from exc
    return preview


@router.post("/assets/{asset_id:path}/audits", response_model=AssetAuditProposal, status_code=202)
async def v1_asset_audit_proposal(
    request: Request,
    asset_id: str,
    payload: AssetAuditProposalRequest,
    env: Environment = Query(),
) -> AssetAuditProposal:
    """Create an idempotent, reviewable Git patch without activating project code."""
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        project_root,
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
        file_path, source = generate_check_file(
            matches[0].key,
            payload.check_name,
            payload.rules,
            failure_policy=payload.failure_policy,
            table_relation=detail.relation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    schema_digest = audit_schema_digest([(column.name, column.type) for column in detail.columns])
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    action_target = (
        f"{env}:{asset_id}@{target.nessie_ref}:audit-proposal:{source_digest}:{schema_digest}"
    )
    idempotency_store_ready = (
        os.environ.get("PHLO_V1_ACTIONS_SINGLE_REPLICA") == "1"
        and os.environ.get("PHLO_V1_ACTIONS_SINGLE_PROCESS") == "1"
    )
    if not idempotency_store_ready:
        raise BackendUnavailableError(
            "Audit proposal creation requires the verified single-replica idempotency store."
        )
    bound_target = idempotency_key_target(payload.idempotency_key, "v1_create_asset_audit_proposal")
    if bound_target is not None and bound_target != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        try:
            proposal = create_audit_proposal(
                project_root=project_root(),
                env=env,
                asset_id=asset_id,
                nessie_ref=target.nessie_ref,
                check_name=payload.check_name,
                file_path=file_path,
                source=source,
                rules=payload.rules,
                failure_policy=payload.failure_policy,
                schema_digest=schema_digest,
                table_relation=detail.relation,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise BackendUnavailableError("Audit proposal storage is unavailable.") from exc
        return proposal.model_dump(mode="json")

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
                "failure_policy": payload.failure_policy,
                "schema_digest": schema_digest,
            },
            result={
                "proposal_id": outcome.get("proposal_id"),
                "status": outcome.get("status"),
                "file_path": outcome.get("file_path"),
                "source_digest": outcome.get("source_digest"),
            },
        ),
    )
    try:
        return AssetAuditProposal.model_validate_json(json.dumps(outcome))
    except (TypeError, ValueError) as exc:
        raise BackendUnavailableError("Persisted audit proposal is invalid.") from exc


@router.get("/assets/{asset_id:path}/audits/{proposal_id}", response_model=AssetAuditProposal)
async def v1_asset_audit_proposal_detail(
    request: Request,
    asset_id: str,
    proposal_id: str,
    env: Environment = Query(),
) -> AssetAuditProposal:
    """Read a stored, source-only review proposal in its original environment/ref."""
    from phlo_api.api.operation_controls import project_root, require_scope

    require_scope(request, "project:write")
    target = _target(request, env)
    try:
        proposal = get_audit_proposal(project_root(), proposal_id)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise BackendUnavailableError("Audit proposal storage is unavailable.") from exc
    if (
        proposal is None
        or proposal.env != env
        or proposal.asset_id != asset_id.strip("/")
        or proposal.nessie_ref != target.nessie_ref
    ):
        raise NotFoundError("Audit proposal was not found.")
    return proposal


async def _require_current_audit_schema(
    request: Request, proposal: AssetAuditProposal, env: Environment
) -> None:
    if proposal.schema_digest is None:
        raise HTTPException(status_code=409, detail={"error": "audit_proposal_needs_regeneration"})
    detail = await v1_asset_detail(request, proposal.asset_id, env)
    digest = audit_schema_digest([(column.name, column.type) for column in detail.columns])
    if digest != proposal.schema_digest or detail.relation != proposal.table_relation:
        raise HTTPException(status_code=409, detail={"error": "audit_proposal_schema_changed"})


@router.post(
    "/assets/{asset_id:path}/audits/{proposal_id}/test", response_model=AssetAuditTestResult
)
async def v1_asset_audit_proposal_test(
    request: Request,
    asset_id: str,
    proposal_id: str,
    env: Environment = Query(),
) -> AssetAuditTestResult:
    """Execute trusted rules against at most 100 real rows, without installing source."""
    from phlo_api.api.operation_controls import audit_operation, enforce_rate_limit, require_scope

    auth = require_scope(request, "project:write")
    enforce_rate_limit(auth["subject"], "test_asset_audit_proposal")
    proposal = await v1_asset_audit_proposal_detail(request, asset_id, proposal_id, env)
    await _require_current_audit_schema(request, proposal, env)
    if not proposal.rules:
        raise HTTPException(status_code=409, detail={"error": "audit_proposal_needs_regeneration"})
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_asset_preview"], {"env": env, "asset_id": asset_id}
    )
    preview = await v1_asset_preview(request, asset_id, env, limit=100)
    try:
        results = await asyncio.to_thread(
            evaluate_audit_rules,
            proposal.rules,
            preview.rows,
            [column.name for column in preview.columns],
        )
    except (ImportError, TypeError, ValueError, KeyError) as exc:
        raise BackendUnavailableError("The validated audit rule runtime is unavailable.") from exc
    result = AssetAuditTestResult(
        proposal_id=proposal.proposal_id,
        source_digest=proposal.source_digest,
        env=env,
        nessie_ref=preview.nessie_ref,
        executed_at=datetime.now(UTC),
        rows_checked=len(preview.rows),
        sampled=preview.has_more,
        passed=all(item.passed for item in results),
        results=results,
    )
    try:
        await asyncio.to_thread(
            audit_operation,
            operation="v1_test_asset_audit_proposal",
            target=f"{env}:{asset_id}@{preview.nessie_ref}",
            dry_run=True,
            auth=auth,
            payload={"proposal_id": proposal_id, "source_digest": proposal.source_digest},
            result=result.model_dump(mode="json"),
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise BackendUnavailableError("Audit execution evidence could not be recorded.") from exc
    return result


@router.post(
    "/assets/{asset_id:path}/audits/{proposal_id}/pull-request",
    response_model=AssetAuditDraftPullRequest,
    status_code=202,
)
async def v1_asset_audit_proposal_pull_request(
    request: Request,
    asset_id: str,
    proposal_id: str,
    payload: AssetAuditPublishRequest,
    env: Environment = Query(),
) -> AssetAuditDraftPullRequest:
    """Publish the stored check source as a draft PR in this project repository."""
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        project_root,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key

    auth = require_scope(request, "project:write")
    enforce_rate_limit(auth["subject"], "publish_asset_audit_proposal")
    require_idempotency_key(payload.idempotency_key)
    if (
        os.environ.get("PHLO_V1_ACTIONS_SINGLE_REPLICA") != "1"
        or os.environ.get("PHLO_V1_ACTIONS_SINGLE_PROCESS") != "1"
    ):
        raise BackendUnavailableError(
            "Project Git publishing requires the verified single-replica idempotency store."
        )
    target = _target(request, env)
    asset_id = asset_id.strip("/")
    try:
        proposal = get_audit_proposal(project_root(), proposal_id)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise BackendUnavailableError("Audit proposal storage is unavailable.") from exc
    if (
        proposal is None
        or proposal.env != env
        or proposal.asset_id != asset_id
        or proposal.nessie_ref != target.nessie_ref
    ):
        raise NotFoundError("Audit proposal was not found.")
    if (
        payload.expected_source_digest is not None
        and payload.expected_source_digest != proposal.source_digest
    ):
        raise HTTPException(status_code=409, detail={"error": "audit_proposal_source_changed"})
    await _require_current_audit_schema(request, proposal, env)
    try:
        config = project_git_review_config()
    except GitReviewUnavailable as exc:
        raise BackendUnavailableError("Project Git review is not configured.") from exc

    action_target = (
        f"{env}:{asset_id}@{target.nessie_ref}:audit-draft-pr:{proposal.source_digest}:"
        f"{config.repository}:{config.base_branch}"
    )
    bound_target = idempotency_key_target(
        payload.idempotency_key,
        "v1_publish_asset_audit_proposal",
    )
    if bound_target is not None and bound_target != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        try:
            async with project_git_review_client(config) as client:
                result = await publish_project_draft_pr(client, config, proposal)
        except GitReviewConflict as exc:
            raise HTTPException(
                status_code=409, detail={"error": "audit_proposal_branch_conflict"}
            ) from exc
        except (GitReviewUnavailable, httpx.HTTPError) as exc:
            raise BackendUnavailableError("Project Git review is unavailable.") from exc
        return result.model_dump(mode="json")

    try:
        outcome = await replay_or_execute_async(
            idempotency_key=payload.idempotency_key,
            operation="v1_publish_asset_audit_proposal",
            target=action_target,
            execute=execute,
            audit=lambda result: audit_operation(
                operation="v1_publish_asset_audit_proposal",
                target=action_target,
                dry_run=False,
                auth=auth,
                payload={"proposal_id": proposal_id, "source_digest": proposal.source_digest},
                result={
                    "status": result.get("status"),
                    "repository": result.get("repository"),
                    "pull_request_number": result.get("pull_request_number"),
                    "pull_request_url": result.get("pull_request_url"),
                },
            ),
        )
    except ValueError as exc:
        raise BackendUnavailableError(
            "Project Git review operation could not be completed."
        ) from exc
    try:
        return AssetAuditDraftPullRequest.model_validate(outcome)
    except (TypeError, ValueError) as exc:
        raise BackendUnavailableError("Stored project Git review result is invalid.") from exc


async def _action_context(
    request: Request,
    env: Environment,
    asset_id: str,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> tuple[EnvironmentTarget, str, list[str]]:
    if (
        os.environ.get("PHLO_V1_ACTIONS_SINGLE_REPLICA") != "1"
        or os.environ.get("PHLO_V1_ACTIONS_SINGLE_PROCESS") != "1"
        or os.environ.get("PHLO_V1_ACTIONS_REF_TAG_CONTRACT") != "1"
    ):
        raise BackendUnavailableError("Environment-pinned actions are not enabled.")
    target = _target(request, env, allowed_query=allowed_query)
    definitions = [
        node
        for node in await _asset_nodes(request, env, allowed_query=allowed_query)
        if node.get("assetKey", {}).get("path") == asset_id.split("/")
    ]
    if len(definitions) != 1:
        raise NotFoundError("Asset has no unique definition in this environment.")
    node = definitions[0]
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


async def _latest_asset_partition(
    asset_id: str, repository_location: str, repository_name: str
) -> str:
    node = await _scoped_operation_node(asset_id, repository_location, repository_name, limit=1)
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


_OPERATION_NODE_QUERY = """query AssetOperationNode($selector: RepositorySelector!, $limit: Int!) {
  repositoryOrError(repositorySelector: $selector) {
    __typename
    ... on Repository {
      name location { name }
      assetNodes {
        assetKey { path } jobNames isPartitioned hasMaterializePermission kinds opVersion
        repository { name location { name } }
        partitionDefinition { type fmt }
        partitionKeyConnection(limit: $limit, ascending: false) { results cursor hasMore }
      }
    }
  }
}"""
_OPERATION_JOB_QUERY = """query AssetOperationJob($selector: PipelineSelector!) {
  pipelineOrError(params: $selector) {
    __typename
    ... on Pipeline { pipelineSnapshotId hasLaunchExecutionPermission }
  }
}"""
_OPERATION_PARTITION_QUERY = """query AssetOperationPartition($selector: PipelineSelector!, $partition: String!, $assets: [AssetKeyInput!]) {
  pipelineOrError(params: $selector) {
    __typename
    ... on Pipeline {
      partition(partitionName: $partition, selectedAssetKeys: $assets) {
        runConfigOrError { __typename ... on PartitionRunConfig { yaml } }
      }
    }
  }
}"""


async def _scoped_operation_node(
    asset_id: str, repository_location: str, repository_name: str, *, limit: int = 501
) -> dict[str, Any]:
    repository = _response_field(
        await _graphql(
            _OPERATION_NODE_QUERY,
            {
                "selector": {
                    "repositoryLocationName": repository_location,
                    "repositoryName": repository_name,
                },
                "limit": limit,
            },
        ),
        "repositoryOrError",
    )
    if (
        repository.get("__typename") != "Repository"
        or repository.get("name") != repository_name
        or repository.get("location", {}).get("name") != repository_location
    ):
        raise NotFoundError("Asset repository was not found in this environment.")
    nodes = repository.get("assetNodes")
    if not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned invalid action definitions.")
    matches = [
        node for node in nodes if node.get("assetKey", {}).get("path") == asset_id.split("/")
    ]
    if len(matches) != 1:
        raise NotFoundError("Asset has no unique definition in this repository.")
    node = matches[0]
    origin = node.get("repository", {})
    if (
        origin.get("name") != repository_name
        or origin.get("location", {}).get("name") != repository_location
    ):
        raise NotFoundError("Asset was not found in this repository.")
    return node


def _plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _time_range_keys(keys: list[str], fmt: str, start: str | None, end: str | None) -> list[str]:
    """Use the owning partition definition, never invent hourly/daily keys."""
    try:
        first = datetime.fromisoformat(start or "")
        last = datetime.fromisoformat(end or "")
        if first.tzinfo is None or last.tzinfo is None or first >= last:
            raise ValueError("invalid range")
        times = sorted(
            [(key, datetime.strptime(key, fmt).replace(tzinfo=UTC)) for key in keys],
            key=lambda item: item[1],
        )
        boundaries = [time for _, time in times]
        if fmt == "%Y-%m-%d":
            step = timedelta(days=1)
        elif len(boundaries) > 1:
            step = min(b - a for a, b in zip(boundaries, boundaries[1:]))
        else:
            raise ValueError("partition interval unavailable")
        chosen = [key for key, timestamp in times if first <= timestamp < last]
        # Reject a truncated range and partial partition boundaries rather than silently widening it.
        if (
            not chosen
            or first not in boundaries
            or last not in [*boundaries, boundaries[-1] + step]
            or last > datetime.now(UTC)
        ):
            raise ValueError("outside available partition boundaries")
        return chosen
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Use a non-empty UTC range starting at an available partition boundary; the end is exclusive.",
        ) from exc


async def _materialization_nodes(
    request: Request,
    env: Environment,
    asset_id: str,
    payload: MaterializeAssetAction,
    target: EnvironmentTarget,
    repository: str,
    *,
    allowed_query: frozenset[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve bounded downstream selection and authorize every scoped definition."""
    inventory = await _assets(request, env, allowed_query=allowed_query)
    selected = {asset_id}
    if payload.rebuild_downstream:
        while True:
            added = {
                asset.id
                for asset in inventory
                if any("/".join(dep) in selected for dep in asset.dependencies)
            } - selected
            if not added:
                break
            selected.update(added)
            if len(selected) > 50:
                raise HTTPException(
                    status_code=422, detail="Downstream rebuild exceeds the 50-asset action limit."
                )
    nodes: list[dict[str, Any]] = []
    for key in sorted(selected):
        if key != asset_id:
            # The root route authorization does not authorize its descendants.
            await enforce_http_operation(
                request=request,
                spec=HTTP_ROUTE_MANIFEST["v1_asset_materialize"],
                path_params={"asset_id": key},
            )
        node = await _scoped_operation_node(key, target.dagster_location, repository)
        origin = node.get("repository", {})
        if (
            (payload.job_name is not None and payload.job_name not in node.get("jobNames", []))
            or origin.get("name") != repository
            or origin.get("location", {}).get("name") != target.dagster_location
        ):
            raise HTTPException(
                status_code=422,
                detail="Every selected downstream asset must belong to the same scoped job and repository.",
            )
        if node.get("hasMaterializePermission") is not True:
            raise HTTPException(
                status_code=403, detail="Dagster denies materialization of a selected asset."
            )
        nodes.append(node)
    return sorted(selected), nodes


def _materialization_partition_keys(
    root: dict[str, Any], nodes: list[dict[str, Any]], payload: MaterializeAssetAction
) -> list[str | None]:
    """Select actual partition boundaries or one unpartitioned/full-refresh run."""
    keys: list[str | None] = [None]
    if payload.mode == "full":
        if any("dbt" not in node.get("kinds", []) for node in nodes):
            raise HTTPException(
                status_code=422,
                detail="Full refresh requires dbt assets. DLT sources do not expose a safe whole-table replacement contract.",
            )
    elif root.get("isPartitioned") is True:
        connection = root.get("partitionKeyConnection")
        if not isinstance(connection, dict) or not isinstance(connection.get("results"), list):
            raise BadGatewayError("Dagster returned no partition keys.")
        available = connection["results"]
        if not available or any(not isinstance(key, str) or not key for key in available):
            raise HTTPException(status_code=422, detail="No available partitions for this asset.")
        if payload.mode == "backfill":
            definition = root.get("partitionDefinition") or {}
            if definition.get("type") != "TIME_WINDOW" or not isinstance(
                definition.get("fmt"), str
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Time-range backfill requires a time-window partition definition.",
                )
            keys = []
            keys.extend(
                _time_range_keys(available, definition["fmt"], payload.from_time, payload.to_time)
            )
            if len(keys) > 500:
                raise HTTPException(
                    status_code=422, detail="Time range exceeds the 500-partition action limit."
                )
        else:
            keys = [available[0]]
    elif payload.mode == "backfill":
        raise HTTPException(
            status_code=422, detail="Time-range backfill requires a partitioned asset."
        )
    if payload.mode != "backfill" and (
        payload.from_time is not None or payload.to_time is not None
    ):
        raise HTTPException(status_code=422, detail="Time ranges are only valid in backfill mode.")
    if any(node.get("partitionDefinition") != root.get("partitionDefinition") for node in nodes):
        raise HTTPException(
            status_code=422, detail="Selected assets must share a partition definition."
        )
    return keys


async def _materialization_runs(
    selector: dict[str, str], selected: list[str], keys: list[str | None]
) -> list[dict[str, Any]]:
    """Read the owning job's partition configuration without caller overrides."""
    import yaml

    runs: list[dict[str, Any]] = []
    for key in keys:
        config = {}
        if key is not None:
            result = _response_field(
                await _graphql(
                    _OPERATION_PARTITION_QUERY,
                    {
                        "selector": selector,
                        "partition": key,
                        "assets": [{"path": item.split("/")} for item in selected],
                    },
                ),
                "pipelineOrError",
            )
            value = (result.get("partition") or {}).get("runConfigOrError") or {}
            if value.get("__typename") != "PartitionRunConfig" or not isinstance(
                value.get("yaml"), str
            ):
                raise BackendUnavailableError("Dagster partition run configuration is unavailable.")
            try:
                config = yaml.safe_load(value["yaml"]) or {}
            except yaml.YAMLError as exc:
                raise BadGatewayError(
                    "Dagster returned invalid partition run configuration."
                ) from exc
            if not isinstance(config, dict):
                raise BadGatewayError("Dagster returned invalid partition run configuration.")
        runs.append({"partition_key": key, "run_config": config})
    return runs


async def _materialization_plan(
    request: Request,
    env: Environment,
    asset_id: str,
    payload: MaterializeAssetAction,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> dict[str, Any]:
    from phlo_api.api.v1_branch_workflows import _scoped_reference
    from phlo.security import is_regulated

    target, repository, jobs = await _action_context(
        request, env, asset_id, allowed_query=allowed_query
    )
    if payload.job_name is not None and payload.job_name not in jobs:
        raise NotFoundError("Job was not found for this asset.")
    if payload.partition_key is not None or payload.run_config is not None:
        raise HTTPException(
            status_code=422,
            detail="Mode-based operations use Dagster's partition config, not caller overrides.",
        )
    ref = await _scoped_reference(
        payload.write_ref or target.nessie_ref, env, target, branch_only=True
    )
    if ref.protected and (payload.mode == "full" or is_regulated()):
        raise HTTPException(
            status_code=403,
            detail="Use an environment-scoped work branch and the signed merge workflow for this write.",
        )
    selected, nodes = await _materialization_nodes(
        request, env, asset_id, payload, target, repository, allowed_query=allowed_query
    )
    job_name = payload.job_name
    if job_name is None:
        # Dagster's native materialization uses a discovered implicit job, subset by asset keys.
        common = set(jobs).intersection(*(set(node.get("jobNames", [])) for node in nodes))
        implicit = sorted(name for name in common if name.startswith("__ASSET_JOB"))
        if len(implicit) != 1:
            raise HTTPException(
                status_code=422,
                detail="No unique automatic asset job covers this selection. Select a named job explicitly.",
            )
        job_name = implicit[0]
    selector = {
        "pipelineName": job_name,
        "repositoryName": repository,
        "repositoryLocationName": target.dagster_location,
    }
    job = _response_field(
        await _graphql(_OPERATION_JOB_QUERY, {"selector": selector}), "pipelineOrError"
    )
    if job.get("__typename") != "Pipeline" or not isinstance(job.get("pipelineSnapshotId"), str):
        raise BadGatewayError("Dagster returned no job revision.")
    if job.get("hasLaunchExecutionPermission") is not True:
        raise HTTPException(status_code=403, detail="Dagster denies launching this job.")
    root = next(node for node in nodes if node["assetKey"]["path"] == asset_id.split("/"))
    keys = _materialization_partition_keys(root, nodes, payload)
    runs = await _materialization_runs(selector, selected, keys)
    return {
        "write_ref": ref.name,
        "ref_hash": ref.hash,
        "selected_assets": selected,
        "job_name": job_name,
        "job_snapshot_id": job["pipelineSnapshotId"],
        "op_versions": [node.get("opVersion") for node in nodes],
        "mode": payload.mode,
        "runs": runs,
        "repository": repository,
        "location": target.dagster_location,
    }


def _require_full_refresh_mfa(request: Request) -> None:
    from phlo_api.api.authentication import authenticate_request
    from phlo.capabilities import AuthenticatedSession
    from phlo.compliance.signatures.step_up import RecentMfaClaimsChallenge

    auth = authenticate_request(request)
    if (
        not isinstance(auth.session, AuthenticatedSession)
        or not RecentMfaClaimsChallenge().challenge(auth.session).success
    ):
        raise HTTPException(
            status_code=403, detail="Recent verified human MFA is required for full refresh."
        )


@router.post("/assets/{asset_id:path}/materialize", response_model=AssetActionResponse)
async def v1_asset_materialize(
    request: Request,
    asset_id: str,
    payload: MaterializeAssetAction,
    env: Environment = Query(),
) -> AssetActionResponse:
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key
    from phlo_api.observatory_api.orchestrator_operations import resolve_orchestrator_operations

    asset_id = asset_id.strip("/")
    target, repository_name, job_names = await _action_context(request, env, asset_id)
    auth = require_scope(request, "lakehouse:operate")
    if (payload.mode is None or payload.job_name is not None) and payload.job_name not in job_names:
        raise NotFoundError("Job was not found for this asset.")
    enforce_rate_limit(auth["subject"], "materialize_asset")
    require_idempotency_key(payload.idempotency_key)
    provider = resolve_orchestrator_operations()
    write_ref = payload.write_ref or target.nessie_ref
    if payload.mode is None and (
        payload.write_ref is not None
        or payload.rebuild_downstream
        or payload.plan_hash is not None
        or payload.from_time is not None
        or payload.to_time is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="Operation controls require an explicit mode and estimated plan.",
        )
    if payload.mode is not None and not payload.dry_run and not payload.confirmed:
        raise HTTPException(status_code=422, detail="Confirm the materialization before executing.")
    if payload.mode == "full" and not payload.dry_run:
        _require_full_refresh_mfa(request)
    tags = {"environment": env, "phlo/ref": write_ref}

    async def execute() -> dict[str, Any]:
        if payload.mode is not None:
            plan = await _materialization_plan(request, env, asset_id, payload)
            if payload.plan_hash != _plan_hash(plan):
                raise HTTPException(
                    status_code=409,
                    detail="The ref, job revision, selection or partition config changed. Refresh the estimate.",
                )
            results = []
            for index, run in enumerate(plan["runs"]):
                child_key = hashlib.sha256(
                    f"{auth['subject']}:{payload.idempotency_key}:{index}".encode()
                ).hexdigest()
                result = _action_result(
                    await provider.materialize_asset(
                        asset_id,
                        {
                            "job_name": plan["job_name"],
                            "dry_run": payload.dry_run,
                            "partition_key": run["partition_key"],
                            "run_config": run["run_config"],
                            "asset_selection": plan["selected_assets"],
                            "repository_location_name": plan["location"],
                            "repository_name": plan["repository"],
                            "idempotency_key": child_key,
                            "tags": {
                                **tags,
                                "phlo/materialize_mode": payload.mode,
                                "phlo/full_refresh_asset": asset_id
                                if payload.mode == "full"
                                else "",
                                "phlo/plan": payload.plan_hash,
                                "phlo/ref_hash": plan["ref_hash"],
                                "phlo/job_snapshot_id": plan["job_snapshot_id"],
                            },
                        },
                    )
                )
                results.append(result)
                if result.get("accepted") is not True:
                    break
            return {
                "accepted": len(results) == len(plan["runs"])
                and all(item.get("accepted") is True for item in results),
                "run_ids": [item["run_id"] for item in results if item.get("run_id")],
                "runs": results,
                "job_name": plan["job_name"],
                "plan_hash": payload.plan_hash,
                "dry_run": payload.dry_run,
            }
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

    intent = payload.model_dump(exclude={"idempotency_key"})
    intent_digest = hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    action_target = f"{auth['subject']}:{env}:{asset_id}@{write_ref}:materialize:{intent_digest}"
    bound_target = idempotency_key_target(payload.idempotency_key, "v1_materialize_asset")
    if bound_target is not None and bound_target != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})
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
            payload=payload.model_dump(exclude={"idempotency_key", "run_config"}),
            result=value,
        ),
    )
    return AssetActionResponse(env=env, asset_id=asset_id, nessie_ref=write_ref, result=result)


@router.post("/assets/{asset_id:path}/backfill", response_model=AssetActionResponse)
async def v1_asset_backfill(
    request: Request,
    asset_id: str,
    payload: BackfillAssetAction,
    env: Environment = Query(),
) -> AssetActionResponse:
    from phlo_api.api.operation_controls import (
        IdempotencyConflict,
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
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
        [await _latest_asset_partition(asset_id, target.dagster_location, repository_name)]
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
            "dry_run": payload.dry_run,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    intent_digest = hashlib.sha256(intent).hexdigest()
    action_target = f"{env}:{asset_id}@{target.nessie_ref}:backfill:{intent_digest}"
    bound_target = idempotency_key_target(payload.idempotency_key, "v1_backfill_asset")
    if bound_target is not None and bound_target != action_target:
        raise IdempotencyConflict({"error": "idempotency_key_conflict"})
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
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    page = _page(
        env,
        "assets",
        await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"})),
        limit,
        cursor,
    )
    page = await _asset_page_with_table_stats(page, target.nessie_ref)
    try:
        policies, defaults = _freshness_policy_inputs(request, env)
    except BackendUnavailableError:
        policies, defaults = {}, None
    now = datetime.now(UTC)
    return page.model_copy(
        update={
            "items": [_apply_asset_freshness(item, policies, defaults, now) for item in page.items]
        }
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
            if (
                item := _asset_run_view(row, matches[0], target.dagster_location, target.nessie_ref)
            )
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


@router.get("/assets/{asset_id:path}/query-usage", response_model=QueryUsagePage)
async def v1_asset_query_usage(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Limit = 100,
    cursor: str | None = None,
) -> QueryUsagePage:
    """Return retained verified Trino table inputs, not a complete access ledger."""
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    asset_id = asset_id.strip("/")
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    matches = [asset for asset in assets if asset.id == asset_id]
    if not matches:
        raise NotFoundError("Asset was not found.")
    if not matches[0].relation:
        if cursor is not None:
            raise HTTPException(status_code=400, detail="Asset relation changed; cursor is stale.")
        return QueryUsagePage(
            env=env,
            asset_id=asset_id,
            table_name=None,
            nessie_ref=target.nessie_ref,
            status="unavailable",
            reason="no_asset_relation",
            items=[],
            next_cursor=None,
        )
    relation = matches[0].relation
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_table_snapshots"], {"table_name": relation}
    )
    return await asyncio.to_thread(
        read_query_usage,
        env,
        target.nessie_ref,
        asset_id,
        relation.replace(".", "/"),
        limit,
        cursor,
    )


@router.get("/assets/{asset_id:path}/usage", response_model=AssetUsage)
async def v1_asset_usage(
    request: Request,
    asset_id: str,
    env: Environment = Query(),
    limit: Limit = 100,
    cursor: str | None = None,
) -> AssetUsage:
    """Expose only this API's retained preview reads, never inferred query usage."""
    from phlo_api.api.operation_controls import read_operation_audit

    target = _target(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    asset_id = asset_id.strip("/")
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    if not any(asset.id == asset_id for asset in assets):
        raise NotFoundError("Asset was not found.")
    try:
        records = await asyncio.to_thread(read_operation_audit, "v1_asset_preview")
        if any(
            record.get("surface") != "phlo-api"
            or not isinstance(record.get("payload"), dict)
            or not all(
                isinstance(record["payload"].get(field), str) and record["payload"][field]
                for field in ("env", "asset_id", "nessie_ref")
            )
            or record.get("target")
            != f"{record['payload']['env']}:{record['payload']['asset_id']}@{record['payload']['nessie_ref']}"
            for record in records
        ):
            raise ValueError("Preview access record has no resource identity.")
        items = [
            PreviewAccess.model_validate_json(
                json.dumps({"observed_at": record["timestamp"], **record["result"]})
            )
            for record in records
            if record["payload"].get("env") == env
            and record["payload"].get("asset_id") == asset_id
            and record["payload"].get("nessie_ref") == target.nessie_ref
        ]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BackendUnavailableError("Preview access history is unavailable.") from exc
    items.reverse()
    identity = f"usage:{asset_id}@{target.nessie_ref}"
    digest = hashlib.sha256(
        json.dumps([item.model_dump(mode="json") for item in items], separators=(",", ":")).encode()
    ).hexdigest()[:16]
    kind = f"{identity}:{digest}"
    offset = _offset(cursor, env, kind)
    if cursor is not None and offset >= len(items):
        raise HTTPException(status_code=400, detail="Invalid or stale usage cursor.")
    return AssetUsage(
        env=env,
        asset_id=asset_id,
        nessie_ref=target.nessie_ref,
        status="partial" if items else "unavailable",
        reason=None if items else "no_retained_preview_evidence",
        items=items[offset : offset + limit],
        next_cursor=_cursor(env, kind, offset + limit) if offset + limit < len(items) else None,
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
    definitions = await _asset_check_definitions(
        key,
        target.dagster_location,
        matches[0].repository_name,
        allow_legacy=matches[0].check_definition_scope == "unique",
    )
    executions, unverifiable_checks = await _asset_check_executions(
        key,
        [definition.name for definition in definitions],
        target.dagster_location,
        matches[0].repository_name,
        target.nessie_ref,
        limit,
    )
    return AssetChecks(
        env=env,
        asset_id=asset_id,
        definitions=definitions,
        executions=executions,
        history=CheckHistoryEvidence(
            status="partial" if unverifiable_checks else "complete",
            unverifiable_checks=sorted(unverifiable_checks),
        ),
    )


async def _asset_check_definitions(
    key: list[str],
    repository_location: str,
    repository_name: str | None,
    *,
    allow_legacy: bool = True,
) -> list[CheckDefinition]:
    nodes = await _asset_check_inventory(
        repository_location, repository_name, include_legacy=allow_legacy
    )
    return _check_definitions(nodes, key, allow_legacy=allow_legacy)


async def _asset_check_inventory(
    repository_location: str, repository_name: str | None, *, include_legacy: bool
) -> list[dict[str, Any]]:
    if not repository_name:
        raise BackendUnavailableError("Dagster asset-check repository identity is unavailable.")
    result = await _graphql(
        ASSET_CHECKS_QUERY,
        {
            "repositorySelector": {
                "repositoryLocationName": repository_location,
                "repositoryName": repository_name,
            },
            "limit": _CHECK_DEFINITION_LIMIT + 1,
            "includeLegacy": include_legacy,
        },
    )
    data = result.get("data")
    repositories = data.get("repositoriesOrError") if isinstance(data, dict) else None
    nodes = (
        repositories.get("nodes")
        if isinstance(repositories, dict)
        and repositories.get("__typename") == "RepositoryConnection"
        else None
    )
    if result.get("errors") or not isinstance(nodes, list):
        raise BadGatewayError("Dagster returned invalid asset-check definitions.")
    return _check_nodes_for_location(nodes, repository_location, repository_name)


def _check_definitions(
    nodes: list[dict[str, Any]], key: list[str], *, allow_legacy: bool
) -> list[CheckDefinition]:
    scoped_nodes = [node for node in nodes if _key_path(node.get("assetKey")) == key]
    if len(scoped_nodes) != 1:
        raise BackendUnavailableError("Dagster asset-check definitions are not uniquely scoped.")
    node = scoped_nodes[0]
    export = _local_check_export(node, key)
    if export is not None:
        return export
    if not allow_legacy:
        raise BackendUnavailableError("Repository-local asset-check export is unavailable.")
    response = node.get("assetChecksOrError")
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


def _local_check_export(node: dict[str, Any], key: list[str]) -> list[CheckDefinition] | None:
    if "metadataEntries" not in node:
        return None
    entries = node.get("metadataEntries")
    if not isinstance(entries, list):
        raise BadGatewayError("Dagster returned invalid asset-check export metadata.")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("label") == _CHECK_INVENTORY_METADATA_KEY
    ]
    if not matches:
        return None
    if len(matches) != 1 or not isinstance(matches[0].get("jsonString"), str):
        raise BadGatewayError("Dagster returned invalid asset-check export metadata.")
    raw = matches[0]["jsonString"]
    if len(raw.encode()) > _CHECK_INVENTORY_MAX_BYTES:
        raise BackendUnavailableError("Repository-local asset-check export exceeds its bound.")
    try:
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload["version"] != _CHECK_INVENTORY_VERSION
            or payload.get("asset_key") != key
            or type(payload.get("complete")) is not bool
            or not isinstance(payload.get("checks"), list)
            or not isinstance(payload.get("digest"), str)
        ):
            raise ValueError
        digest = payload.pop("digest")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != digest:
            raise ValueError
        if not payload["complete"]:
            raise BackendUnavailableError("Repository-local asset-check export is incomplete.")
        checks = payload["checks"]
        if len(checks) > _CHECK_DEFINITION_LIMIT:
            raise BackendUnavailableError("Repository-local asset-check export exceeds its bound.")
        definitions = [CheckDefinition.model_validate(item) for item in checks]
        if len({definition.name for definition in definitions}) != len(
            definitions
        ) or definitions != sorted(definitions, key=lambda item: item.name):
            raise ValueError
        return definitions
    except BackendUnavailableError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BadGatewayError("Dagster returned invalid asset-check export metadata.") from exc


def _has_local_check_export(nodes: list[dict[str, Any]], key: list[str]) -> bool:
    scoped_nodes = [node for node in nodes if _key_path(node.get("assetKey")) == key]
    if len(scoped_nodes) != 1:
        raise BackendUnavailableError("Dagster asset-check definitions are not uniquely scoped.")
    return _local_check_export(scoped_nodes[0], key) is not None


def _check_nodes_for_location(
    repositories: list[Any], repository_location: str, repository_name: str
) -> list[dict[str, Any]]:
    scoped_nodes = []
    for repository in repositories:
        if (
            not isinstance(repository, dict)
            or not isinstance(repository.get("location"), dict)
            or not isinstance(repository["location"].get("name"), str)
            or not isinstance(repository.get("assetNodes"), list)
        ):
            raise BadGatewayError("Dagster returned invalid asset-check definitions.")
        if (
            repository["location"]["name"] != repository_location
            or repository.get("name") != repository_name
        ):
            raise BadGatewayError("Dagster asset-check inventory does not match its selector.")
        for node in repository["assetNodes"]:
            if (
                not isinstance(node, dict)
                or _repository_location(node) != repository_location
                or node["repository"].get("name") != repository_name
            ):
                raise BadGatewayError(
                    "Dagster asset-check definition does not match its repository."
                )
            scoped_nodes.append(node)
    keys = [tuple(_key_path(node.get("assetKey"))) for node in scoped_nodes]
    if len(keys) != len(set(keys)):
        raise BackendUnavailableError("Dagster asset-check definitions are not uniquely scoped.")
    return scoped_nodes


async def _asset_check_executions(
    key: list[str],
    check_names: list[str],
    repository_location: str,
    repository_name: str | None,
    ref: str,
    limit: int,
) -> tuple[list[CheckExecution], set[str]]:
    groups, _, unverifiable_checks = await _asset_check_execution_groups(
        key, check_names, repository_location, repository_name, ref, limit
    )
    executions = [
        item for name, group in groups.items() if name not in unverifiable_checks for item in group
    ]
    executions.sort(key=lambda item: (item.timestamp, item.check_name, item.run_id), reverse=True)
    return executions[:limit], unverifiable_checks


async def _asset_check_execution_groups(
    key: list[str],
    check_names: list[str],
    repository_location: str,
    repository_name: str | None,
    ref: str,
    limit: int,
) -> tuple[dict[str, list[CheckExecution]], dict[str, int], set[str]]:
    semaphore = asyncio.Semaphore(8)

    async def executions_for_check(
        check_name: str,
    ) -> tuple[str, int, list[CheckExecution], bool]:
        async with semaphore:
            result = await _graphql(
                ASSET_CHECK_EXECUTIONS_QUERY,
                {"assetKey": {"path": key}, "checkName": check_name, "limit": limit},
            )
        data = result.get("data")
        rows = data.get("assetCheckExecutions") if isinstance(data, dict) else None
        if result.get("errors") or not isinstance(rows, list) or len(rows) > limit:
            raise BadGatewayError("Dagster returned invalid asset-check history.")
        executions, history_verified = _normalize_check_executions(
            rows, repository_location, repository_name, ref, check_name
        )
        return (
            check_name,
            len(rows),
            executions,
            history_verified,
        )

    groups = await asyncio.gather(*(executions_for_check(name) for name in check_names))
    return (
        {name: executions for name, _, executions, _ in groups},
        {name: count for name, count, _, _ in groups},
        {name for name, _, _, verified in groups if not verified},
    )


def _normalize_check_executions(
    raw_executions: list[Any],
    repository_location: str,
    repository_name: str | None,
    ref: str,
    check_name: str,
) -> tuple[list[CheckExecution], bool]:
    scoped = []
    history_verified = True
    for raw_execution in raw_executions:
        try:
            execution, verified = _normalize_check_execution(
                raw_execution, repository_location, repository_name, ref, check_name
            )
        except (BadGatewayError, TypeError, ValueError, OverflowError):
            execution, verified = None, False
        if not verified:
            history_verified = False
        if execution is not None:
            scoped.append(execution)
    return scoped, history_verified


def _normalize_check_execution(
    execution: Any,
    repository_location: str,
    repository_name: str | None,
    ref: str,
    check_name: str,
) -> tuple[CheckExecution | None, bool]:
    if not isinstance(execution, dict):
        return None, False
    run_id = execution.get("runId")
    # Runless evaluations have no verifiable repository identity.
    if not isinstance(run_id, str) or not run_id:
        return None, False
    run = execution.get("run")
    matches_target, identity_verified = _check_execution_run_scope(
        run, run_id, repository_location, repository_name, ref
    )
    if not identity_verified:
        return None, False
    if not matches_target:
        return None, True
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
    ), True


def _check_execution_run_scope(
    run: Any, run_id: str, repository_location: str, repository_name: str | None, ref: str
) -> tuple[bool, bool]:
    if not isinstance(run, dict):
        return False, False
    run_run_id = run.get("runId")
    if not isinstance(run_run_id, str) or not run_run_id or run_run_id != run_id:
        return False, False
    origin = run.get("repositoryOrigin")
    location = origin.get("repositoryLocationName") if isinstance(origin, dict) else None
    origin_repository = origin.get("repositoryName") if isinstance(origin, dict) else None
    if not isinstance(location, str) or not location:
        return False, False
    if location != repository_location:
        return False, True
    if not isinstance(origin_repository, str) or not origin_repository:
        return False, False
    if origin_repository != repository_name:
        return False, True
    tags = run.get("tags")
    ref_values = (
        [tag.get("value") for tag in tags if isinstance(tag, dict) and tag.get("key") == "phlo/ref"]
        if isinstance(tags, list)
        else []
    )
    if (
        not isinstance(tags, list)
        or len(ref_values) != 1
        or not isinstance(ref_values[0], str)
        or not ref_values[0]
    ):
        return False, False
    if ref_values[0] != ref:
        return False, True
    return True, True


async def _overview_quality_checks(
    assets: list[AssetView], repository_location: str, ref: str
) -> QualityCheckEvidence:
    if len(assets) > _OVERVIEW_CHECK_ASSET_LIMIT:
        return QualityCheckEvidence(status="unknown", counts=None, reason="asset_limit_exceeded")
    definitions_by_asset: list[tuple[list[str], str | None, list[CheckDefinition]]] = []
    inventories: dict[str | None, list[dict[str, Any]]] = {}
    ambiguous_repositories = {
        asset.repository_name for asset in assets if asset.check_definition_scope != "unique"
    }
    definition_count = 0
    for asset in assets:
        key = asset.id.split("/")
        if asset.repository_name not in inventories:
            inventories[asset.repository_name] = await _asset_check_inventory(
                repository_location,
                asset.repository_name,
                include_legacy=asset.repository_name not in ambiguous_repositories,
            )
        if asset.check_definition_scope != "unique" and not _has_local_check_export(
            inventories[asset.repository_name], key
        ):
            return QualityCheckEvidence(
                status="unknown", counts=None, reason="check_definition_scope_unverified"
            )
        definitions = _check_definitions(
            inventories[asset.repository_name],
            key,
            allow_legacy=asset.check_definition_scope == "unique",
        )
        definition_count += len(definitions)
        if definition_count > _OVERVIEW_CHECK_LIMIT:
            return QualityCheckEvidence(
                status="unknown", counts=None, reason="check_limit_exceeded"
            )
        definitions_by_asset.append((key, asset.repository_name, definitions))

    if definition_count == 0:
        return QualityCheckEvidence(status="unknown", counts=None, reason="no_checks_defined")
    passing = 0
    total = definition_count
    unevaluated = 0
    failing_assets: list[str] = []
    for key, repository_name, definitions in definitions_by_asset:
        names = [definition.name for definition in definitions]
        if not names:
            continue
        histories, raw_counts, unverifiable_checks = await _asset_check_execution_groups(
            key,
            names,
            repository_location,
            repository_name,
            ref,
            _CHECK_EXECUTION_SCAN_LIMIT,
        )
        if unverifiable_checks:
            return QualityCheckEvidence(
                status="unknown", counts=None, reason="check_history_scope_unverified"
            )
        for name in names:
            latest_evaluation = max(
                histories[name],
                key=lambda execution: execution.timestamp,
                default=None,
            )
            if (
                latest_evaluation is None
                or latest_evaluation.passed is None
                or latest_evaluation.status not in {"SUCCEEDED", "FAILED"}
            ):
                if raw_counts[name] >= _CHECK_EXECUTION_SCAN_LIMIT:
                    return QualityCheckEvidence(
                        status="unknown", counts=None, reason="check_history_limit_exceeded"
                    )
                unevaluated += 1
                continue
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
    target = _target(request, env)
    nodes = await _asset_nodes(request, env)
    matches = [node for node in nodes if _key_path(node.get("assetKey")) == asset_id.split("/")]
    if not matches:
        raise NotFoundError("Asset was not found.")
    inventory = [_asset_view(node, target.nessie_ref) for node in nodes]
    payload = matches[0]
    detail = _asset_view(payload, target.nessie_ref)
    definition_entries = payload.get("metadataEntries") or []
    materialization = _scoped_materialization(payload, target.nessie_ref)
    materialization_entries = (
        (materialization.get("metadataEntries") or []) if materialization else []
    )

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
    schema_source: Literal["materialization", "definition", "catalog", "unavailable"] = (
        "materialization" if latest_schema else "definition" if columns else "unavailable"
    )
    schema_observed_at = detail.last_materialization_at if latest_schema else None
    table_metadata: dict[str, Any] = {}
    table_metadata_error = None
    if detail.relation:
        try:
            table_metadata = await asyncio.wait_for(
                asyncio.to_thread(
                    _iceberg_asset_metadata, _table_name(detail.relation), target.nessie_ref
                ),
                timeout=5,
            )
        except Exception:
            table_metadata_error = (
                "Ref-scoped table metadata is unavailable. Check the catalog connection."
            )
        if not table_metadata_error:
            table_metadata_error = _table_stats_reason(table_metadata)
        if detail.freshness_source is None:
            detail = detail.model_copy(
                update={
                    "freshness_observed_at": table_metadata.get("freshness_observed_at"),
                    "freshness_source": "iceberg_snapshot"
                    if table_metadata.get("freshness_observed_at") is not None
                    else None,
                    "freshness_reason": table_metadata.get("freshness_reason")
                    or ("catalog_unavailable" if table_metadata_error else "no_observation"),
                }
            )
        if not columns and table_metadata.get("columns"):
            columns = [AssetColumn.model_validate(column) for column in table_metadata["columns"]]
            schema_source = "catalog"
            schema_observed_at = datetime.now(UTC)

    observed_lineage = _column_lineage(materialization_entries) or _column_lineage(
        definition_entries
    )
    reachable = {asset_id}
    downstream = []
    remaining = [item for item in inventory if item.id != asset_id]
    while True:
        next_assets = [
            item
            for item in remaining
            if any("/".join(key) in reachable for key in item.dependencies)
        ]
        if not next_assets:
            break
        downstream.extend(next_assets)
        reachable.update(item.id for item in next_assets)
        remaining = [item for item in remaining if item.id not in reachable]
    detail_payload = detail.model_dump()
    detail_payload.update(
        {
            "columns": columns,
            "schema_observed_at": schema_observed_at,
            "schema_source": schema_source,
            "row_count": table_metadata.get("row_count"),
            "size_bytes": table_metadata.get("size_bytes"),
            "current_snapshot_id": table_metadata.get("snapshot_id"),
            "sort_order": table_metadata.get("sort_order"),
            "table_metadata_error": table_metadata_error,
            "materializations": _asset_write_counts(payload, target.nessie_ref),
            "column_lineage": observed_lineage,
            "downstream": downstream,
        }
    )
    try:
        policies, defaults = _freshness_policy_inputs(request, env)
    except BackendUnavailableError:
        policies, defaults = {}, None
    detail = _apply_asset_freshness(
        AssetDetail.model_validate(detail_payload), policies, defaults, datetime.now(UTC)
    )
    return AssetDetail.model_validate(detail.model_dump())


@router.post("/assets/{asset_id:path}/row-count", response_model=AssetExactCount, status_code=202)
async def v1_asset_exact_row_count(
    request: Request, asset_id: str, env: Environment = Query()
) -> AssetExactCount:
    asset_id = asset_id.strip("/")
    target = _target(request, env)
    nodes = await _asset_nodes(request, env)
    matches = [node for node in nodes if _key_path(node.get("assetKey")) == asset_id.split("/")]
    if not matches:
        raise NotFoundError("Asset was not found.")
    table_name = _declared_relation(matches[0].get("metadataEntries") or [])
    if table_name is None:
        raise BackendUnavailableError("This asset has no declared Iceberg table to count.")
    table_name = _table_name(table_name)
    try:
        snapshot_id = await asyncio.wait_for(
            asyncio.to_thread(_iceberg_current_snapshot_id, table_name, target.nessie_ref),
            timeout=_EXACT_COUNT_SNAPSHOT_LOOKUP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise BackendUnavailableError(
            "The selected-ref Iceberg snapshot identity is unavailable."
        ) from exc
    if snapshot_id is None:
        raise BackendUnavailableError("The selected-ref table has no current snapshot to count.")
    query = start_exact_asset_count(request, env, table_name=table_name, snapshot_id=snapshot_id)
    return AssetExactCount(
        env=env,
        nessie_ref=target.nessie_ref,
        source="trino_count_star",
        observed_at=datetime.now(UTC),
        snapshot_id=snapshot_id,
        query=query,
    )


@router.get("/sources", response_model=AssetPage)
async def v1_sources(
    request: Request, env: Environment = Query(), limit: Limit = 100, cursor: str | None = None
) -> AssetPage:
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    return _page(
        env,
        "sources",
        [asset for asset in assets if asset.is_source or asset.is_ingestion],
        limit,
        cursor,
    )


def _freshness_policy_inputs(
    request: Request, env: Environment
) -> tuple[dict[str, int], OperationalSettings | None]:
    from phlo_api.incidents import list_asset_incident_policies

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
    try:
        defaults = get_operational_settings()
    except StorageUnavailableError:
        defaults = None
    return policies, defaults


def _count_freshness(
    assets: list[AssetView],
    policies: dict[str, int],
    defaults: OperationalSettings | None,
    now: datetime,
) -> FreshnessCounts:
    counts = {"fresh": 0, "stale": 0, "unknown": 0}
    for asset in assets:
        status, _ = _freshness_state(asset, policies, defaults, now)
        counts[status] += 1
    return FreshnessCounts(**counts)


def _freshness_state(
    asset: AssetView,
    policies: dict[str, int],
    defaults: OperationalSettings | None,
    now: datetime,
) -> tuple[Literal["fresh", "stale", "unknown"], str | None]:
    sla = policies.get(asset.id, asset.freshness_sla_seconds)
    if sla is None and defaults is not None:
        sla = defaults.freshness_sla_seconds(asset.layer)
    if sla is None:
        return "unknown", "missing_sla"
    observed = asset.freshness_observed_at
    if observed is None:
        return "unknown", asset.freshness_reason or "no_observation"
    if observed > now:
        return "unknown", "future_observation"
    if (now - observed).total_seconds() > sla:
        return "stale", None
    return "fresh", None


def _apply_asset_freshness(
    asset: AssetView,
    policies: dict[str, int],
    defaults: OperationalSettings | None,
    now: datetime,
) -> AssetView:
    status, reason = _freshness_state(asset, policies, defaults, now)
    return asset.model_copy(update={"freshness_status": status, "freshness_reason": reason})


@router.get("/layers", response_model=LayerPage)
async def v1_layers(
    request: Request, env: Environment = Query(), limit: Limit = 100, cursor: str | None = None
) -> LayerPage:
    assets = await _assets(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "cursor"}))
    assets = await _assets_with_freshness_evidence(assets, env, target.nessie_ref)
    groups: dict[
        tuple[str | None, Literal["bronze", "silver", "gold"] | None], list[AssetView]
    ] = {}
    for asset in assets:
        groups.setdefault((asset.group_name, asset.layer), []).append(asset)
    try:
        policies, defaults = _freshness_policy_inputs(request, env)
    except BackendUnavailableError:
        policies, defaults = {}, None
    now = datetime.now(UTC)
    layers = [
        LayerView(
            group_name=name,
            layer=layer,
            asset_count=len(group),
            materialized_asset_count=sum(
                asset.last_materialization_at is not None for asset in group
            ),
            latest_materialization_at=max(
                (asset.last_materialization_at for asset in group if asset.last_materialization_at),
                default=None,
            ),
            freshness_counts=_count_freshness(group, policies, defaults, now),
        )
        for (name, layer), group in sorted(
            groups.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")
        )
    ]
    offset = _offset(cursor, env, "layers")
    selected = layers[offset : offset + limit]
    next_cursor = _cursor(env, "layers", offset + limit) if offset + limit < len(layers) else None
    return LayerPage(env=env, items=selected, next_cursor=next_cursor)


@router.get("/overview", response_model=OverviewResponse)
async def v1_overview(request: Request, env: Environment = Query()) -> OverviewResponse:
    assets = await _assets(request, env)
    from phlo_api.api.v1 import _runs
    from phlo_api.incidents import incident_stats

    target = _target(request, env)
    assets = await _assets_with_freshness_evidence(assets, env, target.nessie_ref)
    runs = await _runs(target.dagster_location, target.nessie_ref)
    try:
        quality_checks = await _overview_quality_checks(
            assets, target.dagster_location, target.nessie_ref
        )
    except (BackendUnavailableError, BadGatewayError, NotFoundError):
        quality_checks = QualityCheckEvidence(
            status="unknown", counts=None, reason="source_unavailable"
        )
    stats = incident_stats(request, env)
    policies, defaults = _freshness_policy_inputs(request, env)
    freshness = _count_freshness(assets, policies, defaults, datetime.now(UTC))
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
        incident_counts=stats.counts,
        freshness_counts=freshness,
        run_status_counts=run_status_counts,
        run_history_truncated=len(runs) == 100,
        quality_checks=quality_checks,
    )
