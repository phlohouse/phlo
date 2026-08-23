"""General ingestion helper utilities.

Source-agnostic building blocks: nested-record flattening, callback-driven
paginated API iteration, CSV batch loading, and stable record fingerprints.
pandas is imported lazily so non-dataframe callers never pay for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PaginationState:
    """State passed to generic pagination callbacks."""

    page: int = 1
    cursor: str | None = None
    next_url: str | None = None


def flatten_json_records(
    record: Mapping[str, Any],
    *,
    separator: str = "_",
    prefix: str = "",
) -> dict[str, Any]:
    """Flatten a nested JSON-like record using joined key names."""
    flattened: dict[str, Any] = {}
    for key, value in record.items():
        full_key = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flattened.update(flatten_json_records(value, separator=separator, prefix=full_key))
        else:
            flattened[full_key] = value
    return flattened


def records_to_dataframe(records: Iterable[Mapping[str, Any]]) -> Any:
    """Convert records to a pandas DataFrame."""
    import pandas as pd

    return pd.DataFrame(list(records))


def api_paginated_source(
    fetch_page: Callable[
        [PaginationState], tuple[Iterable[Mapping[str, Any]], PaginationState | None]
    ],
    *,
    initial_state: PaginationState | None = None,
    max_pages: int | None = None,
) -> Iterator[Mapping[str, Any]]:
    """Yield records from a callback-based paginated API source."""
    state = initial_state or PaginationState()
    pages = 0
    while state is not None:
        records, next_state = fetch_page(state)
        yield from records
        pages += 1
        if max_pages is not None and pages >= max_pages:
            break
        state = next_state


def csv_batch_source(paths: Iterable[str], *, read_csv_kwargs: dict[str, Any] | None = None) -> Any:
    """Read and concatenate a batch of CSV files."""
    import pandas as pd

    frames = [pd.read_csv(path, **(read_csv_kwargs or {})) for path in paths]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def source_record_fingerprint(
    record: Mapping[str, Any], *, keys: Iterable[str] | None = None
) -> str:
    """Return a stable fingerprint for source records."""
    from phlo.helpers.reconciliation import row_checksum

    return row_checksum(record, columns=keys)
