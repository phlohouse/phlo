"""Plan-bound maintenance through table stores and optional engine executors.

The table store owns metadata, revision guards and retention revalidation; an
engine executor is only its submission boundary. HTTP and CLI share this
binding and dispatch, while keeping authorization and presentation outside.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from phlo.application.discovery import discover_capabilities
from phlo.capabilities.maintenance import SAFE_MIN_RETENTION_HOURS
from phlo.capabilities.resolver import resolve_capability
from phlo.operations.journal import (
    OperationJournalEntry,
    OperationJournalStore,
    execute_maintenance,
    maintenance_operation_id,
    maintenance_plan_token,
)


class MaintenanceError(ValueError):
    """A maintenance request cannot be bound or dispatched safely."""

    def __init__(self, code: str, *identifiers: str) -> None:
        self.code = code
        self.identifiers = identifiers
        super().__init__(f"{code}: {', '.join(identifiers)}")


def maintenance_store() -> Any:
    """Resolve the shipped table-store maintenance owner, also used for inventory."""
    discover_capabilities()
    resolution = resolve_capability("table_store", "iceberg")
    if resolution is None:
        raise MaintenanceError("maintenance_store_unavailable", "no maintenance store registered")
    return resolution.provider


def _store_method(operation: str) -> Any:
    methods = {"compact": "compact", "snapshot_expiry": "expire_snapshots"}
    if operation not in methods:
        raise MaintenanceError("unknown_maintenance_operation", operation)
    method = getattr(maintenance_store(), methods[operation], None)
    if not callable(method):
        raise MaintenanceError("maintenance_store_unsupported", operation)
    return method


def plan_maintenance(
    *,
    operation: str,
    table: str,
    ref: str = "main",
    catalog: str = "iceberg",
    retention_hours: int = SAFE_MIN_RETENTION_HOURS,
    retain_last: int = 5,
    max_affected_objects: int = 1_000,
    max_affected_bytes: int = 1 << 30,
) -> dict[str, Any]:
    """Read a provider plan with shared defaults; never resolve an executor."""
    method = _store_method(operation)
    kwargs: dict[str, Any] = {"table_name": table, "override_ref": ref, "dry_run": True}
    if operation == "snapshot_expiry":
        kwargs.update(
            catalog=catalog,
            retention_hours=retention_hours,
            retain_last=retain_last,
            max_affected_objects=max_affected_objects,
            max_affected_bytes=max_affected_bytes,
        )
    try:
        result = method(**kwargs)
    except ValueError as exc:
        # Providers reject planning inputs with ValueError, including the
        # MaintenancePreconditionError subtype. Result conversion is separate.
        raise MaintenanceError("invalid_maintenance_input", operation, str(exc)) from exc
    plan = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if token := maintenance_plan_token(plan):
        plan["plan_token"] = token
    return plan


@dataclass(frozen=True)
class MaintenancePlan:
    """An exact confirmed target and the provider parameters used to plan it."""

    operation: str
    table: str
    ref: str
    plan_token: str
    before_revision: str | int | None
    planned: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        confirmation_token: str | None,
        table: str | None = None,
        ref: str | None = None,
    ) -> MaintenancePlan:
        """Bind without discovery or I/O; request targets may confirm, not redirect."""
        operation = str(data.get("operation", ""))
        if operation == "expire_snapshots":
            operation = "snapshot_expiry"
        if operation == "orphan_delete":
            raise MaintenanceError(
                "orphan_delete_unsupported", "orphan deletion is unsupported in v1"
            )
        if operation not in {"compact", "snapshot_expiry"}:
            raise MaintenanceError("unknown_maintenance_operation", operation)
        bound_table = str(data.get("table_name") or "")
        bound_ref = str(data.get("ref") or "main")
        if not bound_table:
            raise MaintenanceError("missing_field", "table")
        if table is not None and table != bound_table:
            raise MaintenanceError("table_mismatch", table, bound_table)
        if ref is not None and ref != bound_ref:
            raise MaintenanceError("ref_mismatch", ref, bound_ref)
        token = maintenance_plan_token(data)
        if not confirmation_token or confirmation_token != token:
            raise MaintenanceError("token_mismatch", token)
        planned = data.get("planned") or {}
        if not isinstance(planned, Mapping):
            raise MaintenanceError("invalid_plan", "planned must be an object")
        revision = data.get("before_revision")
        if revision is None:
            revision = planned.get("before_snapshot_id")
        if operation == "compact" and revision is None:
            raise MaintenanceError("invalid_plan", "missing before_revision")
        return cls(operation, bound_table, bound_ref, token, revision, dict(planned))

    @property
    def operation_id(self) -> str:
        """Use the same journal identity regardless of the calling adapter."""
        return maintenance_operation_id(self.operation, self.table, self.ref, self.plan_token)

    def apply(self, journal: OperationJournalStore, *, subject: str) -> OperationJournalEntry:
        """Replay plan parameters through the table-store guards and durable lifecycle."""
        method = _store_method(self.operation)
        resolution = resolve_capability("maintenance_executor")
        kwargs: dict[str, Any] = {
            "table_name": self.table,
            "override_ref": self.ref,
            "dry_run": False,
        }
        if resolution is not None:
            kwargs["executor"] = resolution.provider.for_ref(self.ref)
        if self.operation == "compact":
            kwargs["expected_revision"] = self.before_revision
        else:
            kwargs.update(
                expected_snapshot_id=self.before_revision,
                confirmation_token=self.plan_token,
                retention_hours=self.planned.get("retention_hours", SAFE_MIN_RETENTION_HOURS),
                retain_last=self.planned.get("retain_last", 5),
                catalog=self.planned.get("catalog"),
                max_affected_objects=self.planned.get("max_affected_objects"),
                max_affected_bytes=self.planned.get("max_affected_bytes"),
            )
        return execute_maintenance(
            journal,
            operation=self.operation,
            table=self.table,
            ref=self.ref,
            plan_token=self.plan_token,
            subject=subject,
            execute=lambda operation_id: method(**kwargs, operation_id=operation_id),
        )
