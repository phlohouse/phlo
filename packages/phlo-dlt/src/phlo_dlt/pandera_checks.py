"""Pandera contract checks for DLT ingestion assets.

This module provides Pandera schema validation integration for DLT-based
ingestion pipelines. It handles the evaluation of data contracts against
staged Parquet files and converts validation results into Phlo-compatible
check results.

Key Components:
    - :class:`PanderaContractEvaluation`: Result container for validation outcomes
    - :class:`PanderaContractValidationError`: Exception for validation failures
    - :func:`evaluate_pandera_contract`: Validate DataFrame against schema
    - :func:`evaluate_pandera_contract_parquet`: Validate single Parquet file
    - :func:`evaluate_pandera_contract_parquet_files`: Validate multiple Parquet files
    - :func:`pandera_contract_asset_check_result`: Convert to Phlo check result
    - :func:`serialize_pandera_contract_evaluation`: Serialize evaluation to dict
    - :func:`deserialize_pandera_contract_evaluation`: Deserialize from dict

Validation Flow:
    1. DLT extracts data to Parquet files
    2. Parquet files are validated against Pandera schema
    3. Results are converted to Phlo check results
    4. In strict mode, failures abort before data is visible

See Also:
    - :mod:`phlo_dlt.decorator`: Decorator that orchestrates validation
    - :mod:`phlo_dlt.executor`: Executor that triggers validation
    - Pandera documentation: https://pandera.readthedocs.io/

"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from phlo.capabilities.specs import CheckResult

PANDERA_CONTRACT_CHECK_NAME = "pandera_contract"
_ISO_TIMESTAMP_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?")
_PANDERA_INSTALL_HINT = (
    "pandera is required for validation; install the quality provider "
    "(for example, pip install phlo-pandera or pip install pandera)"
)


def _dlt_normalization_hint(failure_cases: pd.DataFrame, schema: Any) -> str | None:
    """Detect string-typed contract fields defeated by DLT's ISO-8601 staging.

    DLT normalizes ISO-8601 strings to timestamps while staging to parquet, so
    a contract field typed ``Series[str]`` fails pattern checks against
    timestamp values. When that signature appears, return an actionable hint.
    """
    try:
        hinted: set[str] = set()
        for case in failure_cases.to_dict("records"):
            column = case.get("column")
            column_config = schema.columns.get(column) if column is not None else None
            if column_config is None or "str" not in str(column_config.dtype).lower():
                continue
            failure_value = str(case.get("failure_case", ""))
            if _ISO_TIMESTAMP_LIKE.search(failure_value):
                hinted.add(str(column))
        if hinted:
            return (
                "Hint: DLT normalizes ISO-8601 source values to timestamps during "
                f"staging; type {sorted(hinted)} as Series[datetime] (or coerce the "
                "source before returning it)."
            )
    except Exception:  # noqa: BLE001 - hints are best-effort only
        return None
    return None


def _pandera_schema_errors() -> type[Exception]:
    """Return Pandera's lazy validation exception class."""
    try:
        import pandera.errors
    except ModuleNotFoundError as exc:
        raise ImportError(_PANDERA_INSTALL_HINT) from exc

    return pandera.errors.SchemaErrors


def _pandas_datetime_engine() -> type[Any]:
    """Return Pandera's pandas datetime dtype marker."""
    try:
        from pandera.engines import pandas_engine
    except ModuleNotFoundError as exc:
        raise ImportError(_PANDERA_INSTALL_HINT) from exc

    return pandas_engine.DateTime


@dataclass(frozen=True, slots=True)
class PanderaContractEvaluation:
    """Result summary for Pandera schema contract evaluation.

    Captures pass/fail status, failed and total record counts, up to 20
    sample failure cases, and the error message when validation raised.

    Example:
        ```python
        evaluation = PanderaContractEvaluation(
            passed=True,
            failed_count=0,
            total_count=1000,
            sample=[],
            error=None,
        )
        ```

    """

    passed: bool
    failed_count: int
    total_count: int
    sample: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PanderaContractValidationError(RuntimeError):
    """Raised when strict Pandera validation fails before a visible write.

    Raised when strict validation is enabled and the contract check fails;
    carries the evaluation details and the Parquet paths that were validated.

    Example:
        ```python
        try:
            evaluation = evaluate_pandera_contract_parquet(path, schema_class=MySchema)
            if not evaluation.passed:
                raise PanderaContractValidationError(
                    evaluation=evaluation,
                    parquet_paths=(path,)
                )
        except PanderaContractValidationError as e:
            print(f"Validation failed: {e.evaluation.error}")
        ```

    """

    evaluation: PanderaContractEvaluation
    parquet_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        """Initialize the RuntimeError base class with a standard message."""
        RuntimeError.__init__(self, "Pandera contract validation failed")


def _nullable_series_for_schema_column(column: Any, size: int) -> pd.Series[Any]:
    """Create a null-filled Series using the closest pandas dtype for a schema column."""
    column_dtype = getattr(column, "dtype", None)
    if isinstance(column_dtype, _pandas_datetime_engine()):
        return pd.Series([pd.NaT] * size, dtype="datetime64[ns]")

    dtype_name = str(column_dtype).lower()
    nullable_dtype_map = {
        "string": "string[pyarrow]",
        "string[pyarrow]": "string[pyarrow]",
        "int64": "Int64",
        "int32": "Int32",
        "int16": "Int16",
        "int8": "Int8",
        "uint64": "UInt64",
        "uint32": "UInt32",
        "uint16": "UInt16",
        "uint8": "UInt8",
        "float64": "Float64",
        "float32": "Float32",
        "bool": "boolean",
        "boolean": "boolean",
    }
    dtype = nullable_dtype_map.get(dtype_name)
    if dtype is None:
        return pd.Series([None] * size, dtype="object")
    return pd.Series([None] * size, dtype=dtype)


def evaluate_pandera_contract_parquet(
    parquet_path: Path,
    *,
    schema_class: type[Any],
) -> PanderaContractEvaluation:
    """Load parquet data and validate it against a Pandera schema class.

    Reads the Parquet file into a DataFrame and validates it against
    ``schema_class`` (a Pandera DataFrameModel subclass).

    Example:
        ```python
        from pathlib import Path
        from phlo_dlt.pandera_checks import evaluate_pandera_contract_parquet

        result = evaluate_pandera_contract_parquet(
            Path("/tmp/data.parquet"),
            schema_class=UserSchema,
        )
        print(f"Passed: {result.passed}, Failed: {result.failed_count}")
        ```

    See Also:
        :func:`evaluate_pandera_contract`: Core validation logic.
        :func:`evaluate_pandera_contract_parquet_files`: For multiple files.

    """
    df = pd.read_parquet(parquet_path)
    return evaluate_pandera_contract(df, schema_class=schema_class)


def evaluate_pandera_contract_parquet_files(
    parquet_paths: list[Path],
    *,
    schema_class: type[Any],
) -> PanderaContractEvaluation:
    """Load one or more parquet files and validate them as a single staged dataset.

    Concatenates the files into a single DataFrame before validating, for
    ingestion runs that produce multiple Parquet files. Raises
    FileNotFoundError when ``parquet_paths`` is empty.

    Example:
        ```python
        from pathlib import Path
        from phlo_dlt.pandera_checks import evaluate_pandera_contract_parquet_files

        paths = [Path("/tmp/part1.parquet"), Path("/tmp/part2.parquet")]
        result = evaluate_pandera_contract_parquet_files(
            paths,
            schema_class=UserSchema,
        )
        ```

    See Also:
        :func:`evaluate_pandera_contract_parquet`: For single file validation.

    """
    if not parquet_paths:
        raise FileNotFoundError("Missing parquet_paths in ingestion metadata")
    frames = [pd.read_parquet(parquet_path) for parquet_path in parquet_paths]
    return evaluate_pandera_contract(
        pd.concat(frames, ignore_index=True), schema_class=schema_class
    )


def evaluate_pandera_contract(
    df: pd.DataFrame,
    *,
    schema_class: type[Any],
) -> PanderaContractEvaluation:
    """Validate a dataframe against a Pandera schema class.

    Validates ``df`` against a Pandera DataFrameModel subclass. Before
    validating, null columns are added for missing nullable fields and
    string/object datetime columns are coerced; failures from either path
    surface in the evaluation result.

    Example:
        ```python
        import pandas as pd
        from phlo_dlt.pandera_checks import evaluate_pandera_contract

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        result = evaluate_pandera_contract(df, schema_class=UserSchema)
        ```

    """
    schema = schema_class.to_schema()
    missing_nullable = [
        name for name, col in schema.columns.items() if col.nullable and name not in df.columns
    ]
    if missing_nullable:
        validated_df = df.copy()
        for column_name in missing_nullable:
            validated_df[column_name] = _nullable_series_for_schema_column(
                schema.columns[column_name],
                size=len(validated_df),
            )
    else:
        validated_df = df
    # Best-effort datetime coercion: only string/object columns are parsed and
    # a failed parse is left untouched so Pandera reports it as a failure case.

    datetime_columns = [
        name
        for name, column in schema.columns.items()
        if isinstance(column.dtype, _pandas_datetime_engine())
    ]
    for column_name in datetime_columns:
        if column_name not in validated_df.columns:
            continue
        series = validated_df[column_name]
        if pd.api.types.is_datetime64_any_dtype(series):
            continue
        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        try:
            if validated_df is df:
                validated_df = df.copy()
            validated_df[column_name] = pd.to_datetime(series)
        except (ValueError, TypeError):
            pass

    try:
        schema_class.validate(validated_df, lazy=True)
    except _pandera_schema_errors() as err:
        failure_cases = getattr(err, "failure_cases")
        sample = failure_cases.head(20).to_dict(orient="records")
        error_text = str(err)
        hint = _dlt_normalization_hint(failure_cases, schema)
        if hint:
            error_text = f"{error_text} {hint}"
        return PanderaContractEvaluation(
            passed=False,
            failed_count=len(failure_cases),
            total_count=len(validated_df),
            sample=sample,
            error=error_text,
        )
    except Exception as exc:  # noqa: BLE001 - surface validation errors in check metadata
        return PanderaContractEvaluation(
            passed=False,
            failed_count=1,
            total_count=len(validated_df),
            sample=[{"error": str(exc)}],
            error=str(exc),
        )

    return PanderaContractEvaluation(
        passed=True,
        failed_count=0,
        total_count=len(df),
        sample=[],
    )


def pandera_contract_asset_check_result(
    evaluation: PanderaContractEvaluation,
    *,
    partition_key: str | None,
    asset_key: str,
    schema_class: type[Any],
    query_or_sql: str,
    blocking: bool = True,
) -> CheckResult:
    """Build a normalized Phlo check result from Pandera evaluation output.

    Converts an evaluation into a CheckResult for the orchestrator and UI,
    tagging it with the asset key, partition key, schema name, source
    query, counts, sample failures, and error severity on failure.

    Example:
        ```python
        from phlo_dlt.pandera_checks import (
            evaluate_pandera_contract_parquet,
            pandera_contract_asset_check_result,
        )

        evaluation = evaluate_pandera_contract_parquet(path, schema_class=MySchema)
        check_result = pandera_contract_asset_check_result(
            evaluation,
            partition_key="2024-01-01",
            asset_key="dlt_users",
            schema_class=MySchema,
            query_or_sql="parquet:///tmp/data.parquet",
        )
        ```

    """
    metadata: dict[str, Any] = {
        "source": "pandera",
        "blocking": blocking,
        "partition_key": partition_key,
        "failed_count": evaluation.failed_count,
        "total_count": evaluation.total_count,
        "query_or_sql": query_or_sql,
        "sample": evaluation.sample[:20],
        "schema": schema_class.__name__,
    }
    if evaluation.error:
        metadata["error"] = evaluation.error

    return CheckResult(
        passed=evaluation.passed,
        check_name=PANDERA_CONTRACT_CHECK_NAME,
        metadata=metadata,
        severity=None if evaluation.passed else ("error" if blocking else "warn"),
        asset_key=asset_key,
    )


def serialize_pandera_contract_evaluation(
    evaluation: PanderaContractEvaluation,
) -> dict[str, Any]:
    """Convert a Pandera contract evaluation to metadata-safe primitives.

    Serializes an evaluation to primitive values so results survive
    storage in JSON/YAML metadata between pipeline stages.

    Example:
        ```python
        evaluation = PanderaContractEvaluation(
            passed=True, failed_count=0, total_count=100, sample=[], error=None
        )
        metadata = serialize_pandera_contract_evaluation(evaluation)
        # Can now be stored in JSON/YAML metadata
        ```

    See Also:
        :func:`deserialize_pandera_contract_evaluation`: Reverse operation.

    """
    return {
        "passed": evaluation.passed,
        "failed_count": evaluation.failed_count,
        "total_count": evaluation.total_count,
        "sample": evaluation.sample,
        "error": evaluation.error,
    }


def deserialize_pandera_contract_evaluation(payload: Any) -> PanderaContractEvaluation | None:
    """Convert metadata payload back into a Pandera contract evaluation.

    Rebuilds an evaluation from a payload produced by
    serialize_pandera_contract_evaluation, coercing types as needed;
    returns None when ``payload`` is not a dictionary.

    Example:
        ```python
        metadata = {"passed": True, "failed_count": 0, "total_count": 100, "sample": []}
        evaluation = deserialize_pandera_contract_evaluation(metadata)
        if evaluation:
            print(f"Validation passed: {evaluation.passed}")
        ```

    See Also:
        :func:`serialize_pandera_contract_evaluation`: Forward operation.

    """
    if not isinstance(payload, dict):
        return None
    sample = payload.get("sample")
    return PanderaContractEvaluation(
        passed=bool(payload.get("passed")),
        failed_count=int(payload.get("failed_count", 0)),
        total_count=int(payload.get("total_count", 0)),
        sample=sample if isinstance(sample, list) else [],
        error=str(payload["error"]) if payload.get("error") is not None else None,
    )
