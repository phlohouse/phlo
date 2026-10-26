"""Contract metadata models shared across ingestion and quality APIs.

SLA and Consumer are frozen value objects exchanged inside capability
metadata payloads; the serialize_* helpers convert them to plain dicts
and normalize_consumers accepts bare strings as name-only consumers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SLA:
    """Service-level agreement metadata for a data asset."""

    freshness_hours: int | None = None
    quality_threshold: float = 1.0
    max_failures: int | None = None
    notify: list[str] | None = None
    freshness_column: str | None = field(default=None, kw_only=True)
    freshness_reference_time: datetime | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        """Require a threshold and column for explicit data freshness semantics."""
        if self.freshness_column is None and self.freshness_reference_time is None:
            return
        if not self.freshness_column or not self.freshness_column.strip():
            raise ValueError("Explicit SLA freshness requires freshness_column")
        if self.freshness_hours is None or self.freshness_hours < 0:
            raise ValueError("Explicit SLA freshness requires non-negative freshness_hours")
        reference = self.freshness_reference_time
        if reference is not None and reference.utcoffset() is None:
            raise ValueError("freshness_reference_time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Consumer:
    """Downstream consumer metadata for a data asset."""

    name: str
    contact: str | None = None
    usage: str | None = None


def normalize_consumers(consumers: list[Consumer | str] | None) -> list[Consumer]:
    """Normalize string and Consumer inputs into a Consumer list."""
    if not consumers:
        return []

    normalized: list[Consumer] = []
    for consumer in consumers:
        if isinstance(consumer, Consumer):
            normalized.append(consumer)
            continue
        normalized.append(Consumer(name=str(consumer)))
    return normalized


def serialize_consumers(consumers: list[Consumer]) -> list[dict[str, Any]]:
    """Serialize consumers for capability metadata payloads."""
    return [asdict(consumer) for consumer in consumers]


def serialize_sla(sla: SLA | None) -> dict[str, Any] | None:
    """Serialize SLA for capability metadata payloads."""
    if sla is None:
        return None
    result = asdict(sla)
    # Preserve the legacy payload shape when the new semantics are not used.
    if sla.freshness_column is None:
        result.pop("freshness_column")
    if sla.freshness_reference_time is None:
        result.pop("freshness_reference_time")
    else:
        result["freshness_reference_time"] = sla.freshness_reference_time.isoformat()
    return result


__all__ = ["Consumer", "SLA", "normalize_consumers", "serialize_consumers", "serialize_sla"]
