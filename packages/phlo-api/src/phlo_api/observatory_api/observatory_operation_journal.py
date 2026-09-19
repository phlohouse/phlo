"""Persistent operation journal for Observatory.

Stores validated ObservatoryOperation records in the project's durable
state collection, capped at MAX_OPERATION_RECORDS and kept sorted newest
first on every write. Corrupt records raise StorageCorruptionError rather
than surfacing partially valid state. Also derives the stable v1
agent-readable observability context contract from each operation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from phlo_api.observatory_api.observatory_metadata import safe_metadata
from phlo_api.observatory_api.observatory_durable_state import (
    mutate_collection,
    read_collection,
)
from phlo_api.observatory_api.observatory_models import (
    HealthState,
    OperationStatus,
    ObservatoryActionResult,
    ObservatoryHealth,
    ObservatoryOperation,
    ObservatoryResourceRef,
)

MAX_OPERATION_RECORDS = 200
OPERATION_OBSERVABILITY_SCHEMA_VERSION = "phlo.operation_observability.v1"


def operation_journal_path(project_root: Path) -> Path:
    """Return the legacy journal file path. Pure: the durable store writes
    through the settings service, so this path is only ever read — never
    create the state directory here.
    """
    return project_root / ".phlo" / "observatory" / "operation_journal.json"


def load_operation_journal(project_root: Path) -> list[ObservatoryOperation]:
    """Load and validate the persisted operations, newest first; raises on
    corrupt state. An absent journal reads as empty — legacy import happens
    only through explicit ``initialize_collections`` at startup.
    """
    return _validate_operations(read_collection(project_root, "operation_journal") or [])


def _cap_operations(operations: list[ObservatoryOperation]) -> list[ObservatoryOperation]:
    """Cap journal history without dropping unresolved operation identities.

    Non-terminal records (queued/running/unknown) are never evicted by the
    cap: an unresolved operation that fell out of history would be invisible
    and could neither be reconciled nor blocked from blind replay. Terminal
    records fill the remaining budget, newest first.
    """
    ordered = sort_operations(operations)
    unresolved = [op for op in ordered if op.status in {"queued", "running", "unknown"}]
    terminal = [op for op in ordered if op.status not in {"queued", "running", "unknown"}]
    kept = terminal[: max(0, MAX_OPERATION_RECORDS - len(unresolved))]
    return sort_operations([*unresolved, *kept])


def write_operation_journal(project_root: Path, operations: Iterable[ObservatoryOperation]) -> None:
    """Replace the journal with the given operations, sorted and capped at MAX_OPERATION_RECORDS."""
    records = _cap_operations(list(operations))
    mutate_collection(
        project_root,
        "operation_journal",
        operation_journal_path(project_root),
        lambda _items: [operation.model_dump(mode="json") for operation in records],
    )


def append_operation(
    project_root: Path,
    operation: ObservatoryOperation,
    *,
    record_id: str | None = None,
    recorded_at: str | None = None,
) -> ObservatoryOperation:
    """Persist a copy of the operation under a new record id, original id kept in metadata."""
    timestamp = recorded_at or datetime.now(UTC).isoformat()
    original_id = operation.id
    operation_id = record_id or f"op-{uuid4().hex[:12]}"
    # The journal copy gets its own record id; the in-memory operation's id is
    # preserved in metadata so a recorded entry stays traceable to the action
    # that produced it.
    metadata = safe_metadata(
        {
            **operation.metadata,
            "original_operation_id": original_id,
            "recorded_at": timestamp,
        }
    )
    metadata["observability_contract"] = _operation_observability_contract(
        operation,
        operation_id=operation_id,
        original_operation_id=original_id,
    )
    recorded = operation.model_copy(
        update={
            "id": operation_id,
            "started_at": operation.started_at or timestamp,
            "completed_at": operation.completed_at or timestamp,
            "duration_seconds": operation.duration_seconds
            if operation.duration_seconds is not None
            else 0.0,
            "metadata": metadata,
        }
    )
    mutate_collection(
        project_root,
        "operation_journal",
        operation_journal_path(project_root),
        lambda items: [
            item.model_dump(mode="json")
            for item in _cap_operations([recorded, *_validate_operations(items)])
        ],
    )
    return recorded


def _validate_operations(items: list[dict[str, object]]) -> list[ObservatoryOperation]:
    try:
        return sort_operations([ObservatoryOperation.model_validate(item) for item in items])
    except Exception as exc:
        from phlo.plugins.observatory_settings import StorageCorruptionError

        raise StorageCorruptionError("Observatory durable state is unavailable") from exc


def record_action_result(
    project_root: Path,
    result: ObservatoryActionResult,
    *,
    target: ObservatoryResourceRef | None = None,
    record_id: str | None = None,
    recorded_at: str | None = None,
) -> ObservatoryActionResult:
    """Record an action result's operation in the journal and attach it to the result."""
    operation = result.operation or operation_from_action_result(result, target=target)
    recorded = append_operation(
        project_root,
        operation,
        record_id=record_id,
        recorded_at=recorded_at,
    )
    return result.model_copy(update={"operation": recorded})


def build_operation_observability_context(operation: ObservatoryOperation) -> dict[str, object]:
    """Build the stable agent-readable observability context for an operation."""
    identifiers = _contract_from_operation(operation)
    status = (
        "open" if operation.status in {"failed", "running", "queued", "unknown"} else "resolved"
    )
    return {
        "schema_version": OPERATION_OBSERVABILITY_SCHEMA_VERSION,
        "operation": {
            "id": operation.id,
            "name": operation.name,
            "kind": operation.kind,
            "status": operation.status,
            "health": operation.health.model_dump(mode="json"),
            "target": operation.target.model_dump(mode="json") if operation.target else None,
            "started_at": operation.started_at,
            "completed_at": operation.completed_at,
            "duration_seconds": operation.duration_seconds,
        },
        "identifiers": identifiers,
        "incident": {
            "status": status,
            "severity": operation.health.state,
            "message": operation.health.message or operation.metadata.get("message"),
            "incident_ids": identifiers["incident_ids"],
        },
        "retention": {
            "history_limit": MAX_OPERATION_RECORDS,
            "history_store": ".phlo/observatory/operation_journal.json",
        },
        "metadata": safe_metadata(operation.metadata),
    }


def operation_from_action_result(
    result: ObservatoryActionResult,
    *,
    target: ObservatoryResourceRef | None = None,
) -> ObservatoryOperation:
    """Build an operation record from a completed action result, optionally bound to a target."""
    action = result.action
    return ObservatoryOperation(
        id=action.id,
        name=action.label,
        kind=action.kind,
        status=_coerce_operation_status(result.status),
        health=ObservatoryHealth(
            state=_health_state_for_action_status(result.status),
            message=result.message[:200],
        ),
        target=target,
        metadata=safe_metadata(
            {
                "action_id": action.id,
                "action_kind": action.kind,
                "risk_level": action.risk_level,
                "required_capability": action.required_capability,
                "required_service": action.required_service,
                "message": result.message,
            }
        ),
    )


def operation_from_workflow_action(
    *,
    action_id: str,
    status: str,
    message: str,
    files: list[str],
) -> ObservatoryOperation:
    """Build the operation record for applying a workflow proposal to the listed files."""
    return ObservatoryOperation(
        id=f"workflow:{action_id}",
        name="Apply workflow proposal",
        kind="workflow.apply",
        status=_coerce_operation_status(status),
        health=ObservatoryHealth(
            state=_health_state_for_action_status(status),
            message=message[:200],
        ),
        target=ObservatoryResourceRef(kind="workflow", id=action_id, label=action_id),
        metadata=safe_metadata(
            {
                "action_id": action_id,
                "files": files,
                "message": message,
            }
        ),
    )


def _operation_observability_contract(
    operation: ObservatoryOperation,
    *,
    operation_id: str,
    original_operation_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": OPERATION_OBSERVABILITY_SCHEMA_VERSION,
        "operation_id": operation_id,
        "original_operation_id": original_operation_id or operation.id,
        "trace_ids": _identifier_values(operation.metadata, "trace"),
        "log_ids": _identifier_values(operation.metadata, "log"),
        "metric_ids": _identifier_values(operation.metadata, "metric"),
        "incident_ids": _identifier_values(operation.metadata, "incident"),
    }


def _contract_from_operation(operation: ObservatoryOperation) -> dict[str, object]:
    raw_contract = operation.metadata.get("observability_contract")
    if isinstance(raw_contract, Mapping):
        return {
            "operation_id": _string_or_default(raw_contract.get("operation_id"), operation.id),
            "trace_ids": _string_list(raw_contract.get("trace_ids")),
            "log_ids": _string_list(raw_contract.get("log_ids")),
            "metric_ids": _string_list(raw_contract.get("metric_ids")),
            "incident_ids": _string_list(raw_contract.get("incident_ids")),
        }
    contract = _operation_observability_contract(operation, operation_id=operation.id)
    return {
        "operation_id": operation.id,
        "trace_ids": contract["trace_ids"],
        "log_ids": contract["log_ids"],
        "metric_ids": contract["metric_ids"],
        "incident_ids": contract["incident_ids"],
    }


def _identifier_values(metadata: Mapping[str, object], family: str) -> list[str]:
    keys = (f"{family}_id", f"{family}_ids", f"{family}s", f"phlo.{family}_id")
    values: list[str] = []
    for key in keys:
        values.extend(_string_list(metadata.get(key)))
    return sorted(dict.fromkeys(values))


def _string_list(value: object) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | Mapping):
        return [item for item in (_string_or_default(raw, "") for raw in value) if item]
    return []


def _string_or_default(value: object, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def sort_operations(operations: Iterable[ObservatoryOperation]) -> list[ObservatoryOperation]:
    """Sort operations by completion (or start) timestamp, newest first."""
    return sorted(operations, key=_operation_sort_key, reverse=True)


def _operation_sort_key(operation: ObservatoryOperation) -> tuple[str, str]:
    timestamp = operation.completed_at or operation.started_at or ""
    return (timestamp, operation.id)


def _health_state_for_action_status(status: str) -> HealthState:
    if status == "succeeded":
        return "ok"
    if status == "failed":
        return "error"
    if status == "skipped":
        return "warning"
    return "unknown"


def _coerce_operation_status(status: str) -> OperationStatus:
    if status in {"queued", "running", "succeeded", "failed", "skipped", "unknown"}:
        return cast(OperationStatus, status)
    # A provider-acknowledged but unsettled action records as running.
    if status == "accepted":
        return "running"
    return "unknown"
