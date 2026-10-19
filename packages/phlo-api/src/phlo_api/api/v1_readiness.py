"""Authenticated capability readiness. Probes never initialise or modify business state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Literal

import psycopg2
from fastapi import APIRouter, Query, Request
from pydantic import Field

from phlo.config.process import get_process_settings
from phlo_api.api.v1 import _locations, _target
from phlo_api.errors import BadGatewayError, BackendUnavailableError
from phlo_api.observatory_api.v1_preview import (
    PreviewLimitExceeded,
    PreviewUnavailable,
    execute_preview,
    preview_catalog,
    preview_preflight,
)
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 readiness"])


class ReadinessCheck(WireModel):
    state: Literal["configured", "ready", "unavailable", "degraded", "disabled"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str
    remediation: str


class FeatureReadiness(WireModel):
    env: Environment
    observed_at: datetime
    correlation_id: str
    capabilities: dict[str, list[ReadinessCheck]]


def _storage_readiness() -> dict[str, list[ReadinessCheck]]:
    """Validate required columns with zero-row reads in a read-only transaction."""
    queries = {
        "incidents": (
            "SELECT incident_id, env, asset_id, kind, title, status, owner, version, created_at, updated_at, severity, asset_ids, description, notify_qa, pause_downstream, origin FROM phlo.incident LIMIT 0",
            "SELECT * FROM phlo.incident_event LIMIT 0",
            "SELECT * FROM phlo.incident_signal LIMIT 0",
            "SELECT * FROM phlo.incident_command LIMIT 0",
            "SELECT * FROM phlo.incident_subscription LIMIT 0",
            "SELECT * FROM phlo.incident_follow_up LIMIT 0",
            "SELECT * FROM phlo.incident_schema_decision LIMIT 0",
            "SELECT * FROM phlo.asset_incident_policy LIMIT 0",
            "SELECT * FROM phlo.incident_query_evidence LIMIT 0",
            "SELECT * FROM phlo.incident_effect LIMIT 0",
            "SELECT * FROM phlo.query_execution LIMIT 0",
        ),
        "query_usage": (
            "SELECT * FROM phlo.query_usage_event LIMIT 0",
            "SELECT env, table_id, catalog_version, nessie_ref, source_id, query_id, occurred_at, query_state FROM phlo.asset_query_usage LIMIT 0",
        ),
    }
    result: dict[str, list[ReadinessCheck]] = {}
    dsn = get_process_settings().phlo_run_evidence_db_url
    if not dsn:
        return {
            name: [
                ReadinessCheck(
                    state="unavailable",
                    reason_code="configuration_missing",
                    message="Durable storage is not configured.",
                    remediation="Configure PHLO_RUN_EVIDENCE_DB_URL and apply the documented API schema migrations.",
                )
            ]
            for name in queries
        }
    connection = None
    deadline = monotonic() + 5
    try:
        connection = psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=2000")
        connection.set_session(readonly=True)
        for name, statements in queries.items():
            try:
                with connection.cursor() as cursor:
                    for query in statements:
                        if monotonic() >= deadline:
                            raise psycopg2.OperationalError("Readiness probe deadline exceeded.")
                        cursor.execute(query)
                result[name] = [
                    ReadinessCheck(
                        state="ready",
                        reason_code="ready",
                        message="Required storage schema is readable.",
                        remediation="",
                    )
                ]
            except psycopg2.Error as exc:
                missing = exc.pgcode in {"42P01", "42703", "3F000"}
                result[name] = [
                    ReadinessCheck(
                        state="unavailable",
                        reason_code="schema_missing" if missing else "backend_unavailable",
                        message="Required storage schema is missing or outdated."
                        if missing
                        else "Durable storage is unavailable.",
                        remediation="Apply the documented API schema migrations, then check again."
                        if missing
                        else "Check database connectivity and read permissions, then check again.",
                    )
                ]
            finally:
                connection.rollback()
    except (psycopg2.Error, ValueError):
        return {
            name: [
                ReadinessCheck(
                    state="unavailable",
                    reason_code="backend_unavailable",
                    message="Durable storage is unavailable.",
                    remediation="Check database configuration, connectivity and read permissions, then check again.",
                )
            ]
            for name in queries
        }
    finally:
        if connection is not None:
            connection.close()
    return result


async def _orchestration_readiness(location: str) -> ReadinessCheck:
    try:
        locations = await asyncio.wait_for(_locations(), timeout=5)
        loaded = location in locations
        orchestration = ReadinessCheck(
            state="ready" if loaded else "unavailable",
            reason_code="ready" if loaded else "code_location_unavailable",
            message="Configured code location is loaded."
            if loaded
            else "Configured code location is not loaded.",
            remediation=""
            if loaded
            else "Load the configured Dagster code location, then check again.",
        )
    except (BackendUnavailableError, BadGatewayError, TimeoutError):
        orchestration = ReadinessCheck(
            state="unavailable",
            reason_code="backend_unavailable",
            message="Dagster is unavailable.",
            remediation="Check the Dagster service and its configured endpoint, then check again.",
        )
    return orchestration


async def _preview_readiness(request: Request, env: str, ref: str) -> list[ReadinessCheck]:
    checks = [ReadinessCheck.model_validate(check) for check in preview_preflight(env)]
    if any(check.state != "configured" for check in checks):
        return checks
    try:
        result = await asyncio.wait_for(
            execute_preview(
                "SELECT 1 AS phlo_readiness",
                catalog=preview_catalog(env, ref),
                disconnected=request.is_disconnected,
                limit=1,
            ),
            timeout=4,
        )
        if result.get("rows") != [{"phlo_readiness": 1}]:
            raise PreviewUnavailable("Readiness probe returned an invalid result.")
        checks.append(
            ReadinessCheck(
                state="configured",
                reason_code="backend_reachable",
                message="Authenticated preview probe succeeded. Deployment enforcement remains unverified.",
                remediation="Complete the deployment enforcement checks before claiming readiness.",
            )
        )
    except (PreviewUnavailable, PreviewLimitExceeded, TimeoutError):
        checks.append(
            ReadinessCheck(
                state="unavailable",
                reason_code="backend_unavailable",
                message="Authenticated preview probe failed or timed out.",
                remediation="Check preview service reachability, TLS trust and credentials, then check again.",
            )
        )
    return checks


@router.get("/readiness", response_model=FeatureReadiness)
async def v1_readiness(request: Request, env: Annotated[Environment, Query()]) -> FeatureReadiness:
    try:
        target = _target(request, env)
    except BackendUnavailableError:
        return FeatureReadiness(
            env=env,
            observed_at=datetime.now(UTC),
            correlation_id=request.state.request_id,
            capabilities={
                "environment": [
                    ReadinessCheck(
                        state="unavailable",
                        reason_code="configuration_invalid",
                        message="Environment configuration is missing or invalid.",
                        remediation="Configure environments and default_environment in phlo.yaml, then check again. A runtime override must contain the configured default.",
                    )
                ]
            },
        )
    orchestration, storage, preview = await asyncio.gather(
        _orchestration_readiness(target.dagster_location),
        asyncio.to_thread(_storage_readiness),
        _preview_readiness(request, env, target.nessie_ref),
    )
    return FeatureReadiness(
        env=env,
        observed_at=datetime.now(UTC),
        correlation_id=request.state.request_id,
        capabilities={
            "orchestration": [orchestration],
            **storage,
            "query_preview": preview,
        },
    )
