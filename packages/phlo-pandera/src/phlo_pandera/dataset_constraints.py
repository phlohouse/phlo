"""SQL-only execution for neutral dataset identity and reconciliation rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Any

import pandas as pd

from phlo.capabilities.runtime import RuntimeContext
from phlo.helpers.quality import QualityRule
from phlo.helpers.sql import literal, quote_identifier
from phlo_pandera.checks import QualityCheck, QualityCheckResult
from phlo_pandera.partitioning import PartitionScope, apply_partition_scope


def _filtered(query: str, filters: Mapping[str, Any]) -> str:
    predicates = [
        f"{quote_identifier(column)} IS NULL"
        if value is None
        else f"{quote_identifier(column)} = {literal(value)}"
        for column, value in filters.items()
    ]
    sql = f"SELECT * FROM ({query.rstrip().rstrip(';')}) AS phlo_constraint"
    return sql + (" WHERE " + " AND ".join(predicates) if predicates else "")


@dataclass
class DatasetConstraintCheck(QualityCheck):
    """A translated neutral constraint requiring query-backed execution."""

    rule: QualityRule

    @property
    def name(self) -> str:
        """Identify separate metrics without conflating different aggregates."""
        if self.rule.kind == "grain":
            return "grain_" + "_".join(self.rule.columns)
        return self.rule.parameters["name"]

    def execute(self, df: pd.DataFrame, context: RuntimeContext | None) -> QualityCheckResult:
        """Reject a dataframe fallback rather than silently scanning full tables."""
        raise ValueError("Dataset constraints require the query-backed phlo.quality.rules path")

    def execute_query(
        self,
        *,
        query: str,
        scope: PartitionScope,
        fetch: Callable[[str], pd.DataFrame],
    ) -> QualityCheckResult:
        """Fetch aggregate scalars only, retaining both queries as evidence."""
        if self.rule.kind == "grain":
            keys = ", ".join(quote_identifier(column) for column in self.rule.columns)
            nulls = " OR ".join(
                f"{quote_identifier(column)} IS NULL" for column in self.rule.columns
            )
            sql = (
                f"SELECT COUNT(*) FROM (SELECT {keys} FROM ({query}) AS phlo_grain "
                f"GROUP BY {keys} HAVING COUNT(*) > 1 OR {nulls}) AS phlo_violations"
            )
            violations = int(fetch(sql).iloc[0, 0])
            return QualityCheckResult(
                passed=violations == 0,
                metric_name=self.name,
                metric_value=violations,
                metadata={"invalid_identity_groups": violations, "query": sql},
                failure_message=f"{violations} duplicate or null business identities"
                if violations
                else None,
            )

        parameters = self.rule.parameters
        source_scope = replace(
            scope,
            partition_column=quote_identifier(
                parameters.get("source_partition_column") or scope.partition_column
            ),
        )
        source = apply_partition_scope(f"SELECT * FROM {parameters['source']}", scope=source_scope)
        source = _filtered(source, parameters["source_filters"])
        target = _filtered(query, parameters["target_filters"])
        aggregate = parameters["aggregate"].upper()
        source_sql = (
            f"SELECT CAST({aggregate}({quote_identifier(parameters['source_column'])}) AS VARCHAR) "
            f"FROM ({source}) AS phlo_source"
        )
        target_sql = (
            f"SELECT CAST({aggregate}({quote_identifier(self.rule.columns[0])}) AS VARCHAR) "
            f"FROM ({target}) AS phlo_target"
        )
        source_raw = fetch(source_sql).iloc[0, 0]
        target_raw = fetch(target_sql).iloc[0, 0]
        source_value = Decimal(source_raw) if pd.notna(source_raw) else Decimal("NaN")
        target_value = Decimal(target_raw) if pd.notna(target_raw) else Decimal("NaN")
        valid = source_value.is_finite() and target_value.is_finite()
        delta = None
        if valid:
            with localcontext() as context:
                context.prec = (
                    max(source_value.adjusted(), target_value.adjusted())
                    - min(
                        int(source_value.as_tuple().exponent), int(target_value.as_tuple().exponent)
                    )
                    + 2
                )
                delta = abs(source_value - target_value)
        passed = delta is not None and delta <= Decimal(str(parameters["absolute_tolerance"]))
        return QualityCheckResult(
            passed=passed,
            metric_name=self.name,
            metric_value={
                "source": str(source_value) if source_value.is_finite() else None,
                "target": str(target_value) if target_value.is_finite() else None,
                "delta": str(delta) if delta is not None else None,
            },
            metadata={
                "source_query": source_sql,
                "target_query": target_sql,
                "absolute_tolerance": parameters["absolute_tolerance"],
                "aggregate_defined": bool(valid),
            },
            failure_message=None if passed else "Aggregate reconciliation differs or is undefined",
        )
