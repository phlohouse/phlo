"""Bitemporal scope and SQL predicate helpers.

Predicates treat NULL bounds as open-ended and use half-open interval
semantics (``start <= t < end``), so rows valid from the beginning of time or
still current match any timestamp inside their range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from phlo.helpers.partitions import PartitionScope
from phlo.helpers.sql import literal, table_ref_sql, where_and


@dataclass(frozen=True, slots=True)
class BitemporalScope:
    """Scope records by business validity and system observation time."""

    valid_at: date | datetime | str | None = None
    observed_at: date | datetime | str | None = None
    valid_from_column: str = "valid_from"
    valid_to_column: str = "valid_to"
    observed_from_column: str = "observed_from"
    observed_to_column: str = "observed_to"


def valid_at_predicate(
    value: date | datetime | str,
    *,
    start_column: str = "valid_from",
    end_column: str = "valid_to",
) -> str:
    """Render an effective-time predicate."""
    start = table_ref_sql(start_column)
    end = table_ref_sql(end_column)
    rendered = literal(value)
    return f"({start} IS NULL OR {start} <= {rendered}) AND ({end} IS NULL OR {rendered} < {end})"


def observed_at_predicate(
    value: date | datetime | str,
    *,
    start_column: str = "observed_from",
    end_column: str = "observed_to",
) -> str:
    """Render a system-observation-time predicate."""
    return valid_at_predicate(value, start_column=start_column, end_column=end_column)


def bitemporal_predicate(scope: BitemporalScope) -> str | None:
    """Render a combined bitemporal predicate."""
    predicates: list[str] = []
    if scope.valid_at is not None:
        predicates.append(
            valid_at_predicate(
                scope.valid_at,
                start_column=scope.valid_from_column,
                end_column=scope.valid_to_column,
            )
        )
    if scope.observed_at is not None:
        predicates.append(
            observed_at_predicate(
                scope.observed_at,
                start_column=scope.observed_from_column,
                end_column=scope.observed_to_column,
            )
        )
    return where_and(*predicates) or None


def as_of_query_scope(
    *,
    partition_scope: PartitionScope | None = None,
    bitemporal_scope: BitemporalScope | None = None,
    predicates: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Render one predicate body for partition, valid-time, and observed-time scopes."""
    from phlo.helpers.sql import render_partition_predicate

    parts: list[str | None] = list(predicates)
    if partition_scope is not None:
        parts.insert(0, render_partition_predicate(partition_scope))
    if bitemporal_scope is not None:
        parts.append(bitemporal_predicate(bitemporal_scope))
    return where_and(*parts) or None
