"""Exercise merge publication and rollback against a real local Iceberg catalog.

SQLite metadata and filesystem data need no external services. Readers reload
the catalog at commit boundaries rather than inspecting staged table metadata.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.schema import Schema
from pyiceberg.table import Transaction
from pyiceberg.types import LongType, NestedField, StringType

from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.tables import merge_to_table


TABLE = "raw.events"
SCHEMA = Schema(
    NestedField(1, "id", LongType(), required=True),
    NestedField(2, "status", StringType(), required=False),
)


@pytest.fixture
def local_catalog(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = SqlCatalog(
        "atomic", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=warehouse.as_uri()
    )
    catalog.create_namespace("raw")
    table = catalog.create_table(TABLE, SCHEMA)
    table.append(
        pa.Table.from_pylist([{"id": i, "status": "old"} for i in range(1004)]).cast(
            SCHEMA.as_arrow()
        )
    )
    monkeypatch.setattr("phlo_iceberg.tables.get_catalog", lambda ref: catalog)
    monkeypatch.setattr("phlo_iceberg.resource.get_catalog", lambda ref: catalog)
    yield catalog
    catalog.engine.dispose()


@pytest.fixture
def batch(tmp_path):
    path = tmp_path / "updates.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": i, "status": "new"} for i in range(1003)]), path)
    return path


def _rows(catalog):
    return sorted(
        catalog.load_table(TABLE).scan().to_arrow().to_pylist(), key=lambda row: row["id"]
    )


def _snapshot(catalog):
    return catalog.load_table(TABLE).current_snapshot().snapshot_id


def test_multiple_delete_batches_publish_once_and_replay_without_duplicates(
    local_catalog, batch, monkeypatch
):
    before = _rows(local_catalog)
    expected = [{"id": i, "status": "new"} for i in range(1003)] + [{"id": 1003, "status": "old"}]
    commit = local_catalog.commit_table
    publications = []

    def observe_commit(table, requirements, updates):
        assert _rows(local_catalog) == before
        result = commit(table, requirements, updates)
        publications.append(_rows(local_catalog))
        assert publications[-1] == expected
        return result

    monkeypatch.setattr(local_catalog, "commit_table", observe_commit)
    result = merge_to_table(TABLE, batch, "id")
    assert result == {"rows_deleted": 1003, "rows_inserted": 1003}
    assert publications == [expected]

    before = expected
    assert merge_to_table(TABLE, batch, "id") == result
    assert publications == [expected, expected]
    assert _rows(local_catalog) == expected


@pytest.mark.parametrize("failure_stage", ["second_delete", "append", "commit"])
def test_precommit_failure_preserves_rows_and_snapshot(
    local_catalog, batch, monkeypatch, failure_stage
):
    before = _rows(local_catalog)
    snapshot_before = _snapshot(local_catalog)
    commit = MagicMock(wraps=local_catalog.commit_table)
    monkeypatch.setattr(local_catalog, "commit_table", commit)
    if failure_stage == "commit":
        commit.side_effect = RuntimeError("injected precommit failure")
    else:
        method = getattr(Transaction, "delete" if failure_stage == "second_delete" else "append")
        calls = 0

        def fail_after_staging(self, *args, **kwargs):
            nonlocal calls
            method(self, *args, **kwargs)
            calls += 1
            if failure_stage == "append" or calls == 2:
                raise RuntimeError("injected precommit failure")

        monkeypatch.setattr(
            Transaction,
            "delete" if failure_stage == "second_delete" else "append",
            fail_after_staging,
        )

    with pytest.raises(RuntimeError, match="injected precommit failure"):
        merge_to_table(TABLE, batch, "id")
    assert _rows(local_catalog) == before
    assert _snapshot(local_catalog) == snapshot_before
    assert commit.call_count == (1 if failure_stage == "commit" else 0)


@pytest.mark.parametrize(
    "rows, error",
    [
        ([{"id": 1, "status": "a"}, {"id": 1, "status": "b"}], ValueError),
        ([{"id": "not-an-integer", "status": "new"}], pa.ArrowInvalid),
        ([{"status": "new"}], ValueError),
    ],
)
def test_validation_fails_before_deletes(local_catalog, tmp_path, monkeypatch, rows, error):
    before = _rows(local_catalog)
    snapshot_before = _snapshot(local_catalog)
    path = tmp_path / "invalid.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    delete = MagicMock(side_effect=AssertionError("delete started before validation"))
    monkeypatch.setattr(Transaction, "delete", delete)
    with pytest.raises(error):
        merge_to_table(TABLE, path, "id")
    assert delete.call_count == 0
    assert _rows(local_catalog) == before
    assert _snapshot(local_catalog) == snapshot_before


@pytest.mark.parametrize("status", [None, "new"])
def test_alignment_and_key_cast_precede_matching(local_catalog, tmp_path, status):
    path = tmp_path / "aligned.parquet"
    row = {"id": "1", "source_only": "discarded"}
    if status is not None:
        row["status"] = status
    pq.write_table(pa.Table.from_pylist([row]), path)
    assert merge_to_table(TABLE, path, "id") == {"rows_deleted": 1, "rows_inserted": 1}
    expected = [{"id": i, "status": "old"} for i in range(1004)]
    expected[1] = {"id": 1, "status": status}
    assert _rows(local_catalog) == expected


def test_real_commit_conflict_fails_without_partial_merge_and_can_be_retried(
    local_catalog, batch, monkeypatch
):
    before = _rows(local_catalog)
    commit = local_catalog.commit_table
    concurrent_snapshot = None

    def race_commit(table, requirements, updates):
        nonlocal concurrent_snapshot
        monkeypatch.setattr(local_catalog, "commit_table", commit)
        concurrent = local_catalog.load_table(TABLE)
        concurrent.append(
            pa.Table.from_pylist([{"id": 2000, "status": "concurrent"}]).cast(SCHEMA.as_arrow())
        )
        concurrent_snapshot = _snapshot(local_catalog)
        return commit(table, requirements, updates)

    monkeypatch.setattr(local_catalog, "commit_table", race_commit)
    with pytest.raises(CommitFailedException):
        merge_to_table(TABLE, batch, "id")
    assert _rows(local_catalog) == before + [{"id": 2000, "status": "concurrent"}]
    assert _snapshot(local_catalog) == concurrent_snapshot

    assert merge_to_table(TABLE, batch, "id")["rows_inserted"] == 1003
    assert _rows(local_catalog) == [
        *[{"id": i, "status": "new"} for i in range(1003)],
        {"id": 1003, "status": "old"},
        {"id": 2000, "status": "concurrent"},
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_resource_ref_metrics_and_evidence_follow_publication(
    local_catalog, batch, monkeypatch, fail
):
    refs = []

    def catalog_for_ref(ref):
        refs.append(ref)
        return local_catalog

    monkeypatch.setattr("phlo_iceberg.tables.get_catalog", catalog_for_ref)
    monkeypatch.setattr("phlo_iceberg.resource.get_catalog", catalog_for_ref)
    mutation = MagicMock()
    monkeypatch.setattr("phlo_iceberg.resource.emit_mutation", mutation)
    scope = MagicMock()

    @contextmanager
    def observation(**kwargs):
        assert kwargs["branch"] == "dev"
        assert kwargs["snapshot_id_before"] == str(snapshot_before)
        yield scope

    monkeypatch.setattr("phlo_iceberg.tables.phlo_observe.iceberg_commit", observation)
    snapshot_before = _snapshot(local_catalog)
    resource = IcebergResource(ref="unused")
    kwargs = dict(table_name=TABLE, data_path=str(batch), unique_key="id", override_ref="dev")
    if fail:
        monkeypatch.setattr(
            local_catalog, "commit_table", MagicMock(side_effect=RuntimeError("precommit failure"))
        )
        with pytest.raises(RuntimeError, match="precommit failure"):
            resource.merge_parquet(**kwargs)
        assert _snapshot(local_catalog) == snapshot_before
        assert mutation.call_args.kwargs["status"] == "failed"
        assert scope.set_correlation.call_count == 0
    else:
        result = resource.merge_parquet(**kwargs)
        assert result == {"rows_deleted": 1003, "rows_inserted": 1003}
        assert mutation.call_args.kwargs["status"] == "success"
        assert mutation.call_args.kwargs["metrics"] == result
        assert mutation.call_args.kwargs["after"]["snapshot_id"] == str(_snapshot(local_catalog))
        scope.set_correlation.assert_called_once_with(snapshot_id=str(_snapshot(local_catalog)))
    assert mutation.call_args.kwargs["ref"] == "dev"
    assert mutation.call_args.kwargs["before"]["snapshot_id"] == str(snapshot_before)
    assert refs and set(refs) == {"dev"}
