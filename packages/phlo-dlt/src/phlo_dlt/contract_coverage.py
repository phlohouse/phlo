"""Contract-coverage detection for staged ingestion data.

Contracts drive the destination table schema: columns present in staged
source data but absent from the Pandera model are silently dropped on write.
These helpers surface that gap loudly so schema drift is a visible warning,
not silent loss.

Example:
    ```python
    from phlo_dlt.contract_coverage import detect_dropped_source_columns

    dropped = detect_dropped_source_columns(parquet_paths, schema_class=UserSchema)
    ```

"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

INTERNAL_COLUMN_PREFIXES = ("_dlt_", "_phlo_")


def declared_contract_columns(schema_class: type[Any]) -> set[str]:
    """Return column names declared by a Pandera DataFrameModel."""
    return set(schema_class.to_schema().columns.keys())


def staged_parquet_columns(parquet_paths: list[Path]) -> list[str]:
    """Return the union of column names across staged parquet files."""
    columns: set[str] = set()
    for path in parquet_paths:
        columns.update(pq.read_schema(path).names)
    return sorted(columns)


def detect_dropped_source_columns(
    parquet_paths: list[Path],
    schema_class: type[Any],
) -> list[str]:
    """Return staged source columns that the contract would silently drop.

    Internal bookkeeping columns (``_dlt_*`` and ``_phlo_*``) are excluded:
    they are added by staging tooling rather than the source system.
    """
    declared = declared_contract_columns(schema_class)
    dropped = [
        column
        for column in staged_parquet_columns(parquet_paths)
        if not column.startswith(INTERNAL_COLUMN_PREFIXES) and column not in declared
    ]
    return sorted(dropped)


__all__ = [
    "INTERNAL_COLUMN_PREFIXES",
    "declared_contract_columns",
    "detect_dropped_source_columns",
    "staged_parquet_columns",
]
