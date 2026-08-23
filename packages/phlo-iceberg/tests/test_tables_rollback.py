"""Tests phlo_iceberg.tables rollback: restoring a table to an ancestor
snapshot restores its data as of that snapshot."""

from __future__ import annotations

import pyarrow as pa
import pytest
from pyiceberg.catalog.memory import InMemoryCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField

from phlo_iceberg import tables


def _table_with_two_snapshots(tmp_path, monkeypatch):
    catalog = InMemoryCatalog("test", warehouse=f"file://{tmp_path}")
    catalog.create_namespace("raw")
    table = catalog.create_table(
        "raw.events",
        schema=Schema(NestedField(1, "id", LongType(), required=False)),
    )
    table.append(pa.table({"id": pa.array([1], type=pa.int64())}))
    first_snapshot_id = table.current_snapshot().snapshot_id
    table.append(pa.table({"id": pa.array([2], type=pa.int64())}))
    second_snapshot_id = table.current_snapshot().snapshot_id
    monkeypatch.setattr(tables, "get_catalog", lambda ref: catalog)
    return catalog, first_snapshot_id, second_snapshot_id


def _row_ids(catalog: InMemoryCatalog) -> list[int]:
    return catalog.load_table("raw.events").scan().to_arrow()["id"].to_pylist()


def test_rollback_restores_an_ancestor_snapshot(tmp_path, monkeypatch) -> None:
    catalog, first_snapshot_id, _ = _table_with_two_snapshots(tmp_path, monkeypatch)

    result = tables.rollback_table_to_snapshot("raw.events", first_snapshot_id)

    restored = catalog.load_table("raw.events")
    assert result == {"rolled_back_to": first_snapshot_id}
    assert restored.current_snapshot().snapshot_id == first_snapshot_id
    assert _row_ids(catalog) == [1]


def test_rollback_rejects_an_unknown_snapshot_without_mutation(tmp_path, monkeypatch) -> None:
    catalog, _, second_snapshot_id = _table_with_two_snapshots(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="unknown snapshot"):
        tables.rollback_table_to_snapshot("raw.events", -1)

    assert catalog.load_table("raw.events").current_snapshot().snapshot_id == second_snapshot_id
    assert sorted(_row_ids(catalog)) == [1, 2]


def test_rollback_rejects_a_non_ancestor_without_mutation(tmp_path, monkeypatch) -> None:
    catalog, first_snapshot_id, original_second_id = _table_with_two_snapshots(
        tmp_path, monkeypatch
    )
    # Roll back to the first snapshot, then append again: the original second
    # snapshot now sits on a divergent branch and is no longer an ancestor of
    # the table's current snapshot.
    table = catalog.load_table("raw.events")
    table.manage_snapshots().rollback_to_snapshot(first_snapshot_id).commit()
    table = catalog.load_table("raw.events")
    table.append(pa.table({"id": pa.array([3], type=pa.int64())}))
    divergent_snapshot_id = catalog.load_table("raw.events").current_snapshot().snapshot_id

    with pytest.raises(ValueError, match="not an ancestor"):
        tables.rollback_table_to_snapshot("raw.events", original_second_id)

    assert catalog.load_table("raw.events").current_snapshot().snapshot_id == divergent_snapshot_id
    assert sorted(_row_ids(catalog)) == [1, 3]
