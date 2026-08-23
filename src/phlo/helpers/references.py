"""Reference-data contract helpers.

ReferenceContract describes a reference table: its composite key fields,
required fields, and optional effective-dating columns. Helpers detect
duplicate reference keys, fact keys absent from the reference, rows missing
required fields, and summarize overall fact coverage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ReferenceContract:
    """Descriptor for a reference table used by workflow facts."""

    name: str
    key_fields: list[str]
    required_fields: list[str] = field(default_factory=list)
    effective_start_field: str | None = None
    effective_end_field: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def reference_key(row: Mapping[str, Any], fields: list[str], *, separator: str = "|") -> str:
    """Build a composite reference key."""
    return separator.join(str(row.get(field, "")) for field in fields)


def assert_reference_unique(
    rows: Iterable[Mapping[str, Any]],
    contract: ReferenceContract,
) -> list[dict[str, Any]]:
    """Return duplicate reference keys for a contract."""
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        key = reference_key(row, contract.key_fields)
        if key in seen:
            duplicates.append({"key": key, **dict(row)})
        seen.add(key)
    return duplicates


def missing_reference_keys(
    facts: Iterable[Mapping[str, Any]],
    references: Iterable[Mapping[str, Any]],
    *,
    fact_fields: list[str],
    reference_fields: list[str],
) -> list[str]:
    """Return fact keys absent from reference rows."""
    fact_keys = {reference_key(row, fact_fields) for row in facts}
    ref_keys = {reference_key(row, reference_fields) for row in references}
    return sorted(fact_keys - ref_keys)


def reference_required_field_gaps(
    rows: Iterable[Mapping[str, Any]],
    contract: ReferenceContract,
) -> list[dict[str, Any]]:
    """Return rows missing required reference fields."""
    gaps: list[dict[str, Any]] = []
    for row in rows:
        missing = [field for field in contract.required_fields if row.get(field) in (None, "")]
        if missing:
            gaps.append({"key": reference_key(row, contract.key_fields), "missing": missing})
    return gaps


def reference_coverage_report(
    facts: Iterable[Mapping[str, Any]],
    references: Iterable[Mapping[str, Any]],
    *,
    fact_fields: list[str],
    reference_fields: list[str],
    source_system_field: str | None = None,
) -> dict[str, Any]:
    """Summarize how completely facts are covered by reference rows."""
    fact_rows = list(facts)
    reference_rows = list(references)
    fact_keys = {reference_key(row, fact_fields) for row in fact_rows}
    reference_keys = {reference_key(row, reference_fields) for row in reference_rows}
    missing = sorted(fact_keys - reference_keys)
    report: dict[str, Any] = {
        "fact_key_count": len(fact_keys),
        "reference_key_count": len(reference_keys),
        "covered_key_count": len(fact_keys & reference_keys),
        "missing_key_count": len(missing),
        "coverage_ratio": (len(fact_keys & reference_keys) / len(fact_keys)) if fact_keys else 1.0,
        "missing_keys": missing,
    }
    if source_system_field:
        by_source: dict[str, dict[str, Any]] = {}
        for row in fact_rows:
            source = str(row.get(source_system_field, "unknown"))
            key = reference_key(row, fact_fields)
            bucket = by_source.setdefault(source, {"fact_key_count": 0, "missing_key_count": 0})
            bucket["fact_key_count"] += 1
            if key not in reference_keys:
                bucket["missing_key_count"] += 1
        for bucket in by_source.values():
            total = bucket["fact_key_count"]
            bucket["coverage_ratio"] = (
                (total - bucket["missing_key_count"]) / total if total else 1.0
            )
        report["by_source_system"] = by_source
    return report
