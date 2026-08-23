"""Pandera contract evaluation and asset check utilities.

This module provides functions for evaluating Pandera schema contracts against
pandas DataFrames and parquet files. It handles schema validation, error collection,
and conversion to Phlo's standardized CheckResult format.

The module is designed to integrate Pandera's powerful schema validation with
Phlo's quality check framework, providing:

1. **Schema Evaluation**: Validate DataFrames against Pandera DataFrameModel classes
2. **Type Coercion**: Automatic datetime conversion for improved compatibility
3. **Parquet Support**: Load and validate parquet files directly
4. **Result Conversion**: Convert Pandera results to Phlo CheckResult format

Example:
    ```python
    import pandas as pd
    from pandera.pandas import DataFrameModel, Field
    from phlo_pandera.pandera_asset_checks import (
        evaluate_pandera_contract,
        pandera_contract_asset_check_result,
    )

    class CustomerSchema(DataFrameModel):
        customer_id: int = Field(gt=0)
        email: str = Field(nullable=True)

    # Validate a DataFrame
    df = pd.DataFrame({
        "customer_id": [1, 2, 3],
        "email": ["alice@example.com", "bob@example.com", None],
    })

    evaluation = evaluate_pandera_contract(df, schema_class=CustomerSchema)

    # Convert to Phlo CheckResult
    result = pandera_contract_asset_check_result(
        evaluation=evaluation,
        partition_key="2024-01-15",
        asset_key="customers",
        schema_class=CustomerSchema,
        query_or_sql="SELECT * FROM bronze.customers",
    )
    ```

See Also:
    - ``checks_extra.py``: SchemaCheck class that uses these utilities
    - ``decorator.py``: ``@phlo_pandera`` decorator with Pandera integration
    - ``contract.py``: QualityCheckContract for metadata standardization

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pandera.errors
from pandera.engines import pandas_engine
from pandera.pandas import DataFrameModel

from phlo.capabilities.specs import CheckResult
from phlo.logging import get_logger
from phlo_pandera.contract import PANDERA_CONTRACT_CHECK_NAME, QualityCheckContract
from phlo_pandera.severity import severity_for_pandera_contract

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PanderaContractEvaluation:
    """Result summary for Pandera schema contract evaluation.

    Standardized summary independent of Pandera's internal error types.

    Example:
        ```python
        evaluation = PanderaContractEvaluation(
            passed=False,
            failed_count=5,
            total_count=1000,
            sample=[{"column": "age", "error": "value 200 exceeds maximum 150"}],
            error=None,
        )
        ```
    """

    passed: bool
    failed_count: int
    total_count: int
    sample: list[dict[str, Any]]
    error: str | None = None


def evaluate_pandera_contract(
    df: pd.DataFrame,
    *,
    schema_class: type[DataFrameModel],
) -> PanderaContractEvaluation:
    """Validate a DataFrame against a Pandera ``DataFrameModel`` class (not an instance).

    Uses Pandera's lazy validation mode to collect all errors, converting
    datetime-declared columns automatically for compatibility.

    Unexpected errors are caught and logged, returning a failed evaluation.

    Example:
        ```python
        from pandera.pandas import DataFrameModel, Field

        class ProductSchema(DataFrameModel):
            product_id: int = Field(gt=0)
            price: float = Field(ge=0)

        df = pd.DataFrame({
            "product_id": [1, 2, -3],  # -3 fails gt=0 constraint
            "price": [9.99, -1.0, 5.0],  # -1.0 fails ge=0 constraint
        })

        evaluation = evaluate_pandera_contract(df, schema_class=ProductSchema)
        # evaluation.passed == False
        # evaluation.failed_count >= 2
        ```
    """

    schema = schema_class.to_schema()
    datetime_columns = [
        name
        for name, column in schema.columns.items()
        if isinstance(column.dtype, pandas_engine.DateTime)
    ]
    # Pandera rejects object/string columns on a datetime dtype check, so
    # coerce them in place first; values that cannot be parsed are left alone
    # and surface as ordinary validation failures below.
    for column_name in datetime_columns:
        if column_name not in df.columns:
            continue
        series = df[column_name]
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        try:
            df[column_name] = pd.to_datetime(series)
        except (ValueError, TypeError):
            pass

    try:
        schema_class.validate(df, lazy=True)
    except pandera.errors.SchemaErrors as err:
        failure_cases = err.failure_cases
        sample = failure_cases.head(20).to_dict(orient="records")
        return PanderaContractEvaluation(
            passed=False,
            failed_count=len(failure_cases),
            total_count=len(df),
            sample=sample,
            error=str(err),
        )
    except Exception as exc:
        logger.exception(
            "pandera_contract_evaluation_failed",
            schema_name=schema_class.__name__,
            total_count=len(df),
        )
        return PanderaContractEvaluation(
            passed=False,
            failed_count=1,
            total_count=len(df),
            sample=[{"error": str(exc)}],
            error=str(exc),
        )

    return PanderaContractEvaluation(
        passed=True,
        failed_count=0,
        total_count=len(df),
        sample=[],
    )


def evaluate_pandera_contract_parquet(
    parquet_path: Path,
    *,
    schema_class: type[DataFrameModel],
) -> PanderaContractEvaluation:
    """Load a parquet file and validate it against a Pandera schema class.

    Parquet read errors are logged and re-raised.

    Example:
        ```python
        from pathlib import Path

        path = Path("data/products.parquet")
        evaluation = evaluate_pandera_contract_parquet(
            parquet_path=path,
            schema_class=ProductSchema,
        )
        ```
    """

    try:
        df = pd.read_parquet(parquet_path)
    except Exception:
        logger.exception(
            "pandera_contract_parquet_read_failed",
            schema_name=schema_class.__name__,
            parquet_path=str(parquet_path),
        )
        raise
    return evaluate_pandera_contract(df, schema_class=schema_class)


def pandera_contract_asset_check_result(
    evaluation: PanderaContractEvaluation,
    *,
    partition_key: str | None,
    asset_key: str,
    schema_class: type[DataFrameModel],
    query_or_sql: str,
) -> CheckResult:
    """Build a Phlo quality check result from Pandera evaluation output.

    Converts a PanderaContractEvaluation into a standardized Phlo CheckResult
    with proper metadata, severity assignment, and contract information;
    severity is None when passed and "error" when failed.

    Example:
        ```python
        evaluation = evaluate_pandera_contract(df, schema_class=CustomerSchema)

        result = pandera_contract_asset_check_result(
            evaluation=evaluation,
            partition_key="2024-01-15",
            asset_key="customers",
            schema_class=CustomerSchema,
            query_or_sql="SELECT * FROM bronze.customers",
        )

        # result.passed: bool
        # result.check_name: "pandera_contract"
        # result.severity: None if passed, "error" if failed
        ```
    """

    contract = QualityCheckContract(
        source="pandera",
        partition_key=partition_key,
        failed_count=evaluation.failed_count,
        total_count=evaluation.total_count,
        query_or_sql=query_or_sql,
        repro_sql=None,
        sample=evaluation.sample,
    )
    metadata: dict[str, Any] = {
        **contract.to_metadata(),
        "schema": schema_class.__name__,
    }
    if evaluation.error:
        metadata["error"] = evaluation.error

    severity = severity_for_pandera_contract(passed=evaluation.passed)
    return CheckResult(
        passed=evaluation.passed,
        check_name=PANDERA_CONTRACT_CHECK_NAME,
        metadata=metadata,
        severity=severity,
        asset_key=asset_key,
    )
