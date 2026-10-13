"""Execute built-in quality checks using bounded SQL aggregate results."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pandas as pd

from phlo_pandera.checks import (
    CountCheck,
    NullCheck,
    QualityCheck,
    QualityCheckResult,
    RangeCheck,
    UniqueCheck,
)


def supports_aggregation(check: QualityCheck) -> bool:
    """Do not bypass execute overrides on user-defined subclasses."""
    return type(check) in (CountCheck, NullCheck, RangeCheck, UniqueCheck)


def _bound_condition(column: str, operator: str, value: float) -> str:
    """Match pandas comparisons with non-finite bounds in both SQL engines."""
    if math.isnan(value):
        return "FALSE"
    literal = str(float(value)).replace("inf", "Infinity")
    return f"{column} {operator} CAST('{literal}' AS DOUBLE)"


def execute_aggregate_check(
    check: QualityCheck,
    *,
    query: str,
    columns: list[str],
    floating_columns: set[str],
    row_count: int,
    backend: str,
    fetch: Callable[[str], pd.DataFrame],
) -> QualityCheckResult:
    """Fetch aggregate scalars and at most twenty failure rows, not the dataset."""
    source = f"({query.rstrip().rstrip(';')}) AS phlo_quality"
    index_column = "__phlo_quality_index"
    while index_column in columns:
        index_column += "_"
    indexed = (
        f"(SELECT *, ROW_NUMBER() OVER () - 1 AS {index_column} FROM {source}) AS phlo_indexed"
    )

    def quoted(column: str) -> str:
        return '"' + column.replace('"', '""') + '"'

    def null(column: str) -> str:
        name = quoted(column)
        if column in floating_columns:
            function = "is_nan" if backend == "trino" else "isnan"
            return f"({name} IS NULL OR {function}({name}))"
        return f"{name} IS NULL"

    def samples(condition: str, selected: list[str]) -> list[dict[str, Any]]:
        projection = ", ".join(quoted(column) for column in selected)
        frame = fetch(
            f"SELECT {index_column} AS row_index, {projection} FROM {indexed} "
            f"WHERE {condition} ORDER BY {index_column} LIMIT 20"
        )
        return frame.to_dict("records")

    if isinstance(check, CountCheck):
        return check.result_from_count(row_count)
    if isinstance(check, NullCheck):
        selected = [column for column in check.columns if column in columns]
        expressions = [
            f"COALESCE(SUM(CASE WHEN {null(column)} THEN 1 ELSE 0 END), 0)" for column in selected
        ]
        values = (
            fetch(f"SELECT {', '.join(expressions)} FROM {source}").iloc[0].tolist()
            if expressions
            else []
        )
        return check.result_from_counts(
            row_count,
            dict(zip(selected, map(int, values), strict=True)),
            lambda column: samples(null(column), selected),
        )
    if isinstance(check, RangeCheck):
        if check.column not in columns:
            return check.execute(pd.DataFrame(columns=columns), None)
        name = quoted(check.column)
        bounds = []
        if check.min_value is not None:
            bounds.append(_bound_condition(name, "<", check.min_value))
        if check.max_value is not None:
            bounds.append(_bound_condition(name, ">", check.max_value))
        condition = f"NOT ({null(check.column)}) AND ({' OR '.join(bounds) or 'FALSE'})"
        values = (
            fetch(
                f"SELECT COUNT(*), MIN({name}), MAX({name}), "
                f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0) "
                f"FROM {source} WHERE NOT ({null(check.column)})"
            )
            .iloc[0]
            .tolist()
        )
        valid, minimum, maximum, violations = values
        return check.result_from_counts(
            int(valid),
            int(violations),
            float(minimum) if valid else None,
            float(maximum) if valid else None,
            samples(condition, [check.column]) if violations else [],
        )
    if isinstance(check, UniqueCheck):
        if not check.columns or any(column not in columns for column in check.columns):
            return check.execute(pd.DataFrame(columns=columns), None)
        # Canonicalise IEEE NaN to NULL, matching pandas' duplicate grouping.
        keys = ", ".join(
            f"CASE WHEN {null(column)} THEN NULL ELSE {quoted(column)} END"
            for column in check.columns
        )
        duplicate_column = "__phlo_duplicates"
        while duplicate_column in columns:
            duplicate_column += "_"
        duplicates = f"(SELECT *, COUNT(*) OVER (PARTITION BY {keys}) AS {duplicate_column} FROM {indexed}) AS phlo_duplicates"
        count = int(
            fetch(f"SELECT COUNT(*) FROM {duplicates} WHERE {duplicate_column} > 1").iloc[0, 0]
        )
        projection = ", ".join(quoted(column) for column in check.columns)
        sample = (
            fetch(
                f"SELECT {index_column} AS row_index, {projection} FROM {duplicates} "
                f"WHERE {duplicate_column} > 1 ORDER BY {index_column} LIMIT 20"
            ).to_dict("records")
            if count
            else []
        )
        return check.result_from_counts(row_count, count, sample)
    raise TypeError(f"Check {type(check).__name__} does not support aggregate execution")
