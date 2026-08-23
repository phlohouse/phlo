"""Generic cross-system identity mapping helpers.

Each CrosswalkEntry maps one (source_system, source_id) pair to one canonical_id
with a confidence score; collisions onto multiple canonical IDs are surfaced by
detect_crosswalk_collisions rather than silently resolved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    """Mapping from one source-system identifier to one canonical identifier."""

    source_system: str
    source_id: str
    canonical_id: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


def build_crosswalk(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_system_field: str = "source_system",
    source_id_field: str = "source_id",
    canonical_id_field: str = "canonical_id",
    confidence_field: str = "confidence",
) -> list[CrosswalkEntry]:
    """Build crosswalk entries from row dictionaries."""
    entries: list[CrosswalkEntry] = []
    for row in rows:
        entries.append(
            CrosswalkEntry(
                source_system=str(row[source_system_field]),
                source_id=str(row[source_id_field]),
                canonical_id=str(row[canonical_id_field]),
                confidence=float(row.get(confidence_field, 1.0) or 1.0),
                metadata={
                    str(key): value
                    for key, value in row.items()
                    if key
                    not in {
                        source_system_field,
                        source_id_field,
                        canonical_id_field,
                        confidence_field,
                    }
                },
            )
        )
    return entries


def crosswalk_lookup(entries: Iterable[CrosswalkEntry]) -> dict[tuple[str, str], str]:
    """Return lookup keyed by `(source_system, source_id)`."""
    return {(entry.source_system, entry.source_id): entry.canonical_id for entry in entries}


def map_source_id(
    entries: Iterable[CrosswalkEntry],
    *,
    source_system: str,
    source_id: str,
    default: str | None = None,
) -> str | None:
    """Map one source identifier to a canonical identifier."""
    return crosswalk_lookup(entries).get((source_system, source_id), default)


def detect_crosswalk_collisions(
    entries: Iterable[CrosswalkEntry],
) -> dict[tuple[str, str], list[str]]:
    """Find source IDs that map to multiple canonical IDs."""
    mappings: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        mappings.setdefault((entry.source_system, entry.source_id), set()).add(entry.canonical_id)
    return {key: sorted(values) for key, values in mappings.items() if len(values) > 1}


def unmapped_source_ids(
    observed: Iterable[tuple[str, str]],
    entries: Iterable[CrosswalkEntry],
) -> list[tuple[str, str]]:
    """Return observed source identifiers missing from a crosswalk."""
    known = set(crosswalk_lookup(entries))
    return sorted(set(observed) - known)


def canonical_groups(entries: Iterable[CrosswalkEntry]) -> dict[str, list[tuple[str, str]]]:
    """Group source identifiers by canonical identifier."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for entry in entries:
        groups.setdefault(entry.canonical_id, []).append((entry.source_system, entry.source_id))
    return {key: sorted(values) for key, values in groups.items()}


def crosswalk_coverage_report(
    observed: Iterable[tuple[str, str]],
    entries: Iterable[CrosswalkEntry],
) -> dict[str, Any]:
    """Summarize mapped, unmapped, and colliding source identifiers."""
    observed_set = set(observed)
    entry_list = list(entries)
    lookup = crosswalk_lookup(entry_list)
    collisions = detect_crosswalk_collisions(entry_list)
    unmapped = sorted(observed_set - set(lookup))
    mapped_count = len(observed_set) - len(unmapped)
    return {
        "observed_count": len(observed_set),
        "mapped_count": mapped_count,
        "unmapped_count": len(unmapped),
        "collision_count": len(collisions),
        "coverage_ratio": mapped_count / len(observed_set) if observed_set else 1.0,
        "unmapped_source_ids": unmapped,
        "collisions": collisions,
    }
