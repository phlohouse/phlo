"""Contract metadata models shared across ingestion and quality APIs.

SLA and Consumer are frozen value objects exchanged inside capability
metadata payloads; the serialize_* helpers convert them to plain dicts
and normalize_consumers accepts bare strings as name-only consumers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SLA:
    """Service-level agreement metadata for a data asset."""

    freshness_hours: int | None = None
    quality_threshold: float = 1.0
    max_failures: int | None = None
    notify: list[str] | None = None


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
    return asdict(sla)


__all__ = ["Consumer", "SLA", "normalize_consumers", "serialize_consumers", "serialize_sla"]
