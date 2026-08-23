"""Generic event-ledger helpers.

Normalizes raw rows into EventRecord values and derives quality signals
from them: latest event per entity, adjacent state-transition counts,
observation lag, and missing or duplicate sequence numbers. Pure
functions; no I/O and no dependency on any orchestrator.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Normalized operational event record."""

    entity_key: str
    event_type: str
    event_time: datetime
    source_system: str | None = None
    event_id: str | None = None
    actor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def event_record(
    *,
    entity_key: str,
    event_type: str,
    event_time: datetime,
    source_system: str | None = None,
    event_id: str | None = None,
    actor: str | None = None,
    **metadata: Any,
) -> EventRecord:
    """Create a normalized event record."""
    return EventRecord(
        entity_key=entity_key,
        event_type=event_type,
        event_time=event_time,
        source_system=source_system,
        event_id=event_id,
        actor=actor,
        metadata=metadata,
    )


def events_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    entity_key_field: str,
    event_type_field: str,
    event_time_field: str,
) -> list[EventRecord]:
    """Build event records from mappings."""
    events: list[EventRecord] = []
    for row in rows:
        events.append(
            EventRecord(
                entity_key=str(row[entity_key_field]),
                event_type=str(row[event_type_field]),
                event_time=row[event_time_field],
                source_system=str(row["source_system"]) if row.get("source_system") else None,
                event_id=str(row["event_id"]) if row.get("event_id") else None,
                actor=str(row["actor"]) if row.get("actor") else None,
                metadata={
                    str(key): value
                    for key, value in row.items()
                    if key
                    not in {
                        entity_key_field,
                        event_type_field,
                        event_time_field,
                        "source_system",
                        "event_id",
                        "actor",
                    }
                },
            )
        )
    return events


def latest_event_per_key(events: Iterable[EventRecord]) -> dict[str, EventRecord]:
    """Return the latest event for each entity key."""
    latest: dict[str, EventRecord] = {}
    for event in events:
        current = latest.get(event.entity_key)
        if current is None or event.event_time > current.event_time:
            latest[event.entity_key] = event
    return latest


def state_transition_counts(events: Iterable[EventRecord]) -> dict[tuple[str, str], int]:
    """Count adjacent event-type transitions per entity."""
    by_entity: dict[str, list[EventRecord]] = {}
    for event in events:
        by_entity.setdefault(event.entity_key, []).append(event)
    counts: dict[tuple[str, str], int] = {}
    for entity_events in by_entity.values():
        ordered = sorted(entity_events, key=lambda event: event.event_time)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            key = (previous.event_type, current.event_type)
            counts[key] = counts.get(key, 0) + 1
    return counts


def event_lag_seconds(event: EventRecord, observed_at: datetime) -> float:
    """Return ingestion/observation lag for one event."""
    return (observed_at - event.event_time).total_seconds()


def _event_field(record: EventRecord | Mapping[str, Any], field: str) -> Any:
    if isinstance(record, EventRecord):
        if hasattr(record, field):
            return getattr(record, field)
        return record.metadata.get(field)
    return record.get(field)


def event_sequence_gaps(
    records: Iterable[EventRecord | Mapping[str, Any]],
    *,
    entity_key_field: str = "entity_key",
    sequence_field: str = "sequence",
) -> list[dict[str, Any]]:
    """Find missing or duplicate integer sequence numbers per entity."""
    by_entity: dict[str, list[int]] = {}
    for record in records:
        entity = _event_field(record, entity_key_field)
        sequence = _event_field(record, sequence_field)
        if entity is None or sequence is None:
            continue
        by_entity.setdefault(str(entity), []).append(int(sequence))

    gaps: list[dict[str, Any]] = []
    for entity, sequences in sorted(by_entity.items()):
        ordered = sorted(sequences)
        seen: set[int] = set()
        for sequence in ordered:
            if sequence in seen:
                gaps.append(
                    {
                        "entity_key": entity,
                        "kind": "duplicate",
                        "sequence": sequence,
                    }
                )
            seen.add(sequence)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current - previous > 1:
                gaps.append(
                    {
                        "entity_key": entity,
                        "kind": "gap",
                        "previous_sequence": previous,
                        "current_sequence": current,
                        "missing_sequences": list(range(previous + 1, current)),
                    }
                )
    return gaps
