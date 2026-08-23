"""Generate Pandera schemas from dbt model YAML files.

Enables single source of truth: define schema in dbt model YAML,
generate Pandera schema automatically.

This module bridges dbt and Pandera by parsing dbt model YAML files and
generating corresponding Pandera DataFrameModel classes. This enables
data validation using the same schema definitions used for documentation
and testing in dbt.

Example:
    >>> from phlo_dbt.dbt_schema import dbt_model_to_pandera
    >>> Schema = dbt_model_to_pandera(
    ...     "workflows/transforms/dbt/models/silver/fct_orders.yml",
    ...     "fct_orders"
    ... )
    >>>
    >>> # Use for validation
    >>> import pandas as pd
    >>> df = pd.read_csv("orders.csv")
    >>> validated_df = Schema.validate(df)
    >>>
    >>> # Schema can also be used with Phlo's quality checks
    >>> from phlo.quality import phlo_quality
    >>> @phlo_quality(schema=Schema)
    ... def load_orders():
    ...     return df

"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pandera.pandas import DataFrameModel, Field

from phlo.logging import get_logger

try:
    from phlo_pandera.schemas.base import PhloSchema
except Exception:  # noqa: BLE001 - optional dependency

    class PhloSchema(DataFrameModel):  # type: ignore[no-redef]
        """Fallback schema base with PhloSchema defaults when phlo-pandera is unavailable."""

        class Config:
            """Pandera configuration for fallback schema."""

            strict = False
            coerce = True


logger = get_logger(__name__)


def _parse_dbt_tests(data_tests: list[Any]) -> dict[str, Any]:
    """Parse dbt data_tests into Pandera Field kwargs."""
    kwargs: dict[str, Any] = {}
    accepted_values: list[Any] | None = None

    for test in data_tests:
        if isinstance(test, str):
            # Simple test like "not_null" or "unique"
            if test == "not_null":
                kwargs["nullable"] = False
            elif test == "unique":
                kwargs["unique"] = True
        elif isinstance(test, dict):
            # Complex test with config
            for test_name, test_config in test.items():
                if test_name == "accepted_values":
                    accepted_values = test_config.get("values", [])
                elif test_name == "dbt_expectations.expect_column_values_to_be_between":
                    if "min_value" in test_config:
                        kwargs["ge"] = test_config["min_value"]
                    if "max_value" in test_config:
                        kwargs["le"] = test_config["max_value"]
                elif test_name == "dbt_utils.accepted_range":
                    if "min_value" in test_config:
                        kwargs["ge"] = test_config["min_value"]
                    if "max_value" in test_config:
                        kwargs["le"] = test_config["max_value"]

    if accepted_values is not None:
        kwargs["isin"] = accepted_values

    return kwargs


def _infer_type(column_name: str, data_tests: list[Any]) -> type:
    """Infer Python type from column name and tests.

    dbt doesn't have explicit types in model YAML (they're in the SQL),
    so we use heuristics based on column names and test values.
    """
    name_lower = column_name.lower()

    # Timestamp patterns
    if "timestamp" in name_lower or "date" in name_lower or name_lower.endswith("_at"):
        return datetime

    # Check accepted_values for type hints
    for test in data_tests:
        if isinstance(test, dict):
            if "accepted_values" in test:
                values = test["accepted_values"].get("values", [])
                if values and all(isinstance(v, int) for v in values):
                    return int
                if values and all(isinstance(v, str) for v in values):
                    return str
            if "dbt_expectations.expect_column_values_to_be_between" in test:
                config = test["dbt_expectations.expect_column_values_to_be_between"]
                if isinstance(config.get("min_value"), int):
                    return int
                if isinstance(config.get("min_value"), float):
                    return float

    # ID patterns
    if name_lower.endswith("_id") or name_lower == "id":
        return str

    # Numeric patterns
    if any(x in name_lower for x in ["count", "amount", "num", "qty", "pct", "percent"]):
        if "pct" in name_lower or "percent" in name_lower:
            return float
        return int

    # Default to string
    return str


def dbt_model_to_pandera(
    yaml_path: str | Path,
    model_name: str,
    class_name: str | None = None,
) -> type[PhloSchema]:
    """Generate a PhloSchema class from a dbt model YAML file.
    Example:
        Schema = dbt_model_to_pandera(
            "workflows/transforms/dbt/models/silver/fct_orders.yml",
            "fct_orders"
        )
        validated_df = Schema.validate(df)
    """
    yaml_path = Path(yaml_path)
    configured_column_count: int | None = None
    generated_column_count = 0
    logger.info(
        "dbt_schema_generation_started",
        yaml_path=str(yaml_path),
        model_name=model_name,
    )

    try:
        with open(yaml_path) as f:
            dbt_config = yaml.safe_load(f)

        # Find the model in the YAML
        model_config = None
        for model in dbt_config.get("models", []):
            if model.get("name") == model_name:
                model_config = model
                break

        if model_config is None:
            raise ValueError(f"Model '{model_name}' not found in {yaml_path}")

        # Generate class name if not provided
        if class_name is None:
            class_name = "".join(word.capitalize() for word in model_name.split("_"))

        # Build annotations and namespace
        annotations: dict[str, Any] = {}
        namespace: dict[str, Any] = {
            "__annotations__": annotations,
            "__module__": __name__,
        }

        columns = model_config.get("columns", [])
        if not isinstance(columns, list):
            raise ValueError(f"Model '{model_name}' columns are not a list")
        configured_column_count = len(columns)

        for column in columns:
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            if not isinstance(col_name, str):
                continue
            data_tests = column.get("data_tests", column.get("tests", []))

            # Parse tests into Field kwargs
            field_kwargs = _parse_dbt_tests(data_tests)

            # Infer type
            python_type = _infer_type(col_name, data_tests)

            # Handle nullable
            is_nullable = field_kwargs.pop("nullable", True)
            if not is_nullable:
                annotations[col_name] = python_type
            else:
                annotations[col_name] = python_type | None

            # Create Field if there are constraints
            if field_kwargs:
                namespace[col_name] = Field(**field_kwargs)

        generated_column_count = len(annotations)

        # Create the class dynamically
        from typing import cast

        schema_class = cast(type[PhloSchema], type(class_name, (PhloSchema,), namespace))
        schema_class.__doc__ = model_config.get("description", f"Schema for {model_name}")
        logger.info(
            "dbt_schema_generation_finished",
            yaml_path=str(yaml_path),
            model_name=model_name,
            configured_column_count=configured_column_count,
            generated_column_count=generated_column_count,
        )
        return schema_class
    except Exception as exc:
        logger.error(
            "dbt_schema_generation_failed",
            yaml_path=str(yaml_path),
            model_name=model_name,
            configured_column_count=configured_column_count,
            generated_column_count=generated_column_count,
            error=str(exc),
            exc_info=True,
        )
        raise
