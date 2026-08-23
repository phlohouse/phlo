"""Attempt-scoped, provider-neutral projection of durable run evidence.

build_run_report projects exactly one store-read snapshot into typed report
rows. Payloads are redacted, error text reduced to fingerprints, resource
identities taken only from producer data, and terminal outcomes fail closed on
any ambiguity instead of electing a winner.

Imported by the observatory run-report API and re-exported through phlo.run_evidence; the
system's canonical run-report builder, layering reconciliation and redaction primitives.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from phlo.run_evidence.reconciliation import TERMINAL_STATUSES, normalize_status
from phlo.run_evidence.redaction import redact_payload

Scalar = str | int | float | bool | None
_SAFE_CATALOG_METADATA = frozenset(
    {
        "branch",
        "ref",
        "nessie_ref",
        "nessie_hash",
        "wap",
        "operation",
        "source_hash",
        "target_hash",
        "merge_outcome",
        "snapshot_before",
        "snapshot_after",
    }
)


class RunReportStore(Protocol):
    """Read capability required to project one consistent attempt snapshot."""

    def read_run_attempt(self, project_id: str, run_id: str, attempt: int) -> dict[str, Any]:
        """Return the durable evidence payload for exactly one project/run/attempt."""
        ...


class RunReportNotFound(LookupError):
    """No durable evidence exists for the requested project/run/attempt."""


@dataclass(frozen=True)
class ReportGap:
    field: str
    status: str
    reason: str


@dataclass(frozen=True)
class ReportResourceIdentity:
    """Canonical project-scoped authorization identity carried by evidence."""

    project_id: str
    resource_type: str
    resource_id: str
    tenant: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class ReportEvent:
    event_id: str
    producer: str
    event_type: str
    observed_at: str | None
    sequence: int | None
    payload_checksum: str | None
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportStage:
    stage_id: str
    stage_type: str
    provider: str | None
    tool: str | None
    asset: str | None
    status: str
    started_at: str | None
    finished_at: str | None
    error_fingerprint: str | None
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportResource:
    resource_id: str
    resource_kind: str
    role: str
    normalized_identity: str | None
    uri: str | None
    table_name: str | None
    catalog: str | None
    ref_name: str | None
    schema_hash: str | None
    record_count: int | None
    byte_count: int | None
    staged_objects: tuple[dict[str, Scalar], ...]
    snapshot_before: str | None
    snapshot_after: str | None
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportLineage:
    lineage_edge_id: str
    source: str
    target: str
    origin: str
    derivation: str
    source_resource_identity: ReportResourceIdentity | None
    source_resource_identity_status: str
    target_resource_identity: ReportResourceIdentity | None
    target_resource_identity_status: str


@dataclass(frozen=True)
class ReportQuality:
    quality_result_id: str
    check_id: str
    asset: str | None
    stage_id: str | None
    severity: str | None
    blocking: bool
    passed: bool
    evaluated_count: int | None
    failed_count: int | None
    failure_artifact_id: str | None
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportCatalogChange:
    catalog_change_id: str
    catalog_ref: str | None
    content_key: str | None
    operation: str
    source_hash: str | None
    target_hash: str | None
    commit_hash: str | None
    merge_outcome: str | None
    snapshot_before: str | None
    snapshot_after: str | None
    metadata: dict[str, Scalar]
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportArtifact:
    artifact_id: str
    artifact_kind: str
    uri: str | None
    content_type: str | None
    checksum: str | None
    expires_at: str | None
    legal_hold: bool
    status: str
    resource_identity: ReportResourceIdentity | None
    resource_identity_status: str


@dataclass(frozen=True)
class ReportRunHeader:
    project_id: str
    run_id: str
    pipeline_name: str | None
    provider_run_id: str | None
    attempt: int
    status: str
    started_at: str | None
    finished_at: str | None
    failure_summary: str | None
    evidence_completeness: str


@dataclass(frozen=True)
class ReportLifecycle:
    run: ReportRunHeader | None
    events: tuple[ReportEvent, ...]


@dataclass(frozen=True)
class TerminalOutcome:
    status: str
    source: str
    evidence_id: str
    observed_at: str | None


@dataclass(frozen=True)
class RunReport:
    schema_version: int
    project_id: str
    run_id: str
    attempt: int
    lifecycle: ReportLifecycle
    stages: tuple[ReportStage, ...]
    inputs: tuple[ReportResource, ...]
    staging: tuple[ReportResource, ...]
    outputs: tuple[ReportResource, ...]
    lineage: tuple[ReportLineage, ...]
    transformations: tuple[ReportStage, ...]
    quality: tuple[ReportQuality, ...]
    iceberg_snapshots: tuple[ReportResource, ...]
    catalog_changes: tuple[ReportCatalogChange, ...]
    artifacts: tuple[ReportArtifact, ...]
    terminal_outcome: TerminalOutcome | None
    gaps: tuple[ReportGap, ...]


def _scalar(value: Any) -> Scalar:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _safe_text(value: Any) -> str | None:
    redacted = redact_payload(value)
    return redacted if isinstance(redacted, str) else None


def _safe_staged_objects(value: Any) -> tuple[dict[str, Scalar], ...]:
    if not isinstance(value, list):
        return ()
    safe: list[dict[str, Scalar]] = []
    for item in value:
        if isinstance(item, str):
            safe.append({"identity": _safe_text(item)})
        elif isinstance(item, dict) and isinstance(item.get("identity"), str):
            safe.append(
                {
                    key: _safe_text(item[key]) if key == "identity" else _scalar(item[key])
                    for key in ("identity", "checksum", "byte_count", "record_count")
                    if key in item
                }
            )
    return tuple(safe)


def _safe_metadata(value: Any) -> dict[str, Scalar]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Scalar] = {}
    for key in _SAFE_CATALOG_METADATA:
        if key not in value:
            continue
        redacted = redact_payload(value[key])
        scalar = _scalar(redacted)
        if scalar is not None:
            safe[key] = scalar
    return safe


def _resource_identity(
    row: dict[str, Any], field: str, project_id: str
) -> tuple[ReportResourceIdentity | None, str]:
    """Expose only producer-supplied canonical identities; never infer display text."""
    value = row.get(field)
    if not isinstance(value, dict):
        return None, "incomplete"
    resource_type = value.get("resource_type")
    resource_id = value.get("resource_id")
    attributes = value.get("attributes", {})
    if (
        not isinstance(resource_type, str)
        or not resource_type.strip()
        or not isinstance(resource_id, str)
        or not resource_id.strip()
        or value.get("tenant") != project_id
        or not isinstance(attributes, dict)
        or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in attributes.items()
        )
    ):
        return None, "incomplete"
    return (
        ReportResourceIdentity(project_id, resource_type, resource_id, project_id, attributes),
        "complete",
    )


def _fingerprint(value: Any) -> str | None:
    # Error text never reaches reports; only its SHA-256 fingerprint does.
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _run_header(row: dict[str, Any] | None) -> ReportRunHeader | None:
    if row is None:
        return None
    return ReportRunHeader(
        project_id=str(row["project_id"]),
        run_id=str(row["run_id"]),
        pipeline_name=row.get("pipeline_name"),
        provider_run_id=row.get("provider_run_id"),
        attempt=int(row["attempt"]),
        status=str(row["status"]),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        failure_summary=_safe_text(row.get("failure_summary")),
        evidence_completeness=str(row["evidence_completeness"]),
    )


def _event(row: dict[str, Any]) -> ReportEvent:
    identity, identity_status = _resource_identity(row, "resource_identity", str(row["project_id"]))
    return ReportEvent(
        event_id=str(row["event_id"]),
        producer=str(row["producer"]),
        event_type=str(row["event_type"]),
        observed_at=row.get("observed_at"),
        sequence=row.get("sequence"),
        payload_checksum=row.get("payload_checksum"),
        resource_identity=identity,
        resource_identity_status=identity_status,
    )


def _stage(row: dict[str, Any]) -> ReportStage:
    identity, identity_status = _resource_identity(row, "resource_identity", str(row["project_id"]))
    return ReportStage(
        stage_id=str(row["stage_id"]),
        stage_type=str(row["stage_type"]),
        provider=row.get("provider"),
        tool=row.get("tool"),
        asset=row.get("asset"),
        status=str(row["status"]),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        error_fingerprint=_fingerprint(row.get("error")),
        resource_identity=identity,
        resource_identity_status=identity_status,
    )


def _resource(row: dict[str, Any]) -> ReportResource:
    identity, identity_status = _resource_identity(row, "resource_identity", str(row["project_id"]))
    return ReportResource(
        resource_id=str(row["resource_id"]),
        resource_kind=str(row["resource_kind"]),
        role=str(row["role"]),
        normalized_identity=row.get("normalized_identity"),
        uri=_safe_text(row.get("uri")),
        table_name=row.get("table_name"),
        catalog=row.get("catalog"),
        ref_name=row.get("ref_name"),
        schema_hash=row.get("schema_hash"),
        record_count=row.get("record_count"),
        byte_count=row.get("byte_count"),
        staged_objects=_safe_staged_objects(row.get("staged_objects")),
        snapshot_before=row.get("snapshot_before"),
        snapshot_after=row.get("snapshot_after"),
        resource_identity=identity,
        resource_identity_status=identity_status,
    )


def _terminal_outcome(
    events: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> tuple[TerminalOutcome | None, str | None]:
    # Terminal outcome fails closed: any malformed terminal status, or any
    # disagreement between terminal sources, suppresses the outcome entirely
    # instead of electing a winner.
    candidates: list[tuple[str, str, str, str | None]] = []
    invalid = False
    for event in events:
        if event.get("event_type") not in {"run.terminal", "pipeline.terminal"}:
            continue
        payload = event.get("payload")
        status = (
            normalize_status(payload.get("status") or payload.get("run_status"))
            if isinstance(payload, dict)
            else None
        )
        if status not in TERMINAL_STATUSES:
            invalid = True
            continue
        candidates.append(
            (
                status,
                "lifecycle_event",
                f"{event['producer']}:{event['event_id']}",
                event.get("observed_at"),
            )
        )
    for decision in decisions:
        status = normalize_status(decision.get("status"))
        if status in TERMINAL_STATUSES:
            candidates.append(
                (status, "reconciliation", str(decision["decision_id"]), decision.get("decided_at"))
            )
    statuses = {candidate[0] for candidate in candidates}
    if invalid:
        return None, "invalid_terminal_status"
    if len(statuses) > 1:
        return None, "conflicting_terminal_statuses"
    if not candidates:
        return None, "terminal_evidence_not_stored"
    status, source, evidence_id, observed_at = candidates[0]
    return TerminalOutcome(status, source, evidence_id, observed_at), None


def build_run_report(
    store: RunReportStore, project_id: str, run_id: str, attempt: int
) -> RunReport:
    """Build a safe typed report from exactly one store read snapshot."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
        raise ValueError("attempt must be a positive integer")
    snapshot = store.read_run_attempt(project_id, run_id, attempt)
    rows = {key: value or [] for key, value in snapshot.items() if key != "run"}
    run_row = snapshot.get("run")
    if run_row is None and not any(rows.values()):
        raise RunReportNotFound(f"run report {project_id}/{run_id}/{attempt} was not found")

    stages = tuple(_stage(row) for row in rows["stages"])
    resources = tuple(_resource(row) for row in rows["resources"])
    groups = {
        "inputs": tuple(item for item in resources if item.role.lower() in {"input", "inputs"}),
        "staging": tuple(item for item in resources if item.role.lower() in {"staged", "staging"}),
        "outputs": tuple(item for item in resources if item.role.lower() in {"output", "outputs"}),
    }
    terminal, terminal_gap = _terminal_outcome(rows["events"], rows["reconciliation"])
    gaps: list[ReportGap] = []
    if run_row is None:
        gaps.append(ReportGap("lifecycle.run", "unavailable", "no_attempt_scoped_run_record"))
    for field in (
        "stages",
        "inputs",
        "staging",
        "outputs",
        "lineage",
        "quality",
        "catalog_changes",
        "artifacts",
    ):
        if not rows[field] if field not in groups else not groups[field]:
            gaps.append(ReportGap(field, "unavailable", "no_attempt_scoped_evidence"))
    if not rows["resources"] or not any(
        row.get("snapshot_before") or row.get("snapshot_after") for row in rows["resources"]
    ):
        gaps.append(ReportGap("iceberg_snapshots", "unavailable", "no_attempt_scoped_evidence"))
    if not any(stage.stage_type.lower() in {"transform", "transformation"} for stage in stages):
        gaps.append(ReportGap("transformations", "unavailable", "no_attempt_scoped_evidence"))
    if terminal is None:
        gaps.append(
            ReportGap(
                "terminal_outcome", "unavailable", terminal_gap or "terminal_evidence_not_stored"
            )
        )
    if not rows["reconciliation"]:
        gaps.append(
            ReportGap("historical_fields", "unavailable", "attempt_reconciliation_not_proven")
        )
    identity_fields = {
        "events": ("resource_identity",),
        "stages": ("resource_identity",),
        "resources": ("resource_identity",),
        "lineage": ("source_resource_identity", "target_resource_identity"),
        "quality": ("resource_identity",),
        "catalog_changes": ("resource_identity",),
        "artifacts": ("resource_identity",),
    }
    if any(
        _resource_identity(row, field, project_id)[1] != "complete"
        for family, fields in identity_fields.items()
        for row in rows[family]
        for field in fields
    ):
        gaps.append(
            ReportGap(
                "resource_identities",
                "incomplete",
                "one_or_more_evidence_records_lack_authoritative_resource_identity",
            )
        )

    return RunReport(
        schema_version=1,
        project_id=project_id,
        run_id=run_id,
        attempt=attempt,
        lifecycle=ReportLifecycle(
            _run_header(run_row), tuple(_event(row) for row in rows["events"])
        ),
        stages=stages,
        inputs=groups["inputs"],
        staging=groups["staging"],
        outputs=groups["outputs"],
        lineage=tuple(
            ReportLineage(
                str(row["lineage_edge_id"]),
                str(row["source"]),
                str(row["target"]),
                str(row["origin"]),
                str(row["derivation"]),
                *_resource_identity(row, "source_resource_identity", project_id),
                *_resource_identity(row, "target_resource_identity", project_id),
            )
            for row in rows["lineage"]
        ),
        transformations=tuple(
            stage for stage in stages if stage.stage_type.lower() in {"transform", "transformation"}
        ),
        quality=tuple(
            ReportQuality(
                str(row["quality_result_id"]),
                str(row["check_id"]),
                row.get("asset"),
                row.get("stage_id"),
                row.get("severity"),
                bool(row["blocking"]),
                bool(row["passed"]),
                row.get("evaluated_count"),
                row.get("failed_count"),
                row.get("failure_artifact_id"),
                *_resource_identity(row, "resource_identity", project_id),
            )
            for row in rows["quality"]
        ),
        iceberg_snapshots=tuple(
            resource
            for resource in resources
            if resource.snapshot_before or resource.snapshot_after
        ),
        catalog_changes=tuple(
            ReportCatalogChange(
                str(row["catalog_change_id"]),
                row.get("catalog_ref"),
                row.get("content_key"),
                str(row["operation"]),
                row.get("source_hash"),
                row.get("target_hash"),
                row.get("commit_hash"),
                row.get("merge_outcome"),
                row.get("snapshot_before"),
                row.get("snapshot_after"),
                _safe_metadata(row.get("metadata")),
                *_resource_identity(row, "resource_identity", project_id),
            )
            for row in rows["catalog_changes"]
        ),
        artifacts=tuple(
            ReportArtifact(
                str(row["artifact_id"]),
                str(row["artifact_kind"]),
                _safe_text(row.get("uri")),
                row.get("content_type"),
                row.get("checksum"),
                row.get("expires_at"),
                bool(row["legal_hold"]),
                str(row["status"]),
                *_resource_identity(row, "resource_identity", project_id),
            )
            for row in rows["artifacts"]
        ),
        terminal_outcome=terminal,
        gaps=tuple(gaps),
    )
