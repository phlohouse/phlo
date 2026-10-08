"""Insert immutable versions under a snapshot-head optimistic concurrency guard.

Lookup and staged append use one loaded metadata instance. Definite conflicts
retry the entire operation; ambiguous commits reconcile and fail explicitly.
"""

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.expressions import AlwaysFalse, And, EqualTo, Or, Reference
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from phlo.capabilities.history import (
    HistoryCommitUnknownError,
    HistoryConflictError,
    HistoryPolicy,
)
from phlo_iceberg.catalog import get_catalog
from phlo_iceberg.schema_alignment import SchemaPolicy, prepare_arrow_write, validate_schema_policy
from phlo_iceberg.tables import (
    _current_snapshot_id,
    _finish_iceberg_commit,
    _iceberg_commit_scope,
    _require_direct_write,
)

POLICY_PROPERTY = "phlo.history.policy"
FINGERPRINT_PROPERTY = "phlo.history.policy.sha256"


def _policy_properties(schema: Schema, policy: HistoryPolicy) -> dict[str, str]:
    def field(name: str) -> dict[str, Any]:
        column = schema.find_field(name)
        return {"name": name, "id": column.field_id, "type": str(column.field_type)}

    definition = json.dumps(
        {
            "format": 1,
            "entity_key": field(policy.entity_key),
            "version_key": field(policy.version_key),
            "payload_columns": [field(name) for name in policy.comparison_columns],
            "comparison": "supplied_hash" if policy.payload_hash_column else "null_safe_values",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        POLICY_PROPERTY: definition,
        FINGERPRINT_PROPERTY: hashlib.sha256(definition.encode()).hexdigest(),
    }


def _check_policy(table: Table, properties: dict[str, str]) -> None:
    marked = any(name in table.properties for name in properties)
    if marked:
        if any(table.properties.get(name) != value for name, value in properties.items()):
            raise ValueError("Incompatible history policy; quiesce writers and perform a migration")
    elif (snapshot := table.current_snapshot()) is not None:
        if len(table.scan(snapshot_id=snapshot.snapshot_id, limit=1).to_arrow()):
            raise ValueError(
                "Cannot adopt an unmarked nonempty table; perform a validated migration"
            )


def _equal(left: Any, right: Any) -> bool:
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and math.isnan(left)
        and math.isnan(right)
    ):
        return True
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _validate_value(value: Any, name: str, *, is_hash: bool = False) -> None:
    if value is None or (isinstance(value, (str, bytes)) and not value.strip()):
        raise ValueError(f"History column {name!r} contains a null or empty identity/hash")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"History column {name!r} contains a non-finite identity/hash")
    if is_hash and not isinstance(value, (str, bytes)):
        raise ValueError(f"History payload hash {name!r} must contain strings or bytes")
    try:
        hash(value)
    except TypeError as exc:
        raise ValueError(f"History identity {name!r} must contain scalar values") from exc


def _incoming(
    data: pa.Table, policy: HistoryPolicy
) -> tuple[list[dict[str, Any]], dict[tuple[Any, Any], list[int]]]:
    required = {policy.entity_key, policy.version_key, *policy.comparison_columns}
    missing = required - set(data.column_names)
    if missing:
        raise ValueError(f"History columns missing from source data: {sorted(missing)}")
    rows = data.to_pylist()
    groups: dict[tuple[Any, Any], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        for name in (policy.entity_key, policy.version_key):
            _validate_value(row[name], name)
        if policy.payload_hash_column:
            _validate_value(
                row[policy.payload_hash_column], policy.payload_hash_column, is_hash=True
            )
        groups[(row[policy.entity_key], row[policy.version_key])].append(index)
    return rows, groups


def _lookup(
    table: Table, policy: HistoryPolicy, groups: dict[tuple[Any, Any], list[int]]
) -> dict[tuple[Any, Any], list[tuple[Any, ...]]]:
    existing: dict[tuple[Any, Any], list[tuple[Any, ...]]] = defaultdict(list)
    snapshot = table.current_snapshot()
    if snapshot is None or not groups:
        return existing
    # Pin remote as well as local scan planning to the head used by append.
    fields = tuple(
        dict.fromkeys((policy.entity_key, policy.version_key, *policy.comparison_columns))
    )
    fields = tuple(name for name in fields if name in table.schema().column_names)
    identities = list(groups)
    for start in range(0, len(identities), 1000):
        rows = (
            table.scan(
                snapshot_id=snapshot.snapshot_id,
                row_filter=Or(
                    AlwaysFalse(),
                    *(
                        And(
                            EqualTo(term=Reference(policy.entity_key), value=entity),
                            EqualTo(term=Reference(policy.version_key), value=version),
                        )
                        for entity, version in identities[start : start + 1000]
                    ),
                ),
                selected_fields=fields,
            )
            .to_arrow()
            .to_pylist()
        )
        for row in rows:
            key = (row[policy.entity_key], row[policy.version_key])
            if key in groups:
                existing[key].append(tuple(row.get(name) for name in policy.comparison_columns))
    return existing


def _compare(
    rows: list[dict[str, Any]],
    groups: dict[tuple[Any, Any], list[int]],
    existing: dict[tuple[Any, Any], list[tuple[Any, ...]]],
    policy: HistoryPolicy,
) -> tuple[list[int], dict[str, int]]:
    indices = []
    skipped = conflicting = 0
    for key, members in groups.items():
        payloads = [
            tuple(rows[index][name] for name in policy.comparison_columns) for index in members
        ]
        payload = payloads[0]
        if any(not _equal(payload, other) for other in [*payloads[1:], *existing.get(key, [])]):
            conflicting += len(members)
        elif key in existing:
            skipped += len(members)
        else:
            indices.append(members[0])
            skipped += len(members) - 1
    metrics = {
        "rows_inserted": 0,
        "rows_deleted": 0,
        "rows_skipped": skipped,
        "rows_conflicting": conflicting,
    }
    if conflicting:
        raise HistoryConflictError(
            "Conflicting immutable history payloads; no changes committed", metrics=metrics
        )
    return indices, metrics


def _reconcile(
    catalog: Catalog,
    table_name: str,
    policy: HistoryPolicy,
    rows: list[dict[str, Any]],
    groups: dict[tuple[Any, Any], list[int]],
) -> dict[str, Any]:
    try:
        table = catalog.load_table(table_name)
        _check_policy(table, _policy_properties(table.schema(), policy))
        existing = _lookup(table, policy, groups)
        missing = conflicting = 0
        for key, members in groups.items():
            try:
                indices, _ = _compare(rows, {key: members}, existing, policy)
                missing += len(indices)
            except HistoryConflictError:
                conflicting += 1
        return {
            "state": "conflicting" if conflicting else "observed",
            "snapshot_id": _current_snapshot_id(table),
            "versions_present": len(groups) - missing - conflicting,
            "versions_missing": missing,
            "versions_conflicting": conflicting,
            "policy_bound": POLICY_PROPERTY in table.properties,
        }
    except Exception as exc:
        return {"state": "unavailable", "error_type": type(exc).__name__}


def history_to_table(
    table_name: str,
    data_paths: list[Path],
    policy: HistoryPolicy,
    ref: str = "main",
    *,
    schema_policy: SchemaPolicy = "strict",
) -> dict[str, int]:
    """Compare a complete staged batch and atomically append unseen versions.

    Three attempts are allowed for definite commit conflicts. Any other commit
    exception triggers readback and an explicit unknown-outcome failure.
    """
    validate_schema_policy(schema_policy)
    _require_direct_write(ref)
    if not data_paths:
        raise ValueError("History requires at least one staged Parquet file")
    batches = [pq.read_table(path) for path in data_paths]
    for batch in batches:
        _incoming(batch, policy)  # Reject missing/invalid fields before cross-file null backfill.
    source = pa.concat_tables(batches, promote_options="default")
    catalog = get_catalog(ref=ref)
    additions = pa.schema([])
    for attempt in range(3):
        table = catalog.load_table(table_name)
        _check_additions(table, additions)
        transaction, aligned, additions = prepare_arrow_write(
            table, source, table_name=table_name, schema_policy=schema_policy
        )
        # Fingerprint the staged schema, including IDs of new comparison fields.
        properties = _policy_properties(transaction.table_metadata.schema(), policy)
        _check_policy(table, properties)
        rows, groups = _incoming(aligned, policy)
        indices, metrics = _compare(rows, groups, _lookup(table, policy, groups), policy)
        if not indices:
            return metrics
        if POLICY_PROPERTY not in table.properties:
            transaction.set_properties(properties)
        try:
            with _iceberg_commit_scope(
                table_name,
                ref,
                "history",
                snapshot_id_before=_current_snapshot_id(table),
                rows_added=len(indices),
            ) as scope:
                transaction.append(aligned.take(pa.array(indices, type=pa.int64())))
                try:
                    transaction.commit_transaction()
                except CommitFailedException:
                    raise
                except Exception as exc:
                    reconciliation = _reconcile(catalog, table_name, policy, rows, groups)
                    raise HistoryCommitUnknownError(
                        "History commit outcome is unknown; readback reconciled, explicit recovery required",
                        metrics={},
                        reconciliation=reconciliation,
                    ) from exc
                _finish_iceberg_commit(scope, table)
        except CommitFailedException:
            if attempt == 2:
                raise
            continue
        metrics["rows_inserted"] = len(indices)
        return metrics
    raise AssertionError("Unreachable history retry state")


def _check_additions(table: Table, additions: pa.Schema) -> None:
    refreshed = schema_to_pyarrow(table.schema())
    for field in additions:
        if field.name in refreshed.names:
            existing = refreshed.field(field.name)
            if existing.type != field.type or existing.nullable != field.nullable:
                raise ValueError(
                    f"Conflicting concurrent definition for {table.name()}.{field.name}"
                )
