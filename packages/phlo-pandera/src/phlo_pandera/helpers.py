"""Convenience factories for common Phlo Pandera quality checks.

Derives null, uniqueness, freshness (from SLA metadata), and accepted-values
checks from schema and contract metadata. Accepted-values checks are built
as custom SQL with identifiers and literals safely quoted.
"""

from __future__ import annotations

from typing import Any, Iterable

from pandera.pandas import DataFrameModel

from phlo.contracts import SLA
from phlo_pandera.checks import FreshnessCheck, NullCheck, QualityCheck, UniqueCheck
from phlo_pandera.checks_extra import CustomSQLCheck
from phlo_pandera.schema_extractor import PanderaSchemaExtractor


def required_field_null_checks(
    schema: type[DataFrameModel],
    *,
    allow_threshold: float = 0.0,
) -> list[NullCheck]:
    """Create one null check covering all non-nullable fields in a schema."""
    normalized = PanderaSchemaExtractor().extract(schema)
    required_columns = [field.name for field in normalized.fields if not field.nullable]
    return (
        [NullCheck(columns=required_columns, allow_threshold=allow_threshold)]
        if required_columns
        else []
    )


def unique_key_check(
    unique_key: str | Iterable[str] | None,
    *,
    allow_threshold: float = 0.0,
) -> UniqueCheck | None:
    """Create a uniqueness check for a single-column or composite key."""
    if unique_key is None:
        return None
    columns = [unique_key] if isinstance(unique_key, str) else list(unique_key)
    if not columns:
        return None
    return UniqueCheck(columns=columns, allow_threshold=allow_threshold)


def freshness_check_from_sla(
    sla: SLA | None,
    timestamp_column: str,
    *,
    reference_time: Any = None,
) -> FreshnessCheck | None:
    """Create a freshness check from SLA freshness metadata."""
    if sla is None or sla.freshness_hours is None:
        return None
    return FreshnessCheck(
        timestamp_column=timestamp_column,
        max_age_hours=float(sla.freshness_hours),
        reference_time=reference_time,
    )


def accepted_values_check(
    column: str,
    values: Iterable[str | int | float | bool],
    *,
    allow_threshold: float = 0.0,
    include_nulls: bool = False,
) -> CustomSQLCheck:
    """Create an accepted-values check using the existing custom SQL check."""
    accepted = list(values)
    if not accepted:
        raise ValueError("accepted_values_check requires at least one accepted value")

    value_sql = ", ".join(_sql_literal(value) for value in accepted)
    null_clause = (
        " OR {column} IS NULL".format(column=_quote_identifier(column)) if include_nulls else ""
    )
    quoted_column = _quote_identifier(column)
    sql = f"SELECT ({quoted_column} IN ({value_sql}){null_clause}) AS is_valid FROM data"
    return CustomSQLCheck(
        name_=f"accepted_values_{column}",
        sql=sql,
        allow_threshold=allow_threshold,
    )


def checks_from_contract(
    *,
    schema: type[DataFrameModel] | None = None,
    unique_key: str | Iterable[str] | None = None,
    sla: SLA | None = None,
    freshness_column: str | None = None,
    allow_threshold: float = 0.0,
) -> list[QualityCheck]:
    """Build a compact set of common checks from schema and contract metadata."""
    checks: list[QualityCheck] = []
    if schema is not None:
        checks.extend(required_field_null_checks(schema, allow_threshold=allow_threshold))

    unique = unique_key_check(unique_key, allow_threshold=allow_threshold)
    if unique is not None:
        checks.append(unique)

    if freshness_column is not None:
        freshness = freshness_check_from_sla(sla, freshness_column)
        if freshness is not None:
            checks.append(freshness)

    return checks


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


__all__ = [
    "accepted_values_check",
    "checks_from_contract",
    "freshness_check_from_sla",
    "required_field_null_checks",
    "unique_key_check",
]
