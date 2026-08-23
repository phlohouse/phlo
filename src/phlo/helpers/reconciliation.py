"""Reconciliation helpers for comparing source and target datasets.

Row checksums hash canonical JSON so comparisons stay order-stable. Every
check returns a ReconciliationResult with matched counts and bounded mismatch
samples rather than raising on differences.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Result for a source/target reconciliation."""

    passed: bool
    source_value: Any
    target_value: Any
    metric: str
    metadata: dict[str, Any] = field(default_factory=dict)


def reconcile_counts(
    source_count: int,
    target_count: int,
    *,
    tolerance: int = 0,
) -> ReconciliationResult:
    """Compare source and target row counts."""
    delta = abs(source_count - target_count)
    return ReconciliationResult(
        passed=delta <= tolerance,
        source_value=source_count,
        target_value=target_count,
        metric="row_count",
        metadata={"delta": delta, "tolerance": tolerance},
    )


def reconcile_aggregates(
    source: Mapping[str, float],
    target: Mapping[str, float],
    *,
    tolerance: float = 0.0,
) -> list[ReconciliationResult]:
    """Compare aggregate metrics by name."""
    results: list[ReconciliationResult] = []
    for key, source_value in source.items():
        target_value = target.get(key)
        delta = abs(float(source_value) - float(target_value or 0))
        results.append(
            ReconciliationResult(
                passed=target_value is not None and delta <= tolerance,
                source_value=source_value,
                target_value=target_value,
                metric=key,
                metadata={"delta": delta, "tolerance": tolerance},
            )
        )
    return results


def row_checksum(row: Mapping[str, Any], *, columns: Iterable[str] | None = None) -> str:
    """Return a stable checksum for a row mapping."""
    selected = {key: row.get(key) for key in (columns or sorted(row))}
    payload = json.dumps(selected, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reconcile_checksums(
    source_rows: Iterable[Mapping[str, Any]],
    target_rows: Iterable[Mapping[str, Any]],
    *,
    columns: Iterable[str] | None = None,
) -> ReconciliationResult:
    """Compare checksum sets for two row collections."""
    source = {row_checksum(row, columns=columns) for row in source_rows}
    target = {row_checksum(row, columns=columns) for row in target_rows}
    return ReconciliationResult(
        passed=source == target,
        source_value=len(source),
        target_value=len(target),
        metric="checksum_set",
        metadata={
            "missing_in_target": sorted(source - target)[:20],
            "extra_in_target": sorted(target - source)[:20],
        },
    )


def _sort_values(values: Iterable[Any]) -> list[Any]:
    return sorted(values, key=lambda value: str(value))


def reconcile_key_sets(
    source_keys: Iterable[Any],
    target_keys: Iterable[Any],
    *,
    sample_size: int = 20,
) -> ReconciliationResult:
    """Compare source and target entity-key sets."""
    source = set(source_keys)
    target = set(target_keys)
    missing = source - target
    extra = target - source
    return ReconciliationResult(
        passed=not missing and not extra,
        source_value=len(source),
        target_value=len(target),
        metric="key_set",
        metadata={
            "missing_count": len(missing),
            "extra_count": len(extra),
            "common_count": len(source & target),
            "missing_in_target": _sort_values(missing)[:sample_size],
            "extra_in_target": _sort_values(extra)[:sample_size],
            "sample_size": sample_size,
        },
    )


def compare_partitions(source: Iterable[str], target: Iterable[str]) -> dict[str, list[str]]:
    """Compare two partition key collections."""
    source_set = set(source)
    target_set = set(target)
    return {
        "missing_in_target": sorted(source_set - target_set),
        "extra_in_target": sorted(target_set - source_set),
        "common": sorted(source_set & target_set),
    }
