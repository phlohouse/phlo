"""Migration execution engine.

MigrationExecutor.validate()/execute() stream CSV chunks through optional
quality validation and column mapping, writing to the table store in append
or overwrite mode; every run appends a result record to a local JSON history
file that backfills migration list/status reporting.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from phlo.capabilities import configured_capability_name, list_capabilities
from phlo.capabilities.discovery import discover_capabilities
from phlo.capabilities.resolver import resolve_capability
from phlo.hooks import DataMigrationEventContext, DataMigrationEventEmitter, HookCorrelation
from phlo.logging import get_logger
from phlo.migrations.adapters import list_source_adapter_types, resolve_source_adapter
from phlo.migrations.specs import MigrationResult, MigrationSpec

logger = get_logger(__name__)

_HISTORY_PATH = Path(".phlo/migrations/history.jsonl")


class MigrationExecutionError(RuntimeError):
    """Raised when migration execution fails."""


class MigrationExecutor:
    """Execute data migrations from parsed specs."""

    def validate(
        self,
        spec: MigrationSpec,
        *,
        dry_run_override: bool | None = None,
    ) -> list[str]:
        """Validate migration spec and environment without writing."""
        errors: list[str] = []
        dry_run = spec.options.dry_run if dry_run_override is None else dry_run_override
        discover_capabilities()

        adapter = resolve_source_adapter(spec.source.type)
        if adapter is None:
            supported = ", ".join(list_source_adapter_types()) or "none"
            errors.append(
                f"Unsupported source.type '{spec.source.type}'. Supported adapters: {supported}"
            )
            return errors

        errors.extend(adapter.validate_config(spec.source))

        if spec.destination.write_mode == "merge" and not spec.destination.unique_key:
            errors.append("destination.unique_key is required for merge write_mode")

        if not dry_run:
            resolution = resolve_capability("table_store")
            if resolution is None:
                configured_name = configured_capability_name("table_store")
                available = list_capabilities("table_store")
                if configured_name:
                    errors.append(
                        f"Configured table_store '{configured_name}' is not registered. "
                        f"Available providers: {available}"
                    )
                elif available:
                    errors.append(
                        "Multiple table_store providers are registered. "
                        f"Configure a default table_store provider: {available}"
                    )
                else:
                    errors.append(
                        "No table store registered. Install a table-store provider or run with --dry-run"
                    )

        if spec.options.quality_schema:
            try:
                _resolve_quality_schema(spec.options.quality_schema)
            except MigrationExecutionError as exc:
                errors.append(str(exc))

        return errors

    def execute(
        self, spec: MigrationSpec, *, dry_run_override: bool | None = None
    ) -> MigrationResult:
        """Execute a migration specification."""
        dry_run = spec.options.dry_run if dry_run_override is None else dry_run_override

        validation_errors = self.validate(spec, dry_run_override=dry_run_override)
        if validation_errors:
            raise MigrationExecutionError("; ".join(validation_errors))

        adapter = resolve_source_adapter(spec.source.type)
        if adapter is None:
            raise MigrationExecutionError(
                f"No adapter available for source type: {spec.source.type}"
            )

        table_store = None
        if not dry_run:
            resolution = resolve_capability("table_store")
            if resolution is None:
                configured_name = configured_capability_name("table_store")
                available = list_capabilities("table_store")
                if configured_name:
                    raise MigrationExecutionError(
                        f"Configured table_store '{configured_name}' is not registered. "
                        f"Available providers: {available}"
                    )
                if available:
                    raise MigrationExecutionError(
                        "Multiple table_store providers are registered. "
                        f"Configure PHLO_DEFAULT_CAPABILITIES to select one: {available}"
                    )
                raise MigrationExecutionError(
                    "No table store registered. Install a table-store provider or run with --dry-run"
                )
            table_store = resolution.provider

        request_id = uuid4().hex
        emitter = DataMigrationEventEmitter(
            DataMigrationEventContext(
                migration_name=spec.name,
                source_type=spec.source.type,
                destination_table=spec.destination.table,
                correlation=HookCorrelation(
                    request_id=request_id,
                    asset_key=spec.destination.table,
                ),
            )
        )

        rows_read = 0
        rows_written = 0
        rows_rejected = 0
        chunks_processed = 0
        validation_passed: bool | None = None
        started_at = time.perf_counter()

        estimated_rows = adapter.estimate_row_count(spec.source)
        emitter.emit(
            status="started",
            rows_read=0,
            rows_written=0,
            chunk_index=None,
            metrics={"estimated_rows": estimated_rows, "dry_run": dry_run},
        )

        quality_schema = None
        if spec.options.validate and spec.options.quality_schema:
            quality_schema = _resolve_quality_schema(spec.options.quality_schema)

        try:
            # Chunks are validated then written one at a time. There is no
            # transaction spanning chunks: a quality-validation failure aborts
            # the run with every earlier chunk already committed.
            for index, chunk in enumerate(
                adapter.read_chunks(spec.source, chunk_size=spec.options.chunk_size), start=1
            ):
                mapped = _apply_column_mapping(chunk, spec.column_mapping)
                rows_read += len(mapped)

                if quality_schema is not None:
                    _validate_quality_chunk(quality_schema, mapped)

                if not dry_run and table_store is not None:
                    _write_chunk_to_table_store(
                        table_store=table_store,
                        table_name=spec.destination.table,
                        write_mode=spec.destination.write_mode,
                        unique_key=spec.destination.unique_key,
                        chunk=mapped,
                        first_chunk=index == 1,
                    )
                    rows_written += len(mapped)

                chunks_processed += 1
                emitter.emit(
                    status="chunk_complete",
                    rows_read=rows_read,
                    rows_written=rows_written,
                    chunk_index=index,
                    metrics={"chunk_rows": len(mapped)},
                )

            if quality_schema is not None:
                validation_passed = True
                emitter.emit(
                    status="validation",
                    rows_read=rows_read,
                    rows_written=rows_written,
                    chunk_index=None,
                    metrics={"validation_passed": True},
                )

            duration_seconds = time.perf_counter() - started_at
            status = "dry_run" if dry_run else "completed"
            result = MigrationResult(
                name=spec.name,
                status=status,
                rows_read=rows_read,
                rows_written=rows_written,
                rows_rejected=rows_rejected,
                chunks_processed=chunks_processed,
                duration_seconds=duration_seconds,
                validation_passed=validation_passed,
                metadata={
                    "destination_table": spec.destination.table,
                    "source_type": spec.source.type,
                    "write_mode": spec.destination.write_mode,
                    "dry_run": dry_run,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            emitter.emit(
                status="completed",
                rows_read=rows_read,
                rows_written=rows_written,
                chunk_index=None,
                metrics={"duration_seconds": duration_seconds},
            )
            _append_history(result)
            return result
        except Exception as exc:
            duration_seconds = time.perf_counter() - started_at
            emitter.emit(
                status="failed",
                rows_read=rows_read,
                rows_written=rows_written,
                chunk_index=None,
                metrics={"duration_seconds": duration_seconds, "error": str(exc)},
            )
            logger.exception(
                "migration_execution_failed",
                migration_name=spec.name,
                source_type=spec.source.type,
                destination_table=spec.destination.table,
            )
            raise


def read_migration_history(limit: int = 10) -> list[dict[str, Any]]:
    """Read recent migration results from local history, newest first.

    Malformed lines are skipped so a torn or partial append cannot break reads.
    """
    if limit <= 0:
        return []
    if not _HISTORY_PATH.exists():
        return []

    lines = _HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    payloads: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(payloads) >= limit:
            break
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            payloads.append(loaded)
    return payloads


def _append_history(result: MigrationResult) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(asdict(result), sort_keys=False)}\n")


def _apply_column_mapping(
    rows: list[dict[str, Any]],
    column_mapping: dict[str, str],
) -> list[dict[str, Any]]:
    if not column_mapping:
        return rows
    mapped_rows: list[dict[str, Any]] = []
    for row in rows:
        mapped: dict[str, Any] = {}
        for key, value in row.items():
            mapped_key = column_mapping.get(key, key)
            if mapped_key in mapped:
                raise MigrationExecutionError(
                    f"Column mapping collapses multiple source columns into '{mapped_key}'"
                )
            mapped[mapped_key] = value
        mapped_rows.append(mapped)
    return mapped_rows


def _resolve_quality_schema(reference: str) -> Any:
    if "::" not in reference:
        raise MigrationExecutionError(
            "options.quality_schema must use '<path>.py::ClassName' format"
        )

    module_path, class_name = reference.split("::", 1)
    schema_path = Path(module_path)
    if not schema_path.exists():
        raise MigrationExecutionError(f"Quality schema path not found: {schema_path}")

    import importlib.util

    module_name = f"phlo_migration_quality_{abs(hash(schema_path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, schema_path)
    if spec is None or spec.loader is None:
        raise MigrationExecutionError(f"Could not load quality schema module: {schema_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    schema = getattr(module, class_name, None)
    if schema is None:
        raise MigrationExecutionError(
            f"Quality schema class '{class_name}' not found in {schema_path}"
        )
    return schema


def _validate_quality_chunk(schema: Any, rows: list[dict[str, Any]]) -> None:
    if not hasattr(schema, "validate"):
        raise MigrationExecutionError(
            "Configured quality schema does not expose a 'validate' method"
        )

    try:
        import pandas as pd
    except ImportError as exc:
        raise MigrationExecutionError(
            "pandas is required for chunk-level quality validation"
        ) from exc

    frame = pd.DataFrame(rows)
    schema.validate(frame)


def _write_chunk_to_table_store(
    *,
    table_store: Any,
    table_name: str,
    write_mode: str,
    unique_key: str | None,
    chunk: list[dict[str, Any]],
    first_chunk: bool,
) -> None:
    # "overwrite" replaces the table only for the first chunk; later chunks
    # append so a single run produces exactly one full replacement. Chunk
    # order is therefore significant.
    parquet_path = _stage_chunk_parquet(chunk)
    try:
        if write_mode == "append":
            table_store.append_parquet(table_name=table_name, data_path=parquet_path)
            return
        if write_mode == "overwrite":
            if not hasattr(table_store, "overwrite_parquet"):
                raise MigrationExecutionError(
                    "write_mode 'overwrite' requires table store support for overwrite_parquet"
                )
            if first_chunk:
                table_store.overwrite_parquet(table_name=table_name, data_path=parquet_path)
            else:
                table_store.append_parquet(table_name=table_name, data_path=parquet_path)
            return
        if write_mode == "merge":
            if not unique_key:
                raise MigrationExecutionError("unique_key is required for merge mode")
            table_store.merge_parquet(
                table_name=table_name,
                data_path=parquet_path,
                unique_key=unique_key,
            )
            return
        raise MigrationExecutionError(f"Unsupported write_mode: {write_mode}")
    finally:
        parquet_path.unlink(missing_ok=True)


def _stage_chunk_parquet(chunk: list[dict[str, Any]]) -> Path:
    """Stage a chunk as a temporary parquet file for the table store to ingest.

    The file is created with delete=False because the table store reopens it by
    path; ownership passes to the caller, which must unlink it when done.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MigrationExecutionError(
            "pyarrow is required for non-dry-run migration writes"
        ) from exc

    table = pa.Table.from_pylist(chunk)
    with tempfile.NamedTemporaryFile(
        prefix="phlo-migrate-", suffix=".parquet", delete=False
    ) as tmp:
        temp_path = Path(tmp.name)
    try:
        pq.write_table(table, temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path
