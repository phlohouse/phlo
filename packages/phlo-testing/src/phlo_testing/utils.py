"""Utility helpers for phlo-testing.

This module provides data normalization utilities for converting between
different data formats commonly used in Phlo testing scenarios.

Example:
    >>> from phlo_testing.utils import to_dataframe, to_records
    >>> data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    >>> df = to_dataframe(data)
    >>> records = to_records(df)

"""

from __future__ import annotations

from typing import Any

import pandas as pd


def to_dataframe(data: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize data into a pandas DataFrame.

    Example:
        >>> data = [{"id": 1, "value": 100}, {"id": 2, "value": 200}]
        >>> df = to_dataframe(data)
        >>> print(df.columns)
        Index(['id', 'value'], dtype='object')

    """
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


def to_records(data: pd.DataFrame | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize data into a list of records.

    Each dictionary represents a row with column names as keys.

    Example:
        >>> df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
        >>> records = to_records(df)
        >>> print(records)
        [{'id': 1, 'name': 'Alice'}, {'id': 2, 'name': 'Bob'}]

    """
    df = to_dataframe(data)
    return df.to_dict("records")
