"""Quality API Router.

Endpoints for aggregating quality check results from Dagster.
Powers the Quality Center dashboard and asset quality tabs.

This module queries the Dagster GraphQL API to fetch asset check
executions and statuses, normalizing the results into a quality
overview with categorization, scoring, and trending.

Key Endpoints:
    GET /overview: Get aggregated quality metrics.
    GET /assets/{key}/checks: Get checks for a specific asset.
    GET /assets/{key}/checks/{name}/history: Get execution history.
    GET /failing: Get all currently failing checks.

Environment Variables:
    DAGSTER_GRAPHQL_URL: URL for Dagster GraphQL endpoint.

Example:
    Getting quality overview:

    .. code-block:: bash

        curl http://localhost:4000/api/quality/overview

"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["quality"])

DEFAULT_DAGSTER_URL = "http://dagster:3000/graphql"


def resolve_dagster_url(override: str | None = None) -> str:
    """Resolve the Dagster GraphQL URL from override, environment, or default."""
    if override and override.strip():
        return resolve_url(override, port_env_var="DAGSTER_PORT")
    return resolve_url(
        project_env_value("DAGSTER_GRAPHQL_URL", DEFAULT_DAGSTER_URL) or DEFAULT_DAGSTER_URL,
        port_env_var="DAGSTER_PORT",
    )


# --- GraphQL Queries ---

ASSET_CHECKS_QUERY = """
query AssetChecksQuery {
    assetNodes {
        assetKey {
            path
        }
        assetChecksOrError {
            __typename
            ... on AssetChecks {
                checks {
                    name
                    description
                }
            }
            ... on AssetCheckNeedsMigrationError {
                message
            }
        }
    }
}
"""

ASSET_CHECK_EXECUTIONS_QUERY = """
query AssetCheckExecutionsQuery($assetKey: AssetKeyInput!, $limit: Int!) {
    assetCheckExecutions(assetKey: $assetKey, limit: $limit) {
        status
        runId
        timestamp
        checkName
        evaluation {
            severity
            metadataEntries {
                __typename
                label
                ... on TextMetadataEntry { text }
                ... on IntMetadataEntry { intValue }
                ... on FloatMetadataEntry { floatValue }
                ... on BoolMetadataEntry { boolValue }
                ... on JsonMetadataEntry { jsonString }
            }
        }
    }
}
"""


# --- Pydantic Models ---

CheckStatus = Literal["PASSED", "FAILED", "IN_PROGRESS", "SKIPPED"]
Severity = Literal["WARN", "ERROR"]


class CheckResult(BaseModel):
    """Represents the latest outcome for a quality check."""

    passed: bool
    metadata: dict[str, Any] = {}


class QualityCheck(BaseModel):
    """Represents current status and metadata for one asset quality check."""

    name: str
    asset_key: list[str]
    description: str | None = None
    severity: Severity
    status: CheckStatus
    last_execution_time: str | None = None
    last_result: CheckResult | None = None


class CategoryStats(BaseModel):
    """Represents pass-rate statistics for a quality check category."""

    category: str
    passing: int
    total: int
    percentage: int


class RecentCheckExecution(BaseModel):
    """Represents one recent quality check execution event."""

    asset_key: list[str]
    check_name: str
    timestamp: str
    passed: bool
    run_id: str | None = None
    severity: Severity
    status: CheckStatus
    metadata: dict[str, Any] = {}


class QualityOverview(BaseModel):
    """Aggregates top-level quality metrics for the Observatory overview."""

    total_checks: int
    passing_checks: int
    failing_checks: int
    warning_checks: int
    quality_score: int
    by_category: list[CategoryStats]
    recent_executions: list[RecentCheckExecution] = []
    failing_checks_list: list[QualityCheck] = []


class CheckExecution(BaseModel):
    """Represents a normalized historical execution point for a check."""

    timestamp: str
    passed: bool
    run_id: str | None = None
    metadata: dict[str, Any] = {}


# --- Helper Functions ---


def normalize_status(status: str) -> CheckStatus:
    """Map a raw Dagster check status onto the canonical CheckStatus values."""
    normalized = status.strip().upper()
    if normalized == "SUCCEEDED":
        return "PASSED"
    if normalized == "FAILED":
        return "FAILED"
    if normalized == "IN_PROGRESS":
        return "IN_PROGRESS"
    return "SKIPPED"


def normalize_severity(severity: str | None) -> Severity:
    """Normalize a Dagster severity value; anything but WARN is treated as ERROR."""
    return "WARN" if severity == "WARN" else "ERROR"


def to_epoch_ms(value: str | int | float) -> int:
    """Convert a timestamp value to epoch milliseconds; returns 0 when parsing fails."""
    if isinstance(value, (int, float)):
        if value > 1_000_000_000_000:
            return int(value)
        return int(value * 1000)
    try:
        num = float(value.strip())
        if num > 1_000_000_000_000:
            return int(num)
        return int(num * 1000)
    except ValueError:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            return 0


def to_iso_timestamp(value: str | int | float) -> str:
    """Convert an ISO string or numeric timestamp to a UTC ISO 8601 string."""
    return datetime.fromtimestamp(to_epoch_ms(value) / 1000, tz=timezone.utc).isoformat()


def metadata_entries_to_dict(entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Flatten Dagster metadata entries into a dict keyed by entry label."""
    record: dict[str, Any] = {}
    if not entries:
        return record

    for entry in entries:
        label = entry.get("label")
        if not label:
            continue
        typename = entry.get("__typename", "")
        if typename == "TextMetadataEntry":
            record[label] = entry.get("text")
        elif typename == "IntMetadataEntry":
            record[label] = entry.get("intValue")
        elif typename == "FloatMetadataEntry":
            record[label] = entry.get("floatValue")
        elif typename == "BoolMetadataEntry":
            record[label] = entry.get("boolValue")
        elif typename == "JsonMetadataEntry":
            try:
                record[label] = json.loads(entry.get("jsonString", "{}"))
            except Exception:
                record[label] = entry.get("jsonString")
    return record


async def dagster_query(
    client: httpx.AsyncClient, url: str, query: str, variables: dict[str, Any]
) -> dict[str, Any] | None:
    """Post the GraphQL query and return its data payload, or None on any failure."""
    try:
        response = await client.post(
            url,
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errors"):
            logger.error("dagster_graphql_error", errors=result["errors"])
            return None
        return result.get("data")
    except Exception as exc:
        logger.error("dagster_query_failed", error=str(exc))
        return None


async def fetch_quality_snapshot(dagster_url: str, recent_limit: int = 50) -> dict[str, Any] | None:
    """Fetch assets and check executions from Dagster and aggregate them; None on fetch failure."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Get all assets with their checks
        assets_data = await dagster_query(client, dagster_url, ASSET_CHECKS_QUERY, {})
        if not assets_data:
            return None

        asset_nodes = assets_data.get("assetNodes", [])

        # Filter assets that have checks
        assets_with_checks: list[dict[str, Any]] = []
        for node in asset_nodes:
            checks_or_error = node.get("assetChecksOrError", {})
            if checks_or_error.get("__typename") != "AssetChecks":
                continue
            checks = checks_or_error.get("checks", [])
            if not checks:
                continue
            assets_with_checks.append(
                {
                    "asset_key": node.get("assetKey", {}).get("path", []),
                    "checks": checks,
                }
            )

        # Step 2: Fetch executions for each asset
        total_checks = 0
        passing_checks = 0
        failing_checks = 0
        warning_checks = 0
        latest_checks: list[QualityCheck] = []
        failing_checks_list: list[QualityCheck] = []
        recent_executions: list[RecentCheckExecution] = []

        for asset in assets_with_checks:
            asset_key = asset["asset_key"]
            checks = asset["checks"]
            # Over-fetch executions so every check has at least a few recent
            # runs even when execution history is skewed toward some checks.
            per_asset_limit = max(50, len(checks) * 3)

            exec_data = await dagster_query(
                client,
                dagster_url,
                ASSET_CHECK_EXECUTIONS_QUERY,
                {"assetKey": {"path": asset_key}, "limit": per_asset_limit},
            )
            if not exec_data:
                continue

            executions = exec_data.get("assetCheckExecutions", [])
            total_checks += len(checks)

            # Get newest execution per check
            newest_by_check: dict[str, dict[str, Any]] = {}
            for exec in executions:
                check_name = exec.get("checkName")
                if not check_name:
                    continue
                existing = newest_by_check.get(check_name)
                if not existing or to_epoch_ms(exec["timestamp"]) > to_epoch_ms(
                    existing["timestamp"]
                ):
                    newest_by_check[check_name] = exec

            # Build check records
            for check_def in checks:
                check_name = check_def.get("name")
                latest = newest_by_check.get(check_name)
                if not latest:
                    continue

                status = normalize_status(latest.get("status", ""))
                evaluation = latest.get("evaluation") or {}
                severity = normalize_severity(evaluation.get("severity"))

                check = QualityCheck(
                    name=check_name,
                    asset_key=asset_key,
                    description=check_def.get("description"),
                    severity=severity,
                    status=status,
                    last_execution_time=to_iso_timestamp(latest["timestamp"]),
                    last_result=CheckResult(
                        passed=status == "PASSED",
                        metadata=metadata_entries_to_dict(evaluation.get("metadataEntries")),
                    ),
                )
                latest_checks.append(check)

                if status == "PASSED":
                    passing_checks += 1
                elif status == "FAILED" and severity == "WARN":
                    warning_checks += 1
                elif status == "FAILED":
                    failing_checks += 1
                    failing_checks_list.append(check)

            # Build recent executions
            for exec in executions:
                evaluation = exec.get("evaluation") or {}
                recent_executions.append(
                    RecentCheckExecution(
                        asset_key=asset_key,
                        check_name=exec.get("checkName", ""),
                        timestamp=to_iso_timestamp(exec["timestamp"]),
                        passed=normalize_status(exec.get("status", "")) == "PASSED",
                        run_id=exec.get("runId"),
                        severity=normalize_severity(evaluation.get("severity")),
                        status=normalize_status(exec.get("status", "")),
                        metadata=metadata_entries_to_dict(evaluation.get("metadataEntries")),
                    )
                )

        # Sort recent executions by timestamp desc
        recent_executions.sort(key=lambda e: to_epoch_ms(e.timestamp), reverse=True)

        # Calculate quality score
        # WARN-severity failures count as passing here: only ERROR failures
        # reduce the score.
        evaluated = passing_checks + failing_checks + warning_checks
        quality_score = (
            round(((passing_checks + warning_checks) / evaluated) * 100) if evaluated > 0 else 0
        )

        # Calculate category stats
        categories = [
            ("Contract (Pandera)", lambda c: c.name == "pandera_contract"),
            ("dbt tests", lambda c: c.name.startswith("dbt__")),
            (
                "Custom",
                lambda c: c.name != "pandera_contract" and not c.name.startswith("dbt__"),
            ),
        ]

        by_category: list[CategoryStats] = []
        for cat_name, predicate in categories:
            relevant = [c for c in latest_checks if predicate(c)]
            if not relevant:
                continue
            passing = len([c for c in relevant if c.status == "PASSED"])
            total = len(relevant)
            by_category.append(
                CategoryStats(
                    category=cat_name,
                    passing=passing,
                    total=total,
                    percentage=round((passing / total) * 100) if total > 0 else 0,
                )
            )

        return {
            "total_checks": total_checks,
            "passing_checks": passing_checks,
            "failing_checks": failing_checks,
            "warning_checks": warning_checks,
            "quality_score": quality_score,
            "by_category": by_category,
            "recent_executions": recent_executions[:recent_limit],
            "failing_checks_list": failing_checks_list,
            "latest_checks": latest_checks,
        }


# --- API Endpoints ---


@router.get("/overview", response_model=QualityOverview | dict)
async def get_quality_overview(
    dagster_url: str | None = None,
) -> QualityOverview | dict[str, str]:
    """Aggregate asset checks into a quality score and category breakdown.

    Fetch failures and exceptions are returned as ``{"error": ...}`` dicts.

    """
    url = resolve_dagster_url(dagster_url)

    try:
        snapshot = await fetch_quality_snapshot(url)
        if not snapshot:
            return {"error": "Failed to fetch quality snapshot from Dagster"}

        return QualityOverview(
            total_checks=snapshot["total_checks"],
            passing_checks=snapshot["passing_checks"],
            failing_checks=snapshot["failing_checks"],
            warning_checks=snapshot["warning_checks"],
            quality_score=snapshot["quality_score"],
            by_category=snapshot["by_category"],
            recent_executions=snapshot["recent_executions"],
            failing_checks_list=snapshot["failing_checks_list"],
        )
    except Exception as e:
        logger.exception("Failed to get quality overview")
        return {"error": str(e)}


@router.get("/assets/{asset_key_path:path}/checks", response_model=list[QualityCheck] | dict)
async def get_asset_checks(
    asset_key_path: str,
    dagster_url: str | None = None,
) -> list[QualityCheck] | dict[str, str]:
    """Return the newest execution per check for one asset.

    Fetch failures and exceptions are returned as ``{"error": ...}`` dicts.

    """
    asset_key = asset_key_path.split("/")
    url = resolve_dagster_url(dagster_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json={
                    "query": ASSET_CHECK_EXECUTIONS_QUERY,
                    "variables": {"assetKey": {"path": asset_key}, "limit": 200},
                },
            )
            response.raise_for_status()
            result = response.json()

            if result.get("errors"):
                return {"error": result["errors"][0].get("message", "GraphQL error")}

            executions = result.get("data", {}).get("assetCheckExecutions", [])

            # Group by check name and get newest
            newest_by_check: dict[str, dict[str, Any]] = {}
            for exec in executions:
                check_name = exec.get("checkName")
                if not check_name:
                    continue
                existing = newest_by_check.get(check_name)
                if not existing or to_epoch_ms(exec["timestamp"]) > to_epoch_ms(
                    existing["timestamp"]
                ):
                    newest_by_check[check_name] = exec

            checks = []
            for check_name, exec in sorted(newest_by_check.items()):
                status = normalize_status(exec.get("status", ""))
                evaluation = exec.get("evaluation") or {}
                checks.append(
                    QualityCheck(
                        name=check_name,
                        asset_key=asset_key,
                        severity=normalize_severity(evaluation.get("severity")),
                        status=status,
                        last_execution_time=to_iso_timestamp(exec["timestamp"]),
                        last_result=CheckResult(
                            passed=status == "PASSED",
                            metadata=metadata_entries_to_dict(evaluation.get("metadataEntries")),
                        ),
                    )
                )
            return checks
    except Exception as e:
        logger.exception("Failed to get asset checks")
        return {"error": str(e)}


@router.get(
    "/assets/{asset_key_path:path}/checks/{check_name}/history",
    response_model=list[CheckExecution] | dict,
)
async def get_check_history(
    asset_key_path: str,
    check_name: str,
    limit: int = Query(default=20, le=100),
    dagster_url: str | None = None,
) -> list[CheckExecution] | dict[str, str]:
    """Return recent executions of one asset check, newest first, capped by ``limit``.

    Fetch failures and exceptions are returned as ``{"error": ...}`` dicts.

    """
    asset_key = asset_key_path.split("/")
    url = resolve_dagster_url(dagster_url)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json={
                    "query": ASSET_CHECK_EXECUTIONS_QUERY,
                    "variables": {"assetKey": {"path": asset_key}, "limit": max(50, limit * 3)},
                },
            )
            response.raise_for_status()
            result = response.json()

            if result.get("errors"):
                return {"error": result["errors"][0].get("message", "GraphQL error")}

            all_executions = result.get("data", {}).get("assetCheckExecutions", [])

            # Filter by check name
            executions = [e for e in all_executions if e.get("checkName") == check_name]

            # Sort by timestamp descending
            executions.sort(key=lambda e: to_epoch_ms(e["timestamp"]), reverse=True)

            return [
                CheckExecution(
                    timestamp=to_iso_timestamp(e["timestamp"]),
                    passed=normalize_status(e.get("status", "")) == "PASSED",
                    run_id=e.get("runId"),
                    metadata=metadata_entries_to_dict(
                        (e.get("evaluation") or {}).get("metadataEntries")
                    ),
                )
                for e in executions[:limit]
            ]
    except Exception as e:
        logger.exception("Failed to get check history")
        return {"error": str(e)}


@router.get("/failing", response_model=list[QualityCheck] | dict)
async def get_failing_checks(
    dagster_url: str | None = None,
) -> list[QualityCheck] | dict[str, str]:
    """List currently failing checks from the latest snapshot.

    Fetch failures and exceptions are returned as ``{"error": ...}`` dicts.

    """
    url = resolve_dagster_url(dagster_url)

    try:
        snapshot = await fetch_quality_snapshot(url)
        if not snapshot:
            return {"error": "Failed to fetch quality snapshot from Dagster"}

        return snapshot["failing_checks_list"]
    except Exception as e:
        logger.exception("Failed to get failing checks")
        return {"error": str(e)}
