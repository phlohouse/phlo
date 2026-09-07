"""Dagster event-log adapter for the provider-neutral run reconciler.

Translates Dagster runs and event-log records into core evidence types so
core code never imports Dagster. Message payloads are redacted before use,
and stage ids hash deterministically from run, asset, and attempt identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from phlo.capabilities import ResourceRef
from phlo.run_evidence import (
    EvidenceCompleteness,
    RunEvent,
    RunEvidenceUnavailable,
    RunLookupOutcome,
    RunObservation,
    RunStage,
)
from phlo.run_evidence.redaction import payload_checksum, redact_payload


def _datetime_from_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _status(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    normalized = str(raw).lower()
    return {
        "failure": "failed",
        "succeeded": "success",
        "canceled": "cancelled",
    }.get(normalized, normalized)


def _attempt(instance: Any, run: Any) -> int:
    tags = getattr(run, "tags", {}) or {}
    explicit_attempt: int | None = None
    for key in ("phlo/attempt", "dagster/attempt", "attempt"):
        try:
            value = int(tags[key])
            if value > 0:
                explicit_attempt = value
                break
        except (KeyError, TypeError, ValueError):
            pass
    attempt = 1
    # No tag records the attempt number, so derive it by walking Dagster's
    # retry parent chain; the chain length is the only authoritative count.
    # A cycle or missing parent fails reconciliation rather than guessing.
    parent_id = getattr(run, "parent_run_id", None)
    seen: set[str] = {str(run.run_id)}
    while parent_id:
        parent_id = str(parent_id)
        if parent_id in seen:
            raise RunEvidenceUnavailable(
                "Dagster retry-chain parent cycle was detected; reconciliation did not change the run"
            )
        seen.add(parent_id)
        try:
            parent = instance.get_run_by_id(parent_id)
        except Exception as exc:
            raise RunEvidenceUnavailable(
                "Dagster retry-chain lookup was unavailable; reconciliation did not change the run"
            ) from exc
        if parent is None:
            raise RunEvidenceUnavailable(
                "Dagster retry-chain parent was missing; reconciliation did not change the run"
            )
        attempt += 1
        parent_id = getattr(parent, "parent_run_id", None)
    if explicit_attempt is not None and attempt > 1 and explicit_attempt != attempt:
        raise RunEvidenceUnavailable(
            "Dagster retry-chain attempt tag disagreed with its parent chain; "
            "reconciliation did not change the run"
        )
    return explicit_attempt or attempt


def _asset_name(event: Any) -> str | None:
    dagster_event = getattr(event, "dagster_event", None)
    asset_key = getattr(dagster_event, "asset_key", None)
    path = getattr(asset_key, "path", None)
    return ".".join(str(part) for part in path) if path else None


def _check_identity(event: Any) -> str | None:
    dagster_event = getattr(event, "dagster_event", None)
    evaluation = getattr(dagster_event, "asset_check_evaluation", None)
    return (
        str(
            getattr(event, "check_name", None)
            or getattr(evaluation, "check_name", None)
            or getattr(evaluation, "check_key", None)
            or getattr(evaluation, "name", None)
            or ""
        )
        or None
    )


def _logical_run_id(run: Any, resolver: Callable[[Any], str] | None) -> str:
    if resolver is not None:
        value = resolver(run)
        if not value.strip():
            raise ValueError("Dagster logical run ID resolver returned an empty ID")
        return value
    tags = getattr(run, "tags", {}) or {}
    return str(tags.get("phlo/run_id") or getattr(run, "root_run_id", None) or run.run_id)


def _safe_message(entry: Any) -> tuple[str, str]:
    raw = str(getattr(entry, "message", "") or "")
    bounded = raw[:512]
    safe = redact_payload(bounded)
    return str(safe), payload_checksum(raw)


def _stage_id(
    logical_run_id: str,
    attempt: int,
    stage_type: str,
    *,
    step_key: str | None,
    asset: str | None,
    partition: str | None,
    check_identity: str | None,
    storage_id: Any,
) -> str:
    identity = [
        str(value)
        for value in (
            logical_run_id,
            attempt,
            stage_type,
            step_key,
            asset,
            partition,
            check_identity,
        )
    ]
    # Materializations include the storage ID because one run can
    # materialize the same asset several times; steps and checks are unique
    # per (step, asset) within an attempt.
    if stage_type == "materialization":
        identity.append(str(storage_id))
    return hashlib.sha256("\0".join(identity).encode()).hexdigest()[:32]


class DagsterRunEvidenceSource:
    """Read explicit Dagster run/event records without storing Dagster types in core.

    ``heartbeat_resolver`` is intentionally opt-in because Dagster's run-record
    update timestamp is metadata freshness, not worker liveness.
    """

    name = "dagster"

    def __init__(
        self,
        instance: Any,
        *,
        project_id: str,
        heartbeat_resolver: Callable[[Any], datetime | None] | None = None,
        logical_run_id_resolver: Callable[[Any], str] | None = None,
    ) -> None:
        self.instance = instance
        self.project_id = project_id
        self.heartbeat_resolver = heartbeat_resolver
        self.logical_run_id_resolver = logical_run_id_resolver

    def _event_records(self, run_id: str) -> list[tuple[Any, Any]]:
        try:
            if not hasattr(self.instance, "get_records_for_run"):
                return [
                    (entry, getattr(entry, "storage_id", None))
                    for entry in self.instance.get_event_log_entries(run_id=run_id)
                ]
            records: list[tuple[Any, Any]] = []
            # Dagster pagination can return a stale cursor; detect a
            # non-advancing loop and fail closed rather than spin forever.
            cursor = None
            seen_cursors: set[Any] = set()
            while True:
                if cursor in seen_cursors:
                    raise RunEvidenceUnavailable(
                        "Dagster event-log pagination cursor repeated; reconciliation did not change the run"
                    )
                seen_cursors.add(cursor)
                kwargs: dict[str, Any] = {"run_id": run_id, "ascending": True}
                if cursor is not None:
                    kwargs["cursor"] = cursor
                connection = self.instance.get_records_for_run(**kwargs)
                page = list(getattr(connection, "records", connection))
                records.extend(
                    (
                        getattr(record, "event_log_entry", record),
                        getattr(record, "storage_id", None),
                    )
                    for record in page
                )
                next_cursor = getattr(connection, "cursor", None)
                if not getattr(connection, "has_more", False):
                    return records
                if next_cursor in seen_cursors:
                    raise RunEvidenceUnavailable(
                        "Dagster event-log pagination cursor repeated; reconciliation did not change the run"
                    )
                if next_cursor is None or next_cursor == cursor:
                    raise RunEvidenceUnavailable(
                        "Dagster event-log pagination did not advance; reconciliation did not change the run"
                    )
                cursor = next_cursor
        except RunEvidenceUnavailable:
            raise
        except Exception as exc:
            raise RunEvidenceUnavailable(
                "Dagster event-log lookup was unavailable; reconciliation did not change the run"
            ) from exc

    def observe_run(self, project_id: str, run_id: str) -> RunObservation:
        """Observe one Dagster run; unknown projects raise, absent runs report MISSING evidence."""
        if project_id != self.project_id:
            raise ValueError("Dagster event source is configured for another project")
        try:
            run = self.instance.get_run_by_id(run_id)
        except Exception as exc:
            raise RunEvidenceUnavailable(
                "Dagster run lookup was unavailable; reconciliation did not change the run"
            ) from exc
        if run is None:
            return RunObservation(
                project_id=project_id,
                run_id=run_id,
                source=self.name,
                evidence_state=EvidenceCompleteness.MISSING,
                lookup_outcome=RunLookupOutcome.ABSENT,
            )

        logical_run_id = _logical_run_id(run, self.logical_run_id_resolver)
        attempt = _attempt(self.instance, run)
        try:
            run_record = self.instance.get_run_record_by_id(run_id)
        except Exception as exc:
            raise RunEvidenceUnavailable(
                "Dagster run-record lookup was unavailable; reconciliation did not change the run"
            ) from exc
        entries = self._event_records(run_id)
        started_at = _datetime_from_epoch(
            getattr(run_record, "start_time", None)
            if run_record
            else getattr(run, "start_time", None)
        )
        finished_at = _datetime_from_epoch(
            getattr(run_record, "end_time", None) if run_record else getattr(run, "end_time", None)
        )
        tags = getattr(run, "tags", {}) or {}
        run_status = _status(getattr(run, "status", None))
        tagged_no_data = str(tags.get("phlo/no_data", "")).lower() in {"1", "true", "yes"}
        successful_event = any(
            str(
                getattr(
                    getattr(entry, "event_type", None),
                    "value",
                    getattr(entry, "event_type", ""),
                )
            )
            in {"RUN_SUCCESS", "PIPELINE_SUCCESS"}
            for entry, _ in entries
        )
        successful_terminal = run_status in {"success", "no_data"} or (
            run_status not in {"failed", "error", "cancelled", "canceled", "skipped", "abandoned"}
            and successful_event
        )
        no_data = tagged_no_data and successful_terminal
        run_resource_ref = ResourceRef(
            resource_type="run",
            resource_id=logical_run_id,
            tenant=project_id,
            attributes={"attempt": str(attempt)},
        )
        events: list[RunEvent] = []
        stages: list[RunStage] = []
        for index, (entry, record_storage_id) in enumerate(entries):
            observed_at = _datetime_from_epoch(getattr(entry, "timestamp", None))
            if observed_at is None:
                continue
            event_type = getattr(entry, "event_type", None)
            event_name = str(getattr(event_type, "value", event_type) or "UNKNOWN")
            storage_id = record_storage_id or getattr(entry, "storage_id", None)
            dagster_event = getattr(entry, "dagster_event", None)
            step_key = getattr(entry, "step_key", None) or getattr(dagster_event, "step_key", None)
            partition = (
                getattr(entry, "partition", None)
                or getattr(dagster_event, "partition", None)
                or tags.get("dagster/partition")
            )
            stage_status = {
                "STEP_START": "running",
                "STEP_SUCCESS": "success",
                "STEP_FAILURE": "failed",
                "STEP_SKIPPED": "skipped",
                "STEP_UP_FOR_RETRY": "retrying",
                "STEP_RESTARTED": "running",
            }.get(event_name)
            # SQLite event storage numbers records independently per provider run.
            # Keep the payload/stage identities unchanged: upgraded reconciliation
            # retains legacy bare-ID rows and adds scoped rows once, without
            # changing stage or terminal state on subsequent replay.
            event_id = str(
                f"{run_id}:{storage_id}"
                if storage_id is not None
                else hashlib.sha256(f"{run_id}\0{event_name}\0{index}".encode()).hexdigest()[:32]
            )
            provider_event_status = {
                "RUN_SUCCESS": "success",
                "PIPELINE_SUCCESS": "success",
                "RUN_FAILURE": "failed",
                "PIPELINE_FAILURE": "failed",
                "RUN_CANCELED": "cancelled",
                "PIPELINE_CANCELED": "cancelled",
            }.get(event_name)
            event_status = (
                "no_data"
                if no_data and provider_event_status == "success"
                else provider_event_status
            )
            if event_name in {"RUN_START", "PIPELINE_START"}:
                normalized_type = "run.start"
            elif event_status is not None:
                normalized_type = "run.terminal"
            elif event_name in {"RUN_ENQUEUED", "PIPELINE_ENQUEUED"}:
                normalized_type = "run.queued"
            elif event_name in {"RUN_STARTING", "PIPELINE_STARTING"}:
                normalized_type = "run.starting"
            elif event_name in {"RUN_CANCELING", "PIPELINE_CANCELING"}:
                normalized_type = "run.canceling"
            elif event_name in {
                "STEP_START",
                "STEP_SUCCESS",
                "STEP_FAILURE",
                "STEP_SKIPPED",
                "STEP_UP_FOR_RETRY",
                "STEP_RESTARTED",
            }:
                normalized_type = "stage.step"
            elif event_name == "ASSET_MATERIALIZATION":
                normalized_type = "stage.materialization"
            elif event_name == "ASSET_CHECK_EVALUATION":
                normalized_type = "stage.check"
            else:
                normalized_type = f"dagster.{event_name.lower()}"
            if normalized_type == "stage.materialization":
                stage_status = "success"
                stage_type = "materialization"
            elif normalized_type == "stage.check":
                stage_type = "check"
                stage_status = (
                    "success"
                    if getattr(
                        getattr(dagster_event, "asset_check_evaluation", None), "passed", False
                    )
                    else "failed"
                )
            elif normalized_type == "stage.step":
                stage_type = "dagster_step"
            else:
                stage_type = None
            asset = _asset_name(entry)
            check_identity = _check_identity(entry) if stage_type == "check" else None
            stage_id = (
                _stage_id(
                    logical_run_id,
                    attempt,
                    stage_type,
                    step_key=step_key,
                    asset=asset,
                    partition=partition,
                    check_identity=check_identity,
                    storage_id=storage_id,
                )
                if stage_type and (step_key or asset)
                else None
            )
            message, message_checksum = _safe_message(entry)
            payload = {
                "provider_event_type": event_name,
                "message_summary": message,
                "message_checksum": message_checksum,
                "storage_id": storage_id,
                "asset": asset,
                "stage_id": stage_id,
                "check_identity": check_identity,
                "status": event_status,
                "provider_status": provider_event_status,
            }
            events.append(
                RunEvent(
                    project_id=project_id,
                    run_id=logical_run_id,
                    event_id=event_id,
                    event_type=normalized_type,
                    producer="dagster",
                    payload=payload,
                    stage_id=stage_id,
                    observed_at=observed_at,
                    sequence=int(storage_id) if isinstance(storage_id, int) else index,
                    attempt=attempt,
                    resource_ref=run_resource_ref,
                )
            )
            if stage_id is not None and stage_type is not None:
                stages.append(
                    RunStage(
                        project_id=project_id,
                        run_id=logical_run_id,
                        stage_id=stage_id,
                        stage_type=stage_type,
                        provider="dagster",
                        tool="dagster",
                        asset=asset,
                        attempt=attempt,
                        status=stage_status or "unknown",
                        started_at=observed_at,
                        finished_at=observed_at
                        if stage_status not in {"running", "retrying"}
                        else None,
                        error=message if stage_status == "failed" else None,
                        resource_ref=run_resource_ref,
                    )
                )
        if no_data:
            tag_timestamp = finished_at or started_at
            if tag_timestamp is not None:
                events.append(
                    RunEvent(
                        project_id=project_id,
                        run_id=logical_run_id,
                        event_id=hashlib.sha256(f"{run_id}\0no_data".encode()).hexdigest()[:32],
                        event_type="run.no_data",
                        producer="dagster",
                        payload={"status": "no_data", "source": "phlo/no_data"},
                        observed_at=tag_timestamp,
                        attempt=attempt,
                        resource_ref=run_resource_ref,
                    )
                )
            run_status = "no_data"
        heartbeat = self.heartbeat_resolver(run) if self.heartbeat_resolver else None
        return RunObservation(
            project_id=project_id,
            run_id=logical_run_id,
            attempt=attempt,
            pipeline_name=getattr(run, "job_name", None),
            provider="dagster",
            provider_run_id=run_id,
            status=run_status,
            started_at=started_at,
            finished_at=finished_at,
            heartbeat_at=heartbeat,
            source=self.name,
            events=tuple(events),
            stages=tuple(stages),
        )


__all__ = ["DagsterRunEvidenceSource"]
