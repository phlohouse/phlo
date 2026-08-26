"""Unit tests for deterministic batch deduplication by unique key."""

import pyarrow as pa
import pytest

from phlo.helpers import deduplicate_arrow_by_unique_key


def _table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows)


def _rows(table: pa.Table) -> list[dict]:
    return table.to_pylist()


def test_no_duplicates_returns_input_unchanged() -> None:
    table = _table(
        [
            {"id": 1, "updated_at": "2024-01-01", "value": "a"},
            {"id": 2, "updated_at": "2024-01-02", "value": "b"},
        ]
    )

    result, removed = deduplicate_arrow_by_unique_key(
        table,
        "id",
        method="last",
        order_by="updated_at",
    )

    assert removed == 0
    assert result is table


def test_last_keeps_row_with_greatest_order_value() -> None:
    # Input deliberately not ordered by updated_at so Parquet row order cannot win.
    table = _table(
        [
            {"id": 1, "updated_at": "2024-01-01", "value": "old"},
            {"id": 1, "updated_at": "2024-03-01", "value": "new"},
            {"id": 1, "updated_at": "2024-02-01", "value": "middle"},
        ]
    )

    result, removed = deduplicate_arrow_by_unique_key(
        table, "id", method="last", order_by="updated_at"
    )

    assert removed == 2
    assert _rows(result) == [{"id": 1, "updated_at": "2024-03-01", "value": "new"}]


def test_first_keeps_first_occurrence_in_input_order() -> None:
    table = _table(
        [
            {"id": 1, "value": "first-seen"},
            {"id": 2, "value": "only"},
            {"id": 1, "value": "later"},
        ]
    )

    result, removed = deduplicate_arrow_by_unique_key(table, "id", method="first")

    assert removed == 1
    assert _rows(result) == [
        {"id": 1, "value": "first-seen"},
        {"id": 2, "value": "only"},
    ]


def test_last_preserves_relative_order_of_survivors() -> None:
    table = _table(
        [
            {"id": 1, "seq": 1, "value": "a1"},
            {"id": 2, "seq": 5, "value": "b5"},
            {"id": 1, "seq": 3, "value": "a3"},
            {"id": 2, "seq": 2, "value": "b2"},
        ]
    )

    result, removed = deduplicate_arrow_by_unique_key(table, "id", method="last", order_by="seq")

    assert removed == 2
    assert [row["id"] for row in _rows(result)] == [2, 1]
    assert {row["value"] for row in _rows(result)} == {"a3", "b5"}


def test_unknown_method_is_rejected() -> None:
    table = _table([{"id": 1, "seq": 1}])

    with pytest.raises(ValueError, match="Unsupported deduplication_method"):
        deduplicate_arrow_by_unique_key(table, "id", method="random")


def test_last_without_order_column_and_duplicates_is_rejected() -> None:
    table = _table([{"id": 1}, {"id": 1}])

    with pytest.raises(ValueError, match="requires an explicit ordering column"):
        deduplicate_arrow_by_unique_key(table, "id", method="last")


def test_last_without_order_column_allows_duplicate_free_batches() -> None:
    table = _table([{"id": 1}, {"id": 2}])

    result, removed = deduplicate_arrow_by_unique_key(table, "id", method="last")

    assert removed == 0
    assert result is table


def test_missing_order_column_is_rejected() -> None:
    table = _table([{"id": 1}, {"id": 1}])

    with pytest.raises(ValueError, match="not found in data"):
        deduplicate_arrow_by_unique_key(table, "id", method="last", order_by="missing")
