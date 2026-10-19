"""Validate legacy history and explicitly adopt it without rewriting data files.

Validation streams a pinned snapshot and spills the identity index to local disk.
Adoption repeats validation; a report is evidence, never authority to stamp policy.
"""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import pickle
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any

from pyiceberg.exceptions import CommitFailedException
from pyiceberg.table import Table
from pyiceberg.table.update import AssertCurrentSchemaId
from pyiceberg.types import BinaryType, FixedType, PrimitiveType, StringType

from phlo.capabilities.history import HistoryCommitUnknownError, HistoryPolicy
from phlo_iceberg.catalog import get_catalog
from phlo_iceberg.evidence import emit_mutation, table_state
from phlo_iceberg.history import (
    FINGERPRINT_PROPERTY,
    POLICY_PROPERTY,
    _equal,
    _policy_properties,
    _validate_value,
)
from phlo_iceberg.tables import _finish_iceberg_commit, _iceberg_commit_scope, _require_direct_write

ADOPTION_PROPERTY = "phlo.history.adoption"


def _schema_issues(table: Table, policy: HistoryPolicy) -> list[dict[str, Any]]:
    issues = []
    required = set((policy.entity_key, policy.version_key, *policy.comparison_columns))
    for name in sorted(required):
        if name not in table.schema().column_names:
            issues.append({"column": name, "reason": "missing top-level history column"})
            continue
        field = table.schema().find_field(name)
        if name in (policy.entity_key, policy.version_key) and not isinstance(
            field.field_type, PrimitiveType
        ):
            issues.append({"column": name, "reason": "identity must have a scalar type"})
        if name == policy.payload_hash_column and not isinstance(
            field.field_type, (StringType, BinaryType, FixedType)
        ):
            issues.append({"column": name, "reason": "payload hash must be string or binary"})
    return issues


def _index_row(
    index: sqlite3.Connection, row: dict[str, Any], policy: HistoryPolicy, position: int
) -> tuple[str, int | None]:
    for name in (policy.entity_key, policy.version_key):
        _validate_value(row[name], name)
    if policy.payload_hash_column:
        _validate_value(row[policy.payload_hash_column], policy.payload_hash_column, is_hash=True)
    key = (row[policy.entity_key], row[policy.version_key])
    payload = tuple(row[name] for name in policy.comparison_columns)
    # Python hashes only select candidates. Equality, not a digest, decides identity
    # and payload compatibility. Pickles are only read from this private scratch DB.
    for stored_key, stored_payload, first in index.execute(
        "SELECT identity, payload, position FROM versions WHERE bucket = ?", (hash(key),)
    ):
        if pickle.loads(stored_key) == key:
            return (
                "duplicate" if _equal(pickle.loads(stored_payload), payload) else "conflict",
                first,
            )
    index.execute(
        "INSERT INTO versions VALUES (?, ?, ?, ?)",
        (hash(key), pickle.dumps(key), pickle.dumps(payload), position),
    )
    return "distinct", None


def _scan_versions(table: Table, policy: HistoryPolicy, report: dict[str, Any]) -> None:
    snapshot = table.current_snapshot()
    if snapshot is None:
        return
    fields = tuple(
        dict.fromkeys((policy.entity_key, policy.version_key, *policy.comparison_columns))
    )
    with (
        TemporaryDirectory(prefix="phlo-history-") as scratch,
        closing(sqlite3.connect(Path(scratch) / "versions.sqlite")) as index,
        table.scan(
            snapshot_id=snapshot.snapshot_id, selected_fields=fields
        ).to_arrow_batch_reader() as reader,
    ):
        index.execute(
            "CREATE TABLE versions (bucket INTEGER, identity BLOB, payload BLOB, position INTEGER)"
        )
        index.execute("CREATE INDEX versions_bucket ON versions(bucket)")
        for batch in reader:
            _validate_rows(index, batch.to_pylist(), policy, report)
            index.commit()


def _validate_rows(
    index: sqlite3.Connection,
    rows: list[dict[str, Any]],
    policy: HistoryPolicy,
    report: dict[str, Any],
) -> None:
    for row in rows:
        report["rows_scanned"] += 1
        position = report["rows_scanned"]
        try:
            state, first = _index_row(index, row, policy, position)
        except ValueError as exc:
            report["issues"].append({"row": position, "reason": str(exc)})
            continue
        if state == "conflict":
            report["issues"].append(
                {"row": position, "first_row": first, "reason": "conflicting immutable payload"}
            )
        elif state == "duplicate":
            report["duplicate_rows"] += 1
        else:
            report["distinct_versions"] += 1


def _validate(table: Table, table_name: str, policy: HistoryPolicy, ref: str) -> dict[str, Any]:
    snapshot = table.current_snapshot()
    report: dict[str, Any] = {
        "format": 1,
        "table": table_name,
        "ref": ref,
        "table_uuid": str(table.metadata.table_uuid),
        "metadata_location": table.metadata_location,
        "snapshot_id": str(snapshot.snapshot_id) if snapshot else None,
        "schema": table.schema().model_dump(mode="json"),
        "policy_properties": {},
        "rows_scanned": 0,
        "distinct_versions": 0,
        "duplicate_rows": 0,
        "issues": _schema_issues(table, policy),
    }
    if not report["issues"]:
        properties = _policy_properties(table.schema(), policy)
        report["policy_properties"] = properties
        if any(name in table.properties for name in properties) and any(
            table.properties.get(name) != value for name, value in properties.items()
        ):
            report["issues"].append({"reason": "incompatible or incomplete native policy metadata"})
        _scan_versions(table, policy, report)
    report["compatible"] = not report["issues"]
    return report


def validate_history_adoption(
    table_name: str, policy: HistoryPolicy, ref: str = "main"
) -> dict[str, Any]:
    """Return a JSON-serialisable read-only report for the complete current snapshot.

    Row numbers are one-based scan ordinals within this report, not stable row IDs.
    Identical duplicate pairs are retained and reported; conflicting pairs reject
    adoption. Supplied hashes are compared as opaque values, never recomputed.
    """
    return _validate(get_catalog(ref=ref).load_table(table_name), table_name, policy, ref)


def _recover(table_name: str, ref: str, receipt: dict[str, Any]) -> dict[str, Any]:
    try:
        table = get_catalog(ref=ref).load_table(table_name)
        return {
            "state": "observed",
            "adoption_record_matches": table.properties.get(ADOPTION_PROPERTY)
            == json.dumps(receipt, sort_keys=True),
            "policy_matches": (
                table.properties.get(FINGERPRINT_PROPERTY) == receipt["policy_sha256"]
                and table.properties.get(POLICY_PROPERTY) == receipt["policy"]
            ),
            "metadata_location": table.metadata_location,
            "operation_id": receipt["operation_id"],
        }
    except Exception as exc:
        return {
            "state": "unavailable",
            "error_type": type(exc).__name__,
            "operation_id": receipt["operation_id"],
        }


def _commit_adoption(
    table: Table, report: dict[str, Any], *, operation_id: str, operator: str, reason: str
) -> dict[str, Any]:
    transaction = table.transaction()
    transaction._apply(
        (),
        (
            AssertCurrentSchemaId.model_validate(
                {"type": "assert-current-schema-id", "current-schema-id": table.schema().schema_id}
            ),
        ),
    )
    # Publish a data-preserving snapshot, not only properties. This makes two
    # adopters of different policies contend on the same atomic snapshot guard.
    with transaction.update_snapshot(
        snapshot_properties={"phlo.history.adoption.id": operation_id}
    ).fast_append():
        pass
    receipt = {
        "format": 1,
        "operation_id": operation_id,
        "operator": operator,
        "reason": reason,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "table": report["table"],
        "ref": report["ref"],
        "table_uuid": report["table_uuid"],
        "source_metadata_location": report["metadata_location"],
        "snapshot_before": report["snapshot_id"],
        "snapshot_after": str(transaction.table_metadata.current_snapshot_id),
        "policy": report["policy_properties"][POLICY_PROPERTY],
        "policy_sha256": report["policy_properties"][FINGERPRINT_PROPERTY],
        "rows_scanned": report["rows_scanned"],
        "distinct_versions": report["distinct_versions"],
        "duplicate_rows": report["duplicate_rows"],
    }
    transaction.set_properties(
        {**report["policy_properties"], ADOPTION_PROPERTY: json.dumps(receipt, sort_keys=True)}
    )
    with _iceberg_commit_scope(
        report["table"],
        report["ref"],
        "history_adoption",
        snapshot_id_before=report["snapshot_id"],
        rows_added=0,
    ) as scope:
        try:
            transaction.commit_transaction()
        except CommitFailedException:
            raise
        except Exception as exc:
            raise HistoryCommitUnknownError(
                "Adoption outcome is unknown; inspect the operation ID and policy before recovery",
                metrics={},
                reconciliation=_recover(report["table"], report["ref"], receipt),
            ) from exc
        _finish_iceberg_commit(scope, table)
    return receipt


def adopt_history_table(
    table_name: str,
    policy: HistoryPolicy,
    *,
    expected_metadata_location: str,
    operation_id: str,
    operator: str,
    reason: str,
    ref: str = "main",
    evidence_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate the reviewed revision and atomically bind native history policy.

    Quiesce legacy/external writers first. Conflicts never retry against a new
    revision. Repeating the same operation ID and source revision returns its
    durable receipt, without publishing another snapshot.
    """
    _require_direct_write(ref)
    if not all(
        isinstance(value, str) and value.strip()
        for value in (expected_metadata_location, operation_id, operator, reason)
    ):
        raise ValueError("Adoption requires a reviewed revision, operation_id, operator and reason")
    catalog = get_catalog(ref=ref)
    table = catalog.load_table(table_name)
    properties = _policy_properties(table.schema(), policy)
    if ADOPTION_PROPERTY in table.properties:
        receipt = json.loads(table.properties[ADOPTION_PROPERTY])
        if (
            receipt["operation_id"] == operation_id
            and receipt["source_metadata_location"] == expected_metadata_location
            and receipt["table_uuid"] == str(table.metadata.table_uuid)
            and receipt["policy_sha256"] == properties[FINGERPRINT_PROPERTY]
            and all(table.properties.get(k) == v for k, v in properties.items())
        ):
            return receipt
        raise ValueError("Table already has an adoption record; inspect it before recovery")
    if table.metadata_location != expected_metadata_location:
        raise ValueError("Table revision changed; run dry-run validation again")
    if any(name in table.properties for name in properties):
        raise ValueError(
            "Adoption requires an unmarked table; native policy changes need a rebuild"
        )
    before = table_state(catalog, table_name)
    try:
        report = _validate(table, table_name, policy, ref)
        if not report["compatible"]:
            raise ValueError("History adoption is incompatible; inspect the dry-run report")
        if catalog.load_table(table_name).metadata_location != expected_metadata_location:
            raise ValueError("Table revision changed during validation; run dry-run again")
        receipt = _commit_adoption(
            table, report, operation_id=operation_id, operator=operator, reason=reason
        )
    except Exception as exc:
        metadata: dict[str, Any] = {"operation_id": operation_id, "outcome": "failed"}
        if isinstance(exc, HistoryCommitUnknownError):
            metadata.update(outcome="unknown", reconciliation=exc.reconciliation)
        emit_mutation(
            context=evidence_context,
            table_name=table_name,
            ref=ref,
            operation="history_adoption",
            status="failed",
            before=before,
            after=table_state(catalog, table_name),
            metrics={},
            error=exc,
            extra_metadata=metadata,
        )
        raise
    emit_mutation(
        context=evidence_context,
        table_name=table_name,
        ref=ref,
        operation="history_adoption",
        status="success",
        before=before,
        after=table_state(catalog, table_name),
        metrics={"rows_inserted": 0, "rows_deleted": 0},
        extra_metadata={"adoption": receipt},
    )
    return receipt
