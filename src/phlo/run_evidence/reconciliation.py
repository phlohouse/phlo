"""Provider-neutral reconciliation of durable pipeline-run evidence.

Evaluates run state from explicit provider observations plus durable store
records only; a provider outage raises RunEvidenceUnavailable so no run
state is ever changed on missing evidence. Evidence degradation uses max
precedence (missing/expired/redacted override complete), and the reconciler
commits decisions through one transactional store call.
Imported across the phlo.run_evidence package (store, report) as its reconciliation core.
Builds on phlo.run_evidence.models and redaction helpers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from phlo.run_evidence.models import (
    EvidenceCompleteness,
    RunEvent,
    RunStage,
)
from phlo.run_evidence.redaction import canonical_json, payload_checksum

TERMINAL_STATUSES = frozenset(
    {
        "success",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "skipped",
        "no_data",
        "abandoned",
    }
)
NONTERMINAL_STATUSES = frozenset(
    {"queued", "not_started", "starting", "started", "running", "canceling"}
)
DEFAULT_CLOCK_SKEW = timedelta(seconds=60)
# Ranks evidence degradation severity; _strongest_evidence_state takes the max.
# COMPLETE and INCOMPLETE tie at 0 because neither is a degradation: only
# missing, expired, or redacted records override an otherwise complete verdict.
EVIDENCE_STATE_PRECEDENCE = {
    EvidenceCompleteness.COMPLETE: 0,
    EvidenceCompleteness.INCOMPLETE: 0,
    EvidenceCompleteness.MISSING: 1,
    EvidenceCompleteness.EXPIRED: 2,
    EvidenceCompleteness.REDACTED: 3,
}


class RunLookupOutcome(StrEnum):
    """Whether the provider returned a run record or authoritatively had none."""

    PRESENT = "present"
    ABSENT = "absent"


class RunEvidenceUnavailable(RuntimeError):
    """The provider could not be queried, so no run state may be changed."""


class RunEvidenceNotFound(LookupError):
    """The provider authoritatively has no run and the store has no durable parent."""


@dataclass(frozen=True, slots=True)
class RequiredEvidenceStage:
    """Durable records required for one provider/pipeline stage."""

    stage_type: str
    provider: str | None = None
    required_event_types: tuple[str, ...] = ()
    required_status: str | None = None
    allowed_statuses: tuple[str, ...] = ()
    allow_no_data: bool = False

    def __post_init__(self) -> None:
        if not self.stage_type.strip():
            raise ValueError("stage_type must be a stable non-empty identifier")
        if self.required_status and self.allowed_statuses:
            raise ValueError("required_status and allowed_statuses are mutually exclusive")


@dataclass(frozen=True, slots=True)
class RequiredEvidenceRecord:
    """A count/status requirement for an existing durable record family."""

    family: str
    minimum: int = 1
    required_status: str | None = None

    def __post_init__(self) -> None:
        if self.family not in {"resource", "catalog_change", "quality_result", "artifact"}:
            raise ValueError("unsupported required evidence record family")
        if self.minimum <= 0:
            raise ValueError("minimum required evidence must be positive")
        if self.required_status is None:
            return
        allowed_statuses = {
            "artifact": {"complete", "incomplete", "missing", "expired", "redacted"},
            "quality_result": {"passed", "failed"},
            "catalog_change": {"merged", "failed", "conflict", "skipped", "cancelled"},
        }.get(self.family)
        if allowed_statuses is None:
            raise ValueError("resource records do not expose a supported required status")
        if self.required_status.strip().lower() not in allowed_statuses:
            raise ValueError(
                f"unsupported required status {self.required_status!r} for {self.family}"
            )


@dataclass(frozen=True, slots=True)
class RequiredEvidenceProfile:
    """Versioned evidence requirements selected for a pipeline/provider."""

    profile_id: str
    version: str
    pipeline_name: str | None = None
    provider: str | None = None
    stages: tuple[RequiredEvidenceStage, ...] = ()
    run_terminal_event_types: tuple[str, ...] = ("run.terminal",)
    required_run_fields: tuple[str, ...] = (
        "status",
        "started_at",
        "finished_at",
        "provider_run_id",
    )
    required_records: tuple[RequiredEvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("profile_id and version must be non-empty")


@dataclass(frozen=True, slots=True)
class RunObservation:
    """A provider adapter's explicit, durable observation of one run."""

    project_id: str
    run_id: str
    attempt: int = 1
    pipeline_name: str | None = None
    provider: str | None = None
    provider_run_id: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    source: str = "unknown"
    events: tuple[RunEvent, ...] = ()
    stages: tuple[RunStage, ...] = ()
    evidence_state: EvidenceCompleteness | None = None
    lookup_outcome: RunLookupOutcome = RunLookupOutcome.PRESENT

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.run_id.strip():
            raise ValueError("project_id and run_id must be non-empty")
        if self.attempt <= 0:
            raise ValueError("attempt must be positive")
        for value in (self.started_at, self.finished_at, self.heartbeat_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("observation timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    """An immutable, auditable result for one evidence snapshot."""

    decision_id: str
    project_id: str
    run_id: str
    attempt: int
    profile_id: str
    profile_version: str
    status: str
    evidence_completeness: EvidenceCompleteness
    reason: str
    missing_evidence: tuple[str, ...]
    evidence_checksum: str
    observed_event_count: int
    source: str
    heartbeat_at: datetime | None
    stale_after_seconds: int | None
    decided_at: datetime
    finished_at: datetime | None = None


class RunEvidenceSource(Protocol):
    """Injectable provider boundary for explicit run/event observations."""

    def observe_run(self, project_id: str, run_id: str) -> RunObservation:
        """Return an observation; raise unavailable on outage and mark authoritative absence missing."""


def normalize_status(status: str | None) -> str | None:
    """Normalize a provider status alias to the canonical status vocabulary."""
    if status is None:
        return None
    value = status.strip().lower()
    return {
        "canceled": "cancelled",
        "failure": "failed",
        "succeeded": "success",
    }.get(value, value)


def _strongest_evidence_state(
    *states: EvidenceCompleteness | None,
) -> EvidenceCompleteness | None:
    available = [state for state in states if state is not None]
    return max(available, key=lambda state: EVIDENCE_STATE_PRECEDENCE[state], default=None)


def _event_is_no_data(event: dict[str, Any]) -> bool:
    if event.get("event_type") in {"run.no_data", "pipeline.no_data"}:
        return True
    try:
        payload = event.get("payload", "{}")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        return isinstance(payload, dict) and payload.get("no_data") is True
    except (TypeError, ValueError):
        return False


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    if isinstance(payload, str):
        try:
            import json

            payload = json.loads(payload)
        except (TypeError, ValueError):
            return {}
    return payload if isinstance(payload, dict) else {}


def _event_status(event: dict[str, Any]) -> str | None:
    payload = _event_payload(event)
    return normalize_status(payload.get("status") or payload.get("run_status"))


def _row_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _record_status(family: str, row: dict[str, Any]) -> str | None:
    if family == "artifact":
        return normalize_status(row.get("status"))
    if family == "quality_result":
        passed = row.get("passed")
        if passed in (True, 1, "1", "true", "True"):
            return "passed"
        if passed in (False, 0, "0", "false", "False"):
            return "failed"
        return None
    if family == "catalog_change":
        return normalize_status(row.get("merge_outcome"))
    return None


def _latest_heartbeat(observation: RunObservation, events: list[dict[str, Any]]) -> datetime | None:
    # Heartbeats come from the provider observation only; stored events never
    # supply one. The parameter keeps call sites uniform across evidence kinds.
    del events
    return observation.heartbeat_at


def evaluate_reconciliation(
    *,
    observation: RunObservation,
    profile: RequiredEvidenceProfile,
    run_row: dict[str, Any] | None,
    event_rows: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]],
    record_rows: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime,
    stale_after: timedelta | None,
    clock_skew: timedelta = DEFAULT_CLOCK_SKEW,
) -> ReconciliationDecision:
    """Evaluate only explicit provider state and durable records."""
    if not now.tzinfo:
        raise ValueError("reconciliation time must be timezone-aware")
    if clock_skew < timedelta(0):
        raise ValueError("clock skew must not be negative")
    if profile.pipeline_name and observation.pipeline_name != profile.pipeline_name:
        raise ValueError("evidence profile does not match the observed pipeline")
    if profile.provider and observation.provider != profile.provider:
        raise ValueError("evidence profile does not match the observed provider")

    attempt_events = [
        row for row in event_rows if int(row.get("attempt", 1)) == observation.attempt
    ]
    attempt_stages = [
        row for row in stage_rows if int(row.get("attempt", 1)) == observation.attempt
    ]
    status = normalize_status(observation.status)
    tagged_no_data = any(_event_is_no_data(row) for row in attempt_events)
    successful_event = any(
        _event_status(row) == "success"
        for row in attempt_events
        if row.get("event_type") in profile.run_terminal_event_types
    )
    successful_terminal = status in {"success", "no_data"} or (
        status not in TERMINAL_STATUSES and successful_event
    )
    # A no-data tag alone does not change the outcome; it only downgrades a
    # successful termination to "no_data".
    no_data = tagged_no_data and successful_terminal
    missing: list[str] = []
    if status == "success" and no_data:
        status = "no_data"
    for row in attempt_events:
        observed_at = row.get("observed_at")
        parsed_observed_at = _row_datetime(observed_at)
        if observed_at is None or parsed_observed_at is None or parsed_observed_at.tzinfo is None:
            missing.append(f"event:{row.get('event_id', 'unknown')}:invalid_timestamp")
        elif parsed_observed_at > now + clock_skew:
            missing.append(f"event:{row.get('event_id', 'unknown')}:timestamp_in_future")
    for row in attempt_stages:
        stage_id = row.get("stage_id", "unknown")
        started_at = row.get("started_at")
        finished_at = row.get("finished_at")
        started = _row_datetime(started_at) if started_at is not None else None
        finished = _row_datetime(finished_at) if finished_at is not None else None
        if started is not None and started.tzinfo is None:
            started = None
        if finished is not None and finished.tzinfo is None:
            finished = None
        if started_at is not None and (started is None or started.tzinfo is None):
            missing.append(f"stage:{stage_id}:invalid_started_at")
        if finished_at is not None and (finished is None or finished.tzinfo is None):
            missing.append(f"stage:{stage_id}:invalid_finished_at")
        if started is not None and started > now + clock_skew:
            missing.append(f"stage:{stage_id}:started_at_in_future")
        if finished is not None and finished > now + clock_skew:
            missing.append(f"stage:{stage_id}:finished_at_in_future")
        if started is not None and finished is not None and finished < started:
            missing.append(f"stage:{stage_id}:finished_before_started")
    terminal_events = [
        row for row in attempt_events if row.get("event_type") in profile.run_terminal_event_types
    ]
    if status in TERMINAL_STATUSES:
        if not terminal_events and not (status == "no_data" and no_data):
            missing.extend(f"event:{event_type}" for event_type in profile.run_terminal_event_types)
        if any(
            event_status is not None and event_status != status
            for event_status in (_event_status(row) for row in terminal_events)
        ):
            missing.append("event:contradictory_run_terminal_status")
        for field_name in profile.required_run_fields:
            value = {
                "status": observation.status,
                "started_at": observation.started_at,
                "finished_at": observation.finished_at,
                "provider_run_id": observation.provider_run_id,
                "pipeline_name": observation.pipeline_name,
                "provider": observation.provider,
            }.get(field_name)
            if value is None or value == "":
                missing.append(f"run:{field_name}")
    for requirement in profile.stages:
        matching_stages = [
            row
            for row in attempt_stages
            if row.get("stage_type") == requirement.stage_type
            and (requirement.provider is None or row.get("provider") == requirement.provider)
        ]
        waived = no_data and requirement.allow_no_data
        if not matching_stages and not waived:
            missing.append(f"stage:{requirement.stage_type}")
            continue
        expected_statuses = requirement.allowed_statuses or (
            (requirement.required_status,) if requirement.required_status else ()
        )
        if (
            expected_statuses
            and matching_stages
            and not any(
                normalize_status(row.get("status"))
                in {normalize_status(value) for value in expected_statuses}
                for row in matching_stages
            )
        ):
            missing.append(f"stage_status:{requirement.stage_type}={'|'.join(expected_statuses)}")
        expected_statuses = {
            normalize_status(value)
            for value in (
                requirement.allowed_statuses
                or ((requirement.required_status,) if requirement.required_status else ())
            )
        }
        matching_stage_ids = {row.get("stage_id") for row in matching_stages}
        # An event satisfies a requirement when its type matches and it is
        # attributed to one of the matching stages. An unattributed event is
        # accepted only when exactly one stage matches, so it can never be
        # claimed by the wrong stage. An event without a status inherits the
        # single matching stage's status when that status alone satisfies the
        # requirement.
        for event_type in requirement.required_event_types:
            if (
                not any(
                    row.get("event_type") == event_type
                    and (
                        (row.get("stage_id") or _event_payload(row).get("stage_id"))
                        in matching_stage_ids
                        if row.get("stage_id") or _event_payload(row).get("stage_id")
                        else len(matching_stages) == 1
                    )
                    and (
                        not expected_statuses
                        or _event_status(row) in expected_statuses
                        or (
                            _event_status(row) is None
                            and len(matching_stages) == 1
                            and normalize_status(matching_stages[0].get("status"))
                            in expected_statuses
                        )
                    )
                    for row in attempt_events
                )
                and not waived
            ):
                missing.append(f"event:{event_type}")

    record_rows = record_rows or {}
    record_states: list[EvidenceCompleteness] = []
    for requirement in profile.required_records:
        rows = record_rows.get(requirement.family, [])
        if len(rows) < requirement.minimum:
            missing.append(f"{requirement.family}:minimum:{requirement.minimum}")
        if requirement.required_status and not any(
            _record_status(requirement.family, row) == requirement.required_status.strip().lower()
            for row in rows
        ):
            missing.append(f"{requirement.family}:status:{requirement.required_status}")
        states = {row.get("status") for row in rows}
        for row in rows:
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, ValueError):
                    metadata = {}
            if isinstance(metadata, dict) and metadata.get("evidence_completeness") == "incomplete":
                missing.append(f"{requirement.family}:incomplete")
        if "redacted" in states:
            record_states.append(EvidenceCompleteness.REDACTED)
        elif "expired" in states:
            record_states.append(EvidenceCompleteness.EXPIRED)
        elif "missing" in states:
            record_states.append(EvidenceCompleteness.MISSING)
        if requirement.family == "artifact":
            for row in rows:
                expires_at = row.get("expires_at")
                if not expires_at:
                    continue
                try:
                    expiry = (
                        datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if isinstance(expires_at, str)
                        else expires_at
                    )
                except (TypeError, ValueError):
                    missing.append("artifact:invalid_expiry")
                    continue
                if expiry.tzinfo is None:
                    missing.append("artifact:invalid_expiry")
                elif (
                    expiry <= now and row.get("status") != "redacted" and not row.get("legal_hold")
                ):
                    record_states.append(EvidenceCompleteness.EXPIRED)
                    missing.append("artifact:expired")

    record_state = _strongest_evidence_state(*record_states)
    explicit_state = _strongest_evidence_state(observation.evidence_state, record_state)
    if explicit_state in {
        EvidenceCompleteness.MISSING,
        EvidenceCompleteness.EXPIRED,
        EvidenceCompleteness.REDACTED,
    }:
        completeness = explicit_state
    else:
        completeness = (
            EvidenceCompleteness.COMPLETE if not missing else EvidenceCompleteness.INCOMPLETE
        )

    reason = (
        "complete" if completeness is EvidenceCompleteness.COMPLETE else "missing_required_evidence"
    )
    finished_at = observation.finished_at
    heartbeat = _latest_heartbeat(observation, attempt_events)
    future_cutoff = now + clock_skew
    invalid_timing = (
        observation.started_at
        and observation.finished_at
        and observation.finished_at < observation.started_at
    )
    future_started = bool(observation.started_at and observation.started_at > future_cutoff)
    future_finished = bool(observation.finished_at and observation.finished_at > future_cutoff)
    invalid_heartbeat = bool(
        heartbeat
        and (
            heartbeat > future_cutoff
            or (observation.started_at is not None and heartbeat < observation.started_at)
        )
    )
    if invalid_timing:
        missing.append("run:finished_before_started")
    if future_started:
        missing.append("run:started_at_in_future")
    if future_finished:
        missing.append("run:finished_at_in_future")
    if invalid_heartbeat:
        missing.append("run:invalid_heartbeat")
    if (
        invalid_timing or future_started or future_finished or invalid_heartbeat
    ) and explicit_state not in {
        EvidenceCompleteness.MISSING,
        EvidenceCompleteness.EXPIRED,
        EvidenceCompleteness.REDACTED,
    }:
        completeness = EvidenceCompleteness.INCOMPLETE
    stale_seconds = int(stale_after.total_seconds()) if stale_after is not None else None
    known_status = status in TERMINAL_STATUSES or status in NONTERMINAL_STATUSES or status is None
    if not known_status:
        status = "unsupported"
        reason = "unsupported_provider_status"
        completeness = EvidenceCompleteness.INCOMPLETE
    elif status is None:
        status = "unknown"
        reason = "missing_provider_status"
        if explicit_state not in {
            EvidenceCompleteness.MISSING,
            EvidenceCompleteness.EXPIRED,
            EvidenceCompleteness.REDACTED,
        }:
            completeness = EvidenceCompleteness.INCOMPLETE
    elif status not in TERMINAL_STATUSES:
        status = status or "unknown"
        if invalid_heartbeat:
            reason = "invalid_heartbeat"
        elif future_started or future_finished:
            reason = "invalid_timing"
        elif stale_after is not None and heartbeat is not None and now - heartbeat >= stale_after:
            status = "abandoned"
            reason = "heartbeat_expired"
        else:
            reason = "awaiting_terminal_evidence" if heartbeat else "missing_heartbeat"
        if explicit_state not in {
            EvidenceCompleteness.MISSING,
            EvidenceCompleteness.EXPIRED,
            EvidenceCompleteness.REDACTED,
        }:
            completeness = EvidenceCompleteness.INCOMPLETE
    elif completeness is not EvidenceCompleteness.COMPLETE:
        reason = (
            ",".join(("source_evidence_" + completeness.value, *missing))
            if explicit_state
            else ",".join(("missing_required_evidence", *missing))
        )

    evidence_checksum = payload_checksum(
        {
            "project_id": observation.project_id,
            "run_id": observation.run_id,
            "attempt": observation.attempt,
            "status": status,
            "provider_status": observation.status,
            "started_at": observation.started_at.isoformat() if observation.started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
            "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
            "events": [
                {
                    "producer": row.get("producer"),
                    "event_id": row.get("event_id"),
                    "event_type": row.get("event_type"),
                    "schema_version": row.get("schema_version"),
                    "stage_id": row.get("stage_id"),
                    "observed_at": row.get("observed_at"),
                    "sequence": row.get("sequence"),
                    "attempt": row.get("attempt"),
                    "payload_checksum": row.get("payload_checksum"),
                }
                for row in sorted(
                    attempt_events,
                    key=lambda item: (item.get("producer", ""), item.get("event_id", "")),
                )
            ],
            "stages": [
                (
                    row.get("stage_id"),
                    row.get("stage_type"),
                    row.get("provider"),
                    row.get("tool"),
                    row.get("asset"),
                    row.get("attempt"),
                    row.get("record_checksum"),
                    row.get("status"),
                    row.get("started_at"),
                    row.get("finished_at"),
                    row.get("metrics"),
                    row.get("error"),
                )
                for row in sorted(attempt_stages, key=lambda item: item.get("stage_id", ""))
            ],
            "records": {
                family: [
                    {
                        "id": row.get(f"{family}_id"),
                        "record_checksum": row.get("record_checksum"),
                    }
                    for row in sorted(
                        rows,
                        key=lambda item: (
                            str(item.get(f"{family}_id", "")),
                            str(item.get("record_checksum", "")),
                        ),
                    )
                ]
                for family, rows in sorted(record_rows.items())
            },
            "source": observation.source,
            "pipeline_name": observation.pipeline_name,
            "provider": observation.provider,
            "provider_run_id": observation.provider_run_id,
            "evidence_state": explicit_state.value if explicit_state else None,
            "missing_evidence": tuple(sorted(set(missing))),
            "timing_policy_seconds": int(clock_skew.total_seconds()),
            "profile": (profile.profile_id, profile.version),
            "reason": reason,
        }
    )
    decision_id = hashlib.sha256(
        canonical_json(
            {
                "project_id": observation.project_id,
                "run_id": observation.run_id,
                "attempt": observation.attempt,
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "evidence_checksum": evidence_checksum,
            }
        ).encode("utf-8")
    ).hexdigest()[:32]
    return ReconciliationDecision(
        decision_id=decision_id,
        project_id=observation.project_id,
        run_id=observation.run_id,
        attempt=observation.attempt,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        status=status,
        evidence_completeness=completeness,
        reason=reason,
        missing_evidence=tuple(missing),
        evidence_checksum=evidence_checksum,
        observed_event_count=len(attempt_events),
        source=observation.source,
        heartbeat_at=heartbeat,
        stale_after_seconds=stale_seconds,
        decided_at=now,
        finished_at=finished_at,
    )


class RunReconciler:
    """Reconcile provider observations through one transactional store call."""

    def __init__(
        self,
        store: Any,
        source: RunEvidenceSource,
        *,
        stale_after: timedelta | None = None,
        clock_skew: timedelta = DEFAULT_CLOCK_SKEW,
    ) -> None:
        if stale_after is not None and stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        if clock_skew < timedelta(0):
            raise ValueError("clock_skew must not be negative")
        self.store = store
        self.source = source
        self.stale_after = stale_after
        self.clock_skew = clock_skew

    def reconcile(
        self,
        project_id: str,
        run_id: str,
        profile: RequiredEvidenceProfile,
        *,
        now: datetime | None = None,
    ) -> ReconciliationDecision:
        """Observe the provider run and reconcile it through the transactional store.

        Raises: RunEvidenceUnavailable when the provider cannot be queried."""
        observation = self.source.observe_run(project_id, run_id)
        if observation is None:
            raise RunEvidenceUnavailable(
                "provider lookup was unavailable; reconciliation did not change the run"
            )
        if observation.project_id != project_id or (
            observation.run_id != run_id and observation.provider_run_id != run_id
        ):
            raise ValueError("event source crossed project/run boundaries")
        return self.store.reconcile_observation(
            observation,
            profile,
            now=now or datetime.now(UTC),
            stale_after=self.stale_after,
            clock_skew=self.clock_skew,
        )
