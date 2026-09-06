"""Schema policy enforcement for Kafka consumer assets.

Additive compatible changes (new fields, type widening) auto-register on the
destination. Incompatible changes halt the consumer, retain the source
offsets uncommitted, and route the offending records to the dead-letter topic
so an explicit schema migration is required before the pipeline resumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WIDENING_RULES: dict[str, set[str]] = {
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
}


@dataclass(frozen=True)
class SchemaDecision:
    """Outcome of evaluating records against the destination schema."""

    decision: str  # "compatible" | "incompatible"
    reason: str | None = None
    new_fields: list[str] = field(default_factory=list)


def _normalizes_to(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "other"


def classify_field_change(*, field_name: str, existing_type: str, incoming_type: str) -> str:
    """Classify one field type change as "safe", "warning", or "breaking"."""
    existing = existing_type.lower()
    incoming = incoming_type.lower()
    if existing == incoming or incoming in WIDENING_RULES.get(existing, set()):
        return "safe"
    return "breaking"


def evaluate_field_types(
    *, existing_schema: dict[str, str], records: list[dict[str, Any]]
) -> SchemaDecision:
    """Compare record value types against the existing typed schema.

    Incompatible (breaking) type conflicts make the batch ineligible for the
    destination; the caller must dead-letter the batch and retain offsets.
    """
    for record in records:
        for key, value in record.items():
            existing_type = existing_schema.get(key)
            if existing_type is None or value is None:
                continue
            incoming = _normalizes_to(value)
            if (
                classify_field_change(
                    field_name=key,
                    existing_type=existing_type,
                    incoming_type=incoming,
                )
                == "breaking"
            ):
                return SchemaDecision(
                    decision="incompatible",
                    reason=(
                        f"Field {key!r} changed from {existing_type!r} to {incoming!r}; "
                        "a schema migration is required."
                    ),
                )
    return SchemaDecision(decision="compatible")
