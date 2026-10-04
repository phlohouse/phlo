"""Forward environment-mapped Dagster asset-check failures to Phlo incidents."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import dagster as dg
import requests

from phlo.security.mode import requires_http_authorization
from phlo.security.service_identity import (
    build_scoped_service_headers,
    build_service_headers,
    load_service_identity_credentials,
)

_LOCATION_ENV_MAP = "PHLO_DAGSTER_INCIDENT_LOCATION_ENV_MAP"
_API_URL = "PHLO_INCIDENT_API_URL"
_PAGE_SIZE = 100
_MATERIALIZATION_PAGE_SIZE = 100


def _location_environments(raw: str | None) -> dict[str, str]:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_LOCATION_ENV_MAP} must be a JSON object") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{_LOCATION_ENV_MAP} must be a non-empty JSON object")
    if any(
        not isinstance(location, str)
        or not location
        or not isinstance(environment, str)
        or environment not in {"prod", "staging"}
        for location, environment in value.items()
    ):
        raise ValueError(f"{_LOCATION_ENV_MAP} maps location names to prod or staging")
    return value


def _service_headers() -> dict[str, str]:
    if requires_http_authorization():
        return build_scoped_service_headers(
            "phlo-orchestration",
            audience="phlo-api",
            scp=("api:orchestrate",),
            credentials=load_service_identity_credentials(),
            initiator="dagster-incident-sensor",
            correlation_id=None,
        )
    return build_service_headers("phlo-orchestration", initiator="dagster-incident-sensor")


def _metadata_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _failure(entry: Any) -> dict[str, Any] | None:
    dagster_event = getattr(entry, "dagster_event", None)
    evaluation = getattr(dagster_event, "asset_check_evaluation", None)
    if evaluation is None or bool(getattr(evaluation, "passed", True)):
        return None
    asset_key = getattr(dagster_event, "asset_key", None)
    path = getattr(asset_key, "path", None)
    if not path:
        raise ValueError("Failed asset-check event has no asset key")
    asset_id = ".".join(str(part) for part in path)
    metadata = {
        str(key): _metadata_value(value)
        for key, value in (getattr(evaluation, "metadata", None) or {}).items()
    }
    source = metadata.get("source")
    is_dlt_contract = path[0].startswith("dlt_") and source in {"domain", "pandera"}
    severity = str(getattr(getattr(evaluation, "severity", None), "value", "error")).lower()
    if not is_dlt_contract and severity == "warn":
        return None
    violation = metadata.get("violation")
    evidence = {
        "dagster_run_id": getattr(entry, "run_id", None),
        "check_name": str(getattr(evaluation, "check_name", "unknown")),
        "partition_key": getattr(evaluation, "partition", None),
        "severity": severity,
        "source": source,
    }
    if is_dlt_contract:
        evidence["violation"] = violation
        evidence["schema"] = metadata.get("schema")
    return {
        "asset_id": asset_id,
        "kind": "dlt_contract_violation" if is_dlt_contract else "failed_check",
        "title": (
            f"dlt contract violation: {asset_id}"
            if is_dlt_contract
            else f"Asset check failed: {asset_id}"
        ),
        "evidence": evidence,
    }


def _run_location(run: Any) -> str:
    origin = getattr(run, "remote_job_origin", None)
    repository_origin = getattr(origin, "repository_origin", None)
    location_origin = getattr(repository_origin, "code_location_origin", None)
    location_name = getattr(location_origin, "location_name", None)
    if not isinstance(location_name, str) or not location_name:
        raise ValueError("Dagster run has no repository code-location identity")
    return location_name


def _send_incident(api_url: str, environment: str, signal_id: str, payload: dict[str, Any]) -> None:
    body = {**payload, "evidence_id": signal_id}
    response = requests.post(
        f"{api_url.rstrip('/')}/api/v1/incidents?{urlencode({'env': environment})}",
        json=body,
        headers={**_service_headers(), "Idempotency-Key": signal_id},
        timeout=30,
    )
    response.raise_for_status()


def _asset_policies(api_url: str, environment: str) -> list[dict[str, Any]]:
    cursor = None
    seen_cursors: set[str] = set()
    policies: list[dict[str, Any]] = []
    while True:
        params: dict[str, str | int] = {"env": environment, "limit": 500}
        if cursor is not None:
            params["cursor"] = cursor
        response = requests.get(
            f"{api_url.rstrip('/')}/api/v1/incident-policies",
            params=params,
            headers=_service_headers(),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("env") != environment:
            raise ValueError("Incident policy response has an unexpected environment")
        items = payload.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("Incident policy response has an invalid items list")
        policies.extend(items)
        cursor = payload.get("next_cursor")
        if cursor is None:
            return policies
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise ValueError("Incident policy pagination cursor did not advance")
        seen_cursors.add(cursor)


def _asset_id_for_key(asset_key: dg.AssetKey) -> str:
    return ".".join(asset_key.path)


def _asset_key_for_policy(context: dg.SensorEvaluationContext, asset_id: str) -> dg.AssetKey | None:
    repository = context.repository_def
    if repository is None:
        return None
    graph = repository.asset_graph
    matches = [key for key in graph.get_all_asset_keys() if _asset_id_for_key(key) == asset_id]
    if len(matches) != 1:
        return None
    key = matches[0]
    if graph.get(key).is_partitioned:
        return None
    return key


def _latest_successful_materialization(
    context: dg.SensorEvaluationContext,
    asset_key: dg.AssetKey,
    environment: str,
    environments: dict[str, str],
) -> dict[str, Any] | None:
    before_cursor = None
    for _ in range(10):
        records = context.instance.get_event_records(
            dg.EventRecordsFilter(
                event_type=dg.DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=asset_key,
                before_cursor=before_cursor,
            ),
            limit=_MATERIALIZATION_PAGE_SIZE,
            ascending=False,
        )
        if not records:
            return None
        storage_ids: list[int] = []
        for record in records:
            storage_id = getattr(record, "storage_id", None)
            entry = getattr(record, "event_log_entry", None)
            if not isinstance(storage_id, int) or entry is None:
                raise ValueError("Dagster materialization record is incomplete")
            storage_ids.append(storage_id)
            run_id = getattr(entry, "run_id", None)
            if not isinstance(run_id, str) or not run_id:
                raise ValueError("Dagster materialization has no linked run ID")
            run = context.instance.get_run_by_id(run_id)
            if run is None:
                raise ValueError(f"Dagster materialization run {run_id!r} is unavailable")
            location = _run_location(run)
            run_environment = environments.get(location)
            if run_environment is None:
                raise ValueError(
                    f"Dagster location {location!r} has no explicit incident environment"
                )
            if run_environment != environment:
                continue
            if str(getattr(getattr(run, "status", None), "value", "")).upper() != "SUCCESS":
                continue
            dagster_event = getattr(entry, "dagster_event", None)
            materialization = getattr(dagster_event, "asset_materialization", None)
            if getattr(materialization, "partition", None) is not None:
                return None
            timestamp = getattr(entry, "timestamp", None)
            if not isinstance(timestamp, (int, float)):
                raise ValueError("Successful Dagster materialization has no timestamp")
            return {
                "location": location,
                "storage_id": storage_id,
                "run_id": run_id,
                "materialized_at": datetime.fromtimestamp(timestamp, UTC),
            }
        if len(records) < _MATERIALIZATION_PAGE_SIZE:
            return None
        next_cursor = min(storage_ids)
        if before_cursor is not None and next_cursor >= before_cursor:
            raise ValueError("Dagster materialization pagination cursor did not advance")
        before_cursor = next_cursor
    return None


def _detect_freshness_breaches(
    context: dg.SensorEvaluationContext,
    api_url: str,
    environments: dict[str, str],
) -> None:
    for environment in sorted(set(environments.values())):
        for policy in _asset_policies(api_url, environment):
            asset_id = policy.get("asset_id")
            sla_seconds = policy.get("freshness_sla_seconds")
            if not isinstance(asset_id, str) or type(sla_seconds) is not int or sla_seconds <= 0:
                continue
            asset_key = _asset_key_for_policy(context, asset_id)
            if asset_key is None:
                continue
            evidence = _latest_successful_materialization(
                context, asset_key, environment, environments
            )
            if evidence is None:
                continue
            age_seconds = (datetime.now(UTC) - evidence["materialized_at"]).total_seconds()
            if age_seconds <= sla_seconds:
                continue
            signal_id = (
                f"dagster-freshness:{environment}:{evidence['location']}:"
                f"{asset_id}:{evidence['storage_id']}"
            )
            _send_incident(
                api_url,
                environment,
                signal_id,
                {
                    "asset_id": asset_id,
                    "kind": "freshness_breach",
                    "title": f"Freshness SLA breached: {asset_id}",
                    "evidence": {
                        "repository_location": evidence["location"],
                        "successful_run_id": evidence["run_id"],
                        "materialization_event_id": evidence["storage_id"],
                        "last_successful_materialization_at": evidence[
                            "materialized_at"
                        ].isoformat(),
                        "freshness_sla_seconds": sla_seconds,
                    },
                },
            )


@dg.sensor(
    name="phlo_incident_signal_sensor",
    description="Create durable incidents from environment-mapped failed Dagster asset checks.",
    minimum_interval_seconds=60,
)
def phlo_incident_signal_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
    """Forward failed check events; only advance the cursor after successful delivery."""
    environments = _location_environments(os.getenv(_LOCATION_ENV_MAP))
    api_url = os.getenv(_API_URL)
    if not environments or not api_url:
        return dg.SensorResult(
            skip_reason=dg.SkipReason(f"Configure {_LOCATION_ENV_MAP} and {_API_URL}.")
        )

    try:
        after_cursor = int(context.cursor) if context.cursor else None
    except ValueError as exc:
        raise ValueError("Incident sensor cursor is not an event storage ID") from exc
    if after_cursor is None:
        existing = context.instance.get_event_records(
            dg.EventRecordsFilter(event_type=dg.DagsterEventType.ASSET_CHECK_EVALUATION),
            limit=1,
            ascending=False,
        )
        if existing:
            storage_id = getattr(existing[0], "storage_id", None)
            if not isinstance(storage_id, int):
                raise ValueError("Dagster check event has no integer storage ID")
            after_cursor = storage_id
        else:
            after_cursor = 0
    records = context.instance.get_event_records(
        dg.EventRecordsFilter(
            event_type=dg.DagsterEventType.ASSET_CHECK_EVALUATION,
            after_cursor=after_cursor,
        ),
        limit=_PAGE_SIZE,
        ascending=True,
    )

    last_storage_id = after_cursor
    for record in records:
        storage_id = getattr(record, "storage_id", None)
        if not isinstance(storage_id, int):
            raise ValueError("Dagster check event has no integer storage ID")
        entry = getattr(record, "event_log_entry", None)
        if entry is None:
            raise ValueError("Dagster check event has no event-log entry")
        failure = _failure(entry)
        if failure is not None:
            run_id = failure["evidence"].get("dagster_run_id")
            if not isinstance(run_id, str) or not run_id:
                raise ValueError("Dagster failed-check event has no run ID")
            run = context.instance.get_run_by_id(run_id)
            if run is None:
                raise ValueError(f"Dagster failed-check run {run_id!r} is unavailable")
            location = _run_location(run)
            environment = environments.get(location)
            if environment is None:
                raise ValueError(
                    f"Dagster location {location!r} has no explicit incident environment"
                )
            signal_id = f"dagster-check:{location}:{storage_id}"
            _send_incident(api_url, environment, signal_id, failure)
        last_storage_id = storage_id

    _detect_freshness_breaches(context, api_url, environments)
    return dg.SensorResult(cursor=str(last_storage_id) if last_storage_id is not None else None)
