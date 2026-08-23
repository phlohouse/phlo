"""Private helper functions for the @phlo_pandera decorator.

Internal implementation details for event emitter creation, Trino/DuckDB data
loading, metadata building, failure estimation, contract metadata, and SQL
reproduction. Not part of the public API; use ``decorator.py`` instead.

Example:
    These functions are used internally by the decorator:

    ```python
    from phlo_pandera.decorator_helpers import _make_emitters, _load_data

    # Create emitters for event publishing
    emitter, telemetry = _make_emitters(
        runtime=context,
        asset_key="customers",
        partition_key_value="2024-01-15",
        source="phlo",
        backend="trino",
    )

    # Load data from configured backend
    df = _load_data(runtime=context, query="SELECT * FROM bronze.customers", backend="trino")
    ```

"""

from __future__ import annotations

from typing import Any, List

from phlo.capabilities.runtime import RuntimeContext
from phlo.hooks.emitters import (
    QualityResultEventContext,
    QualityResultEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
)
from phlo.hooks.events import HookCorrelation

from phlo_pandera.checks import QualityCheckResult
from phlo_pandera.contract import QualityCheckContract


def _make_emitters(
    runtime: RuntimeContext,
    asset_key: str,
    partition_key_value: str | None,
    source: str,
    backend: str,
) -> tuple[QualityResultEventEmitter, TelemetryEventEmitter]:
    """Create quality-result and telemetry emitters wired with correlation
    context so check results and telemetry metrics track through the hooks
    system.

    Example:
        ```python
        emitter, telemetry = _make_emitters(
            runtime=context,
            asset_key="orders",
            partition_key_value="2024-01-15",
            source="phlo",
            backend="trino",
        )

        # Emit a quality result
        emitter.emit_result(
            check_name="null_check",
            passed=True,
            check_type="null",
            metadata={"null_count": 0},
        )
        ```

    """
    correlation = HookCorrelation(
        run_id=runtime.run_id,
        asset_key=asset_key,
        partition_key=partition_key_value,
        job_name=getattr(runtime, "job_name", None),
    )
    emitter = QualityResultEventEmitter(
        QualityResultEventContext(
            asset_key=asset_key,
            run_id=runtime.run_id,
            partition_key=partition_key_value,
            tags={"source": source, "backend": backend},
            correlation=correlation,
        )
    )
    telemetry = TelemetryEventEmitter(
        TelemetryEventContext(
            tags={
                "asset": asset_key,
                "source": source,
                "backend": backend,
            },
            correlation=correlation,
        )
    )
    return emitter, telemetry


def _load_data(
    runtime: RuntimeContext,
    query: str,
    backend: str,
) -> Any:
    """Resolve the backend resource and load query results as a DataFrame,
    dispatching on the "trino"/"duckdb" backend name.

    Raises ValueError when an unknown backend is specified.

    Example:
        ```python
        df = _load_data(
            runtime=context,
            query="SELECT * FROM bronze.events",
            backend="trino",
        )
        ```

    """
    if backend == "trino":
        trino = _resolve_trino_resource(runtime)
        return _load_data_trino(runtime, query, trino)
    elif backend == "duckdb":
        duckdb_conn = _resolve_duckdb_connection(runtime)
        return _load_data_duckdb(runtime, query, duckdb_conn)
    else:
        raise ValueError(f"Unknown backend: {backend}")


def _load_data_trino(context: RuntimeContext, query: str, trino: Any) -> Any:
    """Execute a query via a Trino cursor and build a pandas DataFrame using
    column names from cursor description metadata.

    Raises ValueError when Trino returns no column metadata.

    Example:
        ```python
        trino = _resolve_trino_resource(context)
        df = _load_data_trino(context, "SELECT * FROM events", trino)
        ```

    """
    import pandas as pd

    # Execute query
    with trino.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()

        if not cursor.description:
            raise ValueError("Trino did not return column metadata")

        columns = [desc[0] for desc in cursor.description]

    # Convert to DataFrame
    df = pd.DataFrame(rows, columns=columns)

    context.logger.info("loaded_rows_from_trino", row_count=len(df))

    return df


def _resolve_trino_resource(context: RuntimeContext) -> Any:
    """Resolve a Trino resource from the context's resources or fall back to a
    new default TrinoResource.

    Raises ValueError when no resource can be found or created.

    Example:
        ```python
        trino = _resolve_trino_resource(context)
        ```

    """
    trino = None
    resources = context.resources
    if isinstance(resources, dict):
        trino = resources.get("trino")
    elif resources is not None:
        trino = getattr(resources, "trino", None)
    if trino is None:
        try:
            trino = context.get_resource("trino")
        except Exception:
            trino = None
    if trino is None:
        try:
            from phlo_trino.resource import TrinoResource
        except Exception as exc:  # noqa: BLE001 - surface missing backend cleanly
            raise ValueError(
                "Trino resource not found in context and phlo_trino is not available"
            ) from exc
        trino = TrinoResource()
    return trino


def _resolve_duckdb_connection(context: RuntimeContext) -> Any:
    """Resolve a DuckDB connection from the context's resources or fall back to
    a new in-memory connection.

    Raises ValueError when no connection can be found or created.

    Example:
        ```python
        duckdb_conn = _resolve_duckdb_connection(context)
        ```

    """
    duckdb_conn = None
    resources = context.resources
    if isinstance(resources, dict):
        duckdb_conn = resources.get("duckdb")
    elif resources is not None:
        duckdb_conn = getattr(resources, "duckdb", None)
    if duckdb_conn is None:
        try:
            duckdb_conn = context.get_resource("duckdb")
        except Exception:
            duckdb_conn = None
    if duckdb_conn is None:
        try:
            import duckdb
        except Exception as exc:  # noqa: BLE001 - surface missing backend cleanly
            raise ValueError(
                "DuckDB resource not found in context and duckdb is not available"
            ) from exc
        duckdb_conn = duckdb.connect()
    return duckdb_conn


def _load_data_duckdb(context: RuntimeContext, query: str, duckdb_conn: Any) -> Any:
    """Execute a query via DuckDB and return the results as a pandas DataFrame
    (via fetchdf()).

    Example:
        ```python
        duckdb_conn = _resolve_duckdb_connection(context)
        df = _load_data_duckdb(context, "SELECT * FROM local_table", duckdb_conn)
        ```

    """

    # Execute query
    df = duckdb_conn.execute(query).fetchdf()

    context.logger.info("loaded_rows_from_duckdb", row_count=len(df))

    return df


def _build_metadata(df: Any, check_results: List[QualityCheckResult]) -> dict[str, Any]:
    """Aggregate check results into a metadata dictionary for Dagster metadata
    and observability: row/column counts, pass/fail counts, individual metrics,
    and a Markdown quality summary table.

    Example:
        ```python
        results = [null_result, range_result]
        metadata = _build_metadata(df, results)
        # metadata contains:
        # - rows_validated, columns_validated
        # - checks_executed, checks_passed, checks_failed
        # - Individual results keyed by metric_name
        # - quality_summary: Markdown table
        ```

    """
    metadata: dict[str, Any] = {
        "rows_validated": len(df),
        "columns_validated": len(df.columns),
        "checks_executed": len(check_results),
        "checks_passed": sum(1 for r in check_results if r.passed),
        "checks_failed": sum(1 for r in check_results if not r.passed),
    }

    # Add individual check results
    for result in check_results:
        # Add metric value
        if result.metric_value is not None:
            metadata[f"{result.metric_name}_value"] = result.metric_value

        # Add check metadata
        if result.metadata:
            for key, value in result.metadata.items():
                metadata_key = f"{result.metric_name}_{key}"
                metadata[metadata_key] = value

    # Build quality summary table
    summary_rows = []
    for result in check_results:
        summary_rows.append(
            f"| {result.metric_name} | {'✅ Pass' if result.passed else '❌ Fail'} | "
            f"{result.metric_value} | {result.failure_message or '-'} |"
        )

    if summary_rows:
        summary_table = (
            "## Quality Check Results\n\n"
            "| Check | Status | Value | Message |\n"
            "|-------|--------|-------|----------|\n" + "\n".join(summary_rows)
        )
        metadata["quality_summary"] = summary_table

    return metadata


def _estimate_failed_count(check_results: List[QualityCheckResult]) -> int:
    """Estimate total failed rows from check-result metadata (failed_rows,
    failure_count, duplicate_count, out_of_range, non_match_count), falling
    back to counting failed checks when no row counts are available.

    Example:
        ```python
        failed_count = _estimate_failed_count(check_results)
        # Returns: sum of detected failures across all checks
        ```

    """
    failed_count = 0
    for result in check_results:
        if result.passed:
            continue
        metadata = result.metadata or {}
        for key in (
            "failed_rows",
            "failure_count",
            "duplicate_count",
            "out_of_range",
            "non_match_count",
        ):
            value = metadata.get(key)
            if isinstance(value, int):
                failed_count += value
                break
    if failed_count > 0:
        return failed_count
    return sum(1 for r in check_results if not r.passed)


def _collect_failure_sample(check_results: List[QualityCheckResult]) -> list[dict[str, Any]]:
    """Collect sample failure rows from failed checks, capped at 20 items and
    each tagged with its producing check name.

    Example:
        ```python
        sample = _collect_failure_sample(check_results)
        # Returns: [{"check": "null_check", "row_index": 5, "column": "email"}, ...]
        ```

    """
    sample: list[dict[str, Any]] = []
    for result in check_results:
        if result.passed:
            continue
        rows = result.metadata.get("sample_rows") if result.metadata else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sample.append({"check": result.metric_name, **row})
            if len(sample) >= 20:
                return sample
    return sample


def _contract_metadata(contract: QualityCheckContract) -> dict[str, Any]:
    """Flatten a QualityCheckContract's non-None fields into a metadata dict.

    Example:
        ```python
        contract = QualityCheckContract(
            source="phlo",
            failed_count=5,
            partition_key="2024-01-15",
        )
        metadata = _contract_metadata(contract)
        # Returns: {"source": "phlo", "failed_count": 5, "partition_key": "2024-01-15"}
        ```

    """
    metadata: dict[str, Any] = {"source": contract.source, "failed_count": contract.failed_count}
    if contract.partition_key is not None:
        metadata["partition_key"] = contract.partition_key
    if contract.total_count is not None:
        metadata["total_count"] = contract.total_count
    if contract.query_or_sql is not None:
        metadata["query_or_sql"] = contract.query_or_sql
    if contract.repro_sql is not None:
        metadata["repro_sql"] = contract.repro_sql
    if contract.sample is not None:
        metadata["sample"] = contract.sample[:20]
    return metadata


def _repro_sql(query: str) -> str:
    """Wrap a query in a LIMIT 100 subquery so it is safe for ad-hoc
    reproduction and debugging.

    Example:
        ```python
        repro = _repro_sql("SELECT * FROM large_table")
        # Returns:
        # SELECT *
        # FROM (
        # SELECT * FROM large_table
        # ) AS phlo_pandera
        # LIMIT 100
        ```

    """
    return f"SELECT *\nFROM (\n{query}\n) AS phlo_pandera\nLIMIT 100"
