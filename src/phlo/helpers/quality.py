"""Quality helper factories and checks that do not require a provider import.

Produces provider-neutral QualityRule descriptors (null, unique, freshness,
accepted values) from schemas and SLAs, plus in-memory uniqueness
validation and schema nullability helpers. Importable without any quality
provider installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from phlo.capabilities import FieldSpec, NormalizedSchema
from phlo.contracts import SLA


@dataclass(frozen=True, slots=True)
class QualityRule:
    """Provider-neutral quality rule descriptor."""

    kind: str
    columns: list[str]
    parameters: dict[str, Any]


def required_field_null_rules(schema: NormalizedSchema) -> list[QualityRule]:
    """Generate null-check rules for required fields."""
    return [
        QualityRule("not_null", [field.name], {}) for field in schema.fields if not field.nullable
    ]


def unique_key_rule(unique_key: str | list[str]) -> QualityRule:
    """Generate a uniqueness rule for one or more key columns."""
    columns = [unique_key] if isinstance(unique_key, str) else list(unique_key)
    return QualityRule("unique", columns, {})


def freshness_rule_from_sla(
    sla: SLA,
    *,
    timestamp_column: str = "_phlo_ingested_at",
) -> QualityRule | None:
    """Generate a freshness rule from an SLA when freshness is configured."""
    if sla.freshness_hours is None:
        return None
    return QualityRule(
        "freshness",
        [timestamp_column],
        {"max_age_hours": sla.freshness_hours},
    )


def accepted_values_rule(column: str, values: list[Any]) -> QualityRule:
    """Generate an accepted-values rule descriptor."""
    return QualityRule("accepted_values", [column], {"values": list(values)})


def validate_unique_key_rows(
    rows: list[Mapping[str, Any]],
    unique_key: str | list[str],
) -> dict[str, Any]:
    """Validate uniqueness for in-memory row dictionaries."""
    keys = [unique_key] if isinstance(unique_key, str) else list(unique_key)
    seen: set[tuple[Any, ...]] = set()
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        value = tuple(row.get(key) for key in keys)
        if value in seen:
            duplicates.append({key: row.get(key) for key in keys})
        seen.add(value)
    return {"passed": not duplicates, "duplicates": duplicates, "duplicate_count": len(duplicates)}


def nullability_from_schema(schema: NormalizedSchema) -> dict[str, bool]:
    """Return column nullability keyed by field name."""
    return {field.name: field.nullable for field in schema.fields}


def schema_with_required(schema: NormalizedSchema, *columns: str) -> NormalizedSchema:
    """Return a copy of a schema with selected fields marked required."""
    required = set(columns)
    return NormalizedSchema(
        fields=[
            FieldSpec(
                name=field.name,
                dtype=field.dtype,
                nullable=False if field.name in required else field.nullable,
                default=field.default,
                metadata=field.metadata,
            )
            for field in schema.fields
        ],
        metadata=schema.metadata,
    )
