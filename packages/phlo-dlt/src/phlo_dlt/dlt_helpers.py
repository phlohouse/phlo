"""Shared helper utilities for DLT ingestion execution.

This module provides common utilities used across the DLT ingestion pipeline,
including branch resolution, metadata injection, schema validation, and
table store operations. These helpers abstract away common patterns to keep
the main executor and decorator logic clean.

Key Functions:
    - :func:`generate_row_id`: Generate unique ULID-based row identifiers
    - :func:`get_branch_from_context`: Resolve target branch from runtime context
    - :func:`get_write_branch_from_context`: Determine effective write branch
    - :func:`inject_metadata_columns`: Add Phlo metadata columns to Parquet files
    - :func:`validate_with_pandera`: Validate records against Pandera schema
    - :func:`setup_dlt_pipeline`: Configure filesystem-backed DLT pipeline
    - :func:`stage_to_parquet`: Extract data to Parquet via DLT
    - :func:`merge_to_table_store`: Write staged data to table store

Constants:
    - ``DLT_TABLE_STORE_SUPPORT``: Capability support config for refs
    - ``WAP_TAG_KEY``: Tag key for Write-Audit-Publish branch isolation

Metadata Columns:
    The following columns are injected into ingested data:
    - ``_phlo_row_id``: ULID-based unique row identifier
    - ``_phlo_ingested_at``: UTC timestamp of ingestion
    - ``_phlo_partition_date``: Partition date (YYYY-MM-DD)
    - ``_phlo_run_id``: Orchestrator run identifier

See Also:
    - :mod:`phlo_dlt.executor`: Uses these helpers for ingestion execution
    - :mod:`phlo_dlt.decorator`: Orchestrates helper usage
    - DLT documentation: https://dlthub.com/docs

"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dlt
import pandas as pd
import ulid
from dlt.common.pipeline import LoadInfo
from phlo.capabilities import CapabilitySupport, resolve_runtime_ref
from phlo.capabilities.interfaces import TableStore
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger

from phlo_dlt.registry import TableConfig

logger = get_logger(__name__)
DLT_TABLE_STORE_SUPPORT = CapabilitySupport(supports_refs=True)
WAP_TAG_KEY = "phlo/wap_branch"


def generate_row_id() -> str:
    """Return a globally unique ULID string for ingestion metadata.

    The identifier tracks individual rows through the ingestion pipeline.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import generate_row_id

        row_id = generate_row_id()
        print(row_id)  # "01HV8J3K2M4N5P6Q7R8S9T0UV"
        ```

    See Also:
        https://github.com/ulid/spec for ULID specification.

    """
    return str(ulid.ULID())


def get_branch_from_context(context: Any) -> str:
    """Return the table-store branch resolved from the runtime context.

    Uses canonical runtime routing via capability support, defaulting to
    "main" when no specific routing is configured.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import get_branch_from_context

        branch = get_branch_from_context(runtime_context)
        print(f"Writing to branch: {branch}")
        ```

    """
    return (
        resolve_runtime_ref(
            context,
            support=DLT_TABLE_STORE_SUPPORT,
            default_ref="main",
        )
        or "main"
    )


def get_write_branch_from_context(context: Any, *, strict_validation: bool) -> str:
    """Return the branch writes should target for the current ingestion run.

    With strict_validation enabled, a WAP (Write-Audit-Publish) branch tag
    on the runtime context takes precedence so data lands on an isolated
    branch first and promotion stays explicit; otherwise the routed target
    branch is returned.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import get_write_branch_from_context

        write_branch = get_write_branch_from_context(
            runtime_context,
            strict_validation=True
        )
        # Returns WAP branch tag value if present, else "main"
        ```

    """
    if strict_validation:
        tags = getattr(context, "tags", {}) or {}
        if isinstance(tags, Mapping):
            wap_branch = tags.get(WAP_TAG_KEY)
            if isinstance(wap_branch, str) and (normalized := wap_branch.strip()):
                return normalized
    return get_branch_from_context(context)


def inject_metadata_columns(
    parquet_path: Path,
    partition_date: str,
    run_id: str,
    context: Any = None,
) -> Path:
    """Append Phlo metadata columns to a staged parquet file.

    Rewrites the file in place with four lineage columns: _phlo_row_id
    (unique ULID per row), _phlo_ingested_at (UTC timestamp),
    _phlo_partition_date, and _phlo_run_id. Returns the same path after
    the columns are written. The optional Dagster context is used for
    structured logging.

    Example:
        ```python
        from pathlib import Path
        from phlo_dlt.dlt_helpers import inject_metadata_columns

        inject_metadata_columns(
            parquet_path=Path("/tmp/data.parquet"),
            partition_date="2024-01-01",
            run_id="run-123",
            context=dagster_context,
        )
        ```

    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrow_table = pq.read_table(str(parquet_path))
    num_rows = len(arrow_table)
    logger.info(
        "dlt_metadata_injection_started",
        parquet_path=str(parquet_path),
        row_count=num_rows,
    )

    if context:
        context.log.info(f"Injecting metadata columns into {num_rows} rows")

    ingested_at = datetime.now(timezone.utc)

    row_ids = [generate_row_id() for _ in range(num_rows)]
    row_id_col = pa.array(row_ids, type=pa.string())

    ingested_at_col = pa.array([ingested_at] * num_rows, type=pa.timestamp("us"))
    partition_date_col = pa.array([partition_date] * num_rows, type=pa.string())
    run_id_col = pa.array([run_id] * num_rows, type=pa.string())

    arrow_table = arrow_table.append_column("_phlo_row_id", row_id_col)
    arrow_table = arrow_table.append_column("_phlo_ingested_at", ingested_at_col)
    arrow_table = arrow_table.append_column("_phlo_partition_date", partition_date_col)
    arrow_table = arrow_table.append_column("_phlo_run_id", run_id_col)

    pq.write_table(arrow_table, str(parquet_path))

    if context:
        context.log.debug(
            "Added _phlo_row_id, _phlo_ingested_at, _phlo_partition_date, _phlo_run_id columns"
        )
    logger.info(
        "dlt_metadata_injection_finished",
        parquet_path=str(parquet_path),
        row_count=num_rows,
    )

    return parquet_path


def validate_with_pandera(
    context,
    data: list[dict[str, Any]],
    schema_class: type[Any],
    column_mapping: dict[str, str] | None = None,
    strict: bool = False,
) -> bool:
    """Validate extracted records against a Pandera DataFrameModel schema.

    Builds a DataFrame from the record dicts, applies the optional
    source-to-schema column rename mapping, coerces datetime columns, and
    validates lazily. Returns True on success; on failure returns False,
    or re-raises pandera.errors.SchemaErrors when strict is True. The
    Dagster context is used for logging validation outcomes.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import validate_with_pandera

        data = [{"user_id": 1, "name": "Alice"}, {"user_id": 2, "name": "Bob"}]
        passed = validate_with_pandera(
            context,
            data,
            schema_class=UserSchema,
            column_mapping={"user_id": "id"},  # Remap user_id -> id
            strict=False,
        )
        ```

    """
    import pandera.errors
    from pandera.engines import pandas_engine

    try:
        logger.info(
            "dlt_pandera_validation_started",
            schema_name=schema_class.__name__,
            record_count=len(data),
            strict=strict,
            column_mapping_provided=column_mapping is not None,
        )
        context.log.info(f"Validating {len(data)} records with {schema_class.__name__}")

        df = pd.DataFrame(data)

        if column_mapping:
            df = df.rename(columns=column_mapping)

        schema = schema_class.to_schema()
        datetime_columns = [
            name
            for name, column in schema.columns.items()
            if isinstance(column.dtype, pandas_engine.DateTime)
        ]

        for col in datetime_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        schema_class.validate(df, lazy=True)
        context.log.info("Pandera validation passed")
        logger.info(
            "dlt_pandera_validation_passed",
            schema_name=schema_class.__name__,
            record_count=len(data),
        )
        return True
    except pandera.errors.SchemaErrors as e:
        logger.warning(
            "dlt_pandera_validation_failed",
            schema_name=schema_class.__name__,
            record_count=len(data),
            strict=strict,
        )
        context.log.warning(f"Pandera validation failed: {e.failure_cases}")
        if strict:
            raise
        return False


def setup_dlt_pipeline(
    pipeline_name: str,
    dataset_name: str,
) -> tuple[Any, Path]:
    """Create a DLT pipeline with a filesystem destination under /tmp/phlo/dlt.

    The pipeline stages extracted data as Parquet files before table store
    loading. Returns the pipeline and its working directory.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import setup_dlt_pipeline

        pipeline, working_dir = setup_dlt_pipeline(
            pipeline_name="users_2024_01_01",
            dataset_name="raw",
        )
        ```

    """
    pipelines_dir = Path("/tmp/phlo/dlt")
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    bucket_url = str((pipelines_dir / "bucket").resolve())
    Path(bucket_url).mkdir(parents=True, exist_ok=True)

    pipeline = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=dlt.destinations.filesystem(bucket_url=bucket_url),
        dataset_name=dataset_name,
        pipelines_dir=str(pipelines_dir),
    )

    return pipeline, pipelines_dir / pipeline_name


def stage_to_parquet(
    context,
    pipeline: Any,
    dlt_source: Any,
    local_staging_root: Path,
) -> tuple[list[Path], float]:
    """Run DLT extraction and return the staged parquet paths and elapsed time.

    Executes the pipeline against the DLT source, failing with RuntimeError
    when DLT returns no load info, reports failed loader jobs, or produces
    no parquet files. Relative output paths are resolved against
    local_staging_root. The Dagster context is used for progress logging.

    Example:
        ```python
        from phlo_dlt.dlt_helpers import setup_dlt_pipeline, stage_to_parquet

        pipeline, working_dir = setup_dlt_pipeline("users", "raw")
        dlt_source = fetch_users_source()
        parquet_paths, elapsed = stage_to_parquet(
            context, pipeline, dlt_source, working_dir
        )
        ```

    """
    start_time = time.time()
    logger.info(
        "dlt_stage_to_parquet_started",
        pipeline_name=getattr(pipeline, "pipeline_name", ""),
    )

    load_info: LoadInfo = pipeline.run(dlt_source, loader_file_format="parquet")
    # Best-effort diagnostic hook: stash the load info on the pipeline for
    # later inspection. Failure is ignored because it must never break staging.
    try:
        setattr(pipeline, "_phlo_last_load_info", load_info)
    except Exception:
        pass
    if load_info is None:
        logger.error(
            "dlt_stage_to_parquet_missing_load_info",
            pipeline_name=getattr(pipeline, "pipeline_name", ""),
        )
        raise RuntimeError("DLT pipeline returned no load info")

    if not load_info.load_packages:
        logger.error(
            "dlt_stage_to_parquet_missing_load_packages",
            pipeline_name=getattr(pipeline, "pipeline_name", ""),
        )
        raise RuntimeError("DLT pipeline completed without load packages")

    parquet_paths: list[Path] = []
    completed_job_count = 0
    for load_package in load_info.load_packages:
        failed_jobs = load_package.jobs.get("failed_jobs", [])
        if failed_jobs:
            logger.error(
                "dlt_stage_to_parquet_failed_jobs",
                pipeline_name=getattr(pipeline, "pipeline_name", ""),
                failed_job_count=len(failed_jobs),
            )
            raise RuntimeError("DLT pipeline reported failed loader jobs")
        completed_jobs = load_package.jobs["completed_jobs"]
        completed_job_count += len(completed_jobs)
        for job in completed_jobs:
            if not job.file_path.endswith(".parquet"):
                continue
            parquet_path = Path(job.file_path)
            if not parquet_path.is_absolute():
                parquet_path = (local_staging_root / parquet_path).resolve()
            parquet_paths.append(parquet_path)
    if not parquet_paths:
        logger.error(
            "dlt_stage_to_parquet_missing_parquet_output",
            pipeline_name=getattr(pipeline, "pipeline_name", ""),
            completed_job_count=completed_job_count,
        )
        raise RuntimeError("DLT pipeline completed without producing parquet files")

    elapsed = time.time() - start_time
    context.log.info(f"DLT staging completed in {elapsed:.2f}s")
    context.log.debug(f"Parquet staged to {len(parquet_paths)} files")
    logger.info(
        "dlt_stage_to_parquet_finished",
        pipeline_name=getattr(pipeline, "pipeline_name", ""),
        parquet_path_count=len(parquet_paths),
        parquet_paths=[str(parquet_path) for parquet_path in parquet_paths],
        elapsed_seconds=round(elapsed, 3),
    )

    return parquet_paths, elapsed


def merge_to_table_store(
    context,
    table_store: TableStore,
    table_config: TableConfig,
    parquet_paths: list[Path],
    branch_name: str,
    merge_strategy: str = "merge",
    merge_config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Write staged parquet data into the table store via append or merge.

    Ensures the destination table exists (deriving its schema from
    table_config when necessary), coerces each parquet file to the table
    schema, then appends or upserts per merge_strategy on branch_name.
    Returns metrics with rows_inserted and rows_deleted. Raises
    PhloConfigError when no schema is available or derivable, and
    ValueError for a merge_strategy other than "append" or "merge".

    Example:
        ```python
        from phlo_dlt.dlt_helpers import merge_to_table_store

        metrics = merge_to_table_store(
            context,
            table_store=iceberg_store,
            table_config=user_table_config,
            parquet_paths=[Path("/tmp/data.parquet")],
            branch_name="main",
            merge_strategy="merge",
        )
        print(f"Inserted {metrics['rows_inserted']} rows")
        ```

    """
    merge_config = merge_config or {}
    table_name = table_config.full_table_name
    logger.info(
        "dlt_merge_to_table_store_started",
        table_name=table_name,
        branch_name=branch_name,
        merge_strategy=merge_strategy,
        parquet_path_count=len(parquet_paths),
        merge_config_keys=sorted(merge_config.keys()),
    )

    context.log.info(f"Ensuring destination table {table_name} exists on branch {branch_name}...")
    table_schema = table_config.table_schema
    if table_schema is None:
        validation_schema = table_config.validation_schema
        if validation_schema is None:
            raise PhloConfigError(
                message="No schema available for table-store write",
                suggestions=[
                    "Set table_schema explicitly in @phlo_ingestion",
                    "Or provide validation_schema with a compatible table_store converter",
                ],
            )
        schema_builder = getattr(table_store, "schema_from_validation_schema", None)
        if not callable(schema_builder):
            raise PhloConfigError(
                message="Active table_store cannot derive schema from validation_schema",
                suggestions=[
                    "Set table_schema explicitly in @phlo_ingestion",
                    "Or implement schema_from_validation_schema on the table_store provider",
                ],
            )
        try:
            table_schema = schema_builder(validation_schema=validation_schema)
        except TypeError:
            table_schema = schema_builder(validation_schema)

    table_store.ensure_table(
        table_name=table_name,
        schema=table_schema,
        partition_spec=table_config.partition_spec,
        override_ref=branch_name,
    )

    def _coerce_parquet_to_table_schema(parquet_file: Path) -> Path:
        """Coerce a Parquet file's columns to match the target table schema.

        Projects the file onto the schema's columns, casting types (with a
        string round-trip fallback) and filling missing columns with nulls.
        Returns the path of the coerced file in a temp directory.

        """
        import tempfile

        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        from pyiceberg.types import (
            BooleanType,
            DateType,
            DoubleType,
            LongType,
            StringType,
            TimestamptzType,
        )

        arrow_table = pq.read_table(str(parquet_file))
        num_rows = len(arrow_table)

        def table_store_type_to_arrow_type(table_type: object) -> pa.DataType:
            """Map a subset of table-store primitive types to Arrow data types."""
            if isinstance(table_type, StringType):
                return pa.string()
            if isinstance(table_type, LongType):
                return pa.int64()
            if isinstance(table_type, DoubleType):
                return pa.float64()
            if isinstance(table_type, BooleanType):
                return pa.bool_()
            if isinstance(table_type, TimestamptzType):
                return pa.timestamp("us", tz="UTC")
            if isinstance(table_type, DateType):
                return pa.date32()
            return pa.string()

        if isinstance(table_schema, pa.Schema):
            desired_fields = list(table_schema)
            desired_names = table_schema.names

            def resolve_target_type(field: Any) -> pa.DataType:
                return field.type
        else:
            desired_fields = list(table_schema.fields)
            desired_names = [f.name for f in desired_fields]

            def resolve_target_type(field: Any) -> pa.DataType:
                return table_store_type_to_arrow_type(field.field_type)

        columns: list[pa.Array] = []
        for field in desired_fields:
            name = field.name
            target_type = resolve_target_type(field)

            if name in arrow_table.column_names:
                col = arrow_table[name]
                try:
                    casted = pc.cast(col, target_type)
                except Exception:
                    # Direct cast failed; route through string, which Arrow
                    # accepts from any column, then re-cast to the target.
                    logger.warning(
                        "dlt_merge_schema_cast_fallback",
                        table_name=table_name,
                        column_name=name,
                        target_type=str(target_type),
                    )
                    casted = pc.cast(pc.cast(col, pa.string()), target_type)
                columns.append(casted)
            else:
                columns.append(pa.nulls(num_rows, type=target_type))

        projected = pa.table(columns, names=desired_names)

        temp_dir = tempfile.mkdtemp()
        coerced_path = Path(temp_dir) / "coerced.parquet"
        pq.write_table(projected, str(coerced_path))
        return coerced_path

    coerced_parquet_paths = [
        _coerce_parquet_to_table_schema(parquet_path) for parquet_path in parquet_paths
    ]
    merge_metrics = {"rows_inserted": 0, "rows_deleted": 0}

    if merge_strategy == "append":
        context.log.info(f"Appending data to destination table on branch {branch_name}...")
        for parquet_path in coerced_parquet_paths:
            file_metrics = table_store.append_parquet(
                table_name=table_name,
                data_path=str(parquet_path),
                override_ref=branch_name,
            )
            merge_metrics["rows_inserted"] += file_metrics.get("rows_inserted", 0)
            merge_metrics["rows_deleted"] += file_metrics.get("rows_deleted", 0)
        context.log.info(f"Appended {merge_metrics['rows_inserted']} rows to {table_name}")
    elif merge_strategy == "merge":
        context.log.info(
            f"Merging data to destination table on branch {branch_name} (idempotent upsert)..."
        )
        for parquet_path in coerced_parquet_paths:
            file_metrics = table_store.merge_parquet(
                table_name=table_name,
                data_path=str(parquet_path),
                unique_key=table_config.unique_key,
                override_ref=branch_name,
            )
            merge_metrics["rows_inserted"] += file_metrics.get("rows_inserted", 0)
            merge_metrics["rows_deleted"] += file_metrics.get("rows_deleted", 0)
        context.log.info(
            f"Merged {merge_metrics['rows_inserted']} rows to {table_name} "
            + f"(deleted {merge_metrics['rows_deleted']} existing duplicates)"
        )
    else:
        logger.error(
            "dlt_merge_to_table_store_unknown_strategy",
            table_name=table_name,
            merge_strategy=merge_strategy,
        )
        raise ValueError(f"Unknown merge strategy: {merge_strategy}")

    logger.info(
        "dlt_merge_to_table_store_finished",
        table_name=table_name,
        merge_strategy=merge_strategy,
        rows_inserted=merge_metrics.get("rows_inserted"),
        rows_deleted=merge_metrics.get("rows_deleted"),
    )
    return merge_metrics
