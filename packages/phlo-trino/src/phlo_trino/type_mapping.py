"""Schema-aware type mapping utilities for Trino to Pandas conversion.

Provides centralized Trino-to-Pandas type mappings and schema-aware data
loading: TRINO_TO_PANDAS_TYPES holds the dtype table, trino_type_to_pandas
resolves a Trino type to its Pandas dtype, and apply_schema_types coerces a
DataFrame using a Pandera schema's type hints, eliminating manual conversion.

Example:
    >>> from phlo_trino.type_mapping import trino_type_to_pandas
    >>> trino_type_to_pandas("bigint")
    'int64'
    >>> trino_type_to_pandas("varchar(255)")
    'string'

"""

from __future__ import annotations

from typing import Any

import pandas as pd
from phlo.logging import get_logger

logger = get_logger(__name__)

# Standard Trino to Pandas type mappings
TRINO_TO_PANDAS_TYPES: dict[str, str] = {
    # Integers
    "tinyint": "int8",
    "smallint": "int16",
    "integer": "int32",
    "bigint": "int64",
    "int": "int64",
    # Floats
    "real": "float32",
    "double": "float64",
    "float": "float64",
    "decimal": "float64",
    # Strings
    "varchar": "string",
    "char": "string",
    "string": "string",
    "json": "string",
    # Booleans
    "boolean": "bool",
    # Timestamps
    "timestamp": "datetime64[ns]",
    "timestamp with time zone": "datetime64[ns, UTC]",
    "date": "datetime64[ns]",
    "time": "string",  # Time without date stored as string
    # Binary
    "varbinary": "bytes",
    # UUID
    "uuid": "string",
}


def trino_type_to_pandas(trino_type: str) -> str:
    """Convert a Trino data type to the corresponding Pandas dtype.

    Examples:
        >>> trino_type_to_pandas("bigint")
        'int64'
        >>> trino_type_to_pandas("varchar(255)")
        'string'
        >>> trino_type_to_pandas("timestamp")
        'datetime64[ns]'
    """
    # Normalize: lowercase and strip parameters like varchar(255)
    normalized = trino_type.lower().strip()
    if "(" in normalized:
        normalized = normalized.split("(")[0]

    return TRINO_TO_PANDAS_TYPES.get(normalized, "object")


def apply_schema_types(
    df: pd.DataFrame,
    schema_class: type[Any],
) -> pd.DataFrame:
    """Apply types from a Pandera schema to a DataFrame.

    This eliminates manual type conversion code in quality checks.
    Uses the schema's type hints to coerce DataFrame columns.

    Example:
        from phlo_trino.type_mapping import apply_schema_types
        from workflows.schemas.orders import FactOrders

        df = trino.query("SELECT * FROM gold.fct_orders")
        df = apply_schema_types(df, FactOrders)
        # Types are now correct for validation
    """
    import types
    from typing import get_args, get_origin, get_type_hints

    hints = get_type_hints(schema_class)

    for col_name, type_hint in hints.items():
        if col_name not in df.columns:
            continue

        # Handle Optional types (str | None -> str)
        origin = get_origin(type_hint)
        if origin is types.UnionType:
            args = [a for a in get_args(type_hint) if a is not type(None)]
            if args:
                type_hint = args[0]

        # Apply type conversion based on Python type hint
        try:
            if type_hint is int:
                df[col_name] = pd.to_numeric(df[col_name], errors="coerce").astype("Int64")
            elif type_hint is float:
                df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
            elif type_hint is str:
                df[col_name] = df[col_name].astype("string")
            elif type_hint is bool:
                df[col_name] = df[col_name].astype("boolean")
            # datetime types are usually handled by Pandera coerce=True
        # Coercion is best-effort: a failed conversion leaves the column
        # unchanged instead of failing the load.
        except Exception:
            logger.debug("type_coercion_skipped", column=col_name, target_type=type_hint.__name__)

    return df
