"""Provider-neutral quality rule factories.

Factories producing QualityRule records (not_null, unique, freshness, range,
accepted_values) without binding to any execution provider. Invalid ranges
and empty accepted-value lists raise ValueError at construction time, before
a rule can reach a pipeline.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from phlo.helpers.quality import QualityRule
from phlo.helpers.sql import table_ref_sql
from phlo.references import LogicalRelation


def not_null(*columns: str) -> QualityRule:
    """Create a not-null quality rule for one or more columns."""
    return QualityRule(kind="not_null", columns=list(columns), parameters={})


def unique(*columns: str) -> QualityRule:
    """Create a uniqueness quality rule for one or more columns."""
    return QualityRule(kind="unique", columns=list(columns), parameters={})


def freshness(
    column: str,
    *,
    hours: float,
    reference_time: datetime | None = None,
    require_timestamps: bool = False,
) -> QualityRule:
    """Create a freshness quality rule for a timestamp column."""
    parameters: dict[str, Any] = {"max_age_hours": hours}
    if reference_time is not None:
        parameters["reference_time"] = reference_time
    if require_timestamps:
        parameters["require_timestamps"] = True
    return QualityRule(kind="freshness", columns=[column], parameters=parameters)


def range_between(
    column: str,
    *,
    min_value: float | int | None = None,
    max_value: float | int | None = None,
) -> QualityRule:
    """Create a numeric range quality rule."""
    if min_value is None and max_value is None:
        raise ValueError("range_between requires min_value, max_value, or both")
    return QualityRule(
        kind="range",
        columns=[column],
        parameters={"min_value": min_value, "max_value": max_value},
    )


def accepted_values(column: str, values: list[Any]) -> QualityRule:
    """Create an accepted-values quality rule."""
    accepted = list(values)
    if not accepted:
        raise ValueError("accepted_values requires at least one value")
    return QualityRule(kind="accepted_values", columns=[column], parameters={"values": accepted})


def grain(*columns: str) -> QualityRule:
    """Require non-null, jointly unique business identities, including composite keys.

    Individual columns may repeat. Empty datasets pass. Missing columns fail,
    and any null key component fails even when its tuple occurs only once.
    """
    if not columns or any(not column for column in columns) or len(set(columns)) != len(columns):
        raise ValueError("grain requires distinct, non-empty key columns")
    return QualityRule("grain", list(columns), {})


def _constraint_relation_sql(relation: str | LogicalRelation) -> str:
    """Render a relation within the runtime connection's selected catalog/ref."""
    if isinstance(relation, LogicalRelation):
        if not relation.is_resolved:
            raise ValueError(f"Unresolved constraint relation: {relation.asset_key}")
        if relation.relation:
            relation = relation.relation
        else:
            relation = ".".join(part for part in (relation.schema, relation.table) if part)
    parts = relation.split(".")
    if not relation or len(parts) > 2 or any(not part for part in parts):
        raise ValueError("Constraint relations require table or schema.table, not a pinned catalog")
    table_ref_sql(relation)
    return table_ref_sql(*parts, quote_parts=True)


def reconcile_aggregate(
    *,
    name: str,
    source: str | LogicalRelation,
    source_column: str,
    target_column: str,
    aggregate: str = "sum",
    source_filters: Mapping[str, Any] | None = None,
    target_filters: Mapping[str, Any] | None = None,
    source_partition_column: str | None = None,
    absolute_tolerance: float = 0.0,
) -> QualityRule:
    """Compare SQL aggregates over independently filtered source and target rows.

    Filters are equality predicates (None means IS NULL). The decorator's
    partition/window scope applies to both relations; source_partition_column
    overrides its source column. SUM/AVG/MIN/MAX of empty or all-null scopes
    fail, even on both sides; COUNT(column) returns zero and may match zero.
    Missing relations/columns and non-finite aggregates fail. Tolerance is an
    inclusive absolute difference. Logical references must already resolve;
    their schema/table use the runtime connection's catalog, never a pinned ref.
    """
    if aggregate not in {"sum", "avg", "min", "max", "count"}:
        raise ValueError(f"Unsupported reconciliation aggregate: {aggregate}")
    if not name or not source_column or not target_column:
        raise ValueError("reconcile_aggregate requires a name and source and target columns")
    if not math.isfinite(absolute_tolerance) or absolute_tolerance < 0:
        raise ValueError("absolute_tolerance must be finite and non-negative")
    return QualityRule(
        "reconcile_aggregate",
        [target_column],
        {
            "name": name,
            "source": _constraint_relation_sql(source),
            "source_column": source_column,
            "aggregate": aggregate,
            "source_filters": dict(source_filters or {}),
            "target_filters": dict(target_filters or {}),
            "source_partition_column": source_partition_column,
            "absolute_tolerance": absolute_tolerance,
        },
    )


__all__ = [
    "accepted_values",
    "freshness",
    "grain",
    "not_null",
    "range_between",
    "reconcile_aggregate",
    "unique",
]
