"""Exercise immutable history against a real local Iceberg catalog.

Competing writers and lost commit responses are injected at the catalog boundary;
rows, snapshots, policy properties and operation evidence remain real.
"""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.memory import InMemoryCatalog
from pyiceberg.exceptions import CommitFailedException, CommitStateUnknownException
from pyiceberg.table import DataScan
import pytest

from phlo.capabilities.history import HistoryCommitUnknownError, HistoryConflictError, HistoryPolicy
from phlo_iceberg import history, history_adoption, resource, tables
from phlo_iceberg.history_adoption import adopt_history_table, validate_history_adoption
from phlo_iceberg.resource import IcebergResource

POLICY = HistoryPolicy("entity", "version", ("payload",))
SCHEMA = pa.schema(
    [
        ("entity", pa.string()),
        ("version", pa.string()),
        ("payload", pa.string()),
        ("hash", pa.string()),
        ("_phlo_ingested_at", pa.string()),
    ]
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    store = InMemoryCatalog("test", warehouse=f"file://{tmp_path / 'warehouse'}")
    store.create_namespace("raw")
    store.create_table("raw.versions", schema=SCHEMA)
    for module in (history, history_adoption, tables, resource):
        monkeypatch.setattr(module, "get_catalog", lambda **_kwargs: store)
    return store


def _file(tmp_path: Path, rows, name="input", schema=SCHEMA) -> Path:
    path = tmp_path / f"{name}.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


def _row(version="V1", payload="original", entity="E1", **extra):
    return {
        "entity": entity,
        "version": version,
        "payload": payload,
        "hash": "abc",
        "_phlo_ingested_at": "first",
        **extra,
    }


def _write(paths, policy=POLICY, **kwargs):
    return IcebergResource(ref="dev").history_parquet(
        table_name="raw.versions", data_paths=paths, policy=policy, **kwargs
    )


def _state(catalog):
    table = catalog.load_table("raw.versions")
    return table.metadata.model_dump(), table.scan().to_arrow().to_pylist()


def test_replay_late_versions_and_composite_identity(catalog, tmp_path):
    first = _file(tmp_path, [_row()], "first")
    assert _write([first]) == {
        "rows_inserted": 1,
        "rows_deleted": 0,
        "rows_skipped": 0,
        "rows_conflicting": 0,
    }
    before = _state(catalog)
    replay = _file(tmp_path, [_row(_phlo_ingested_at="later")], "replay")
    assert _write([replay])["rows_skipped"] == 1
    assert _state(catalog) == before
    assert (
        _write(
            [
                _file(
                    tmp_path,
                    [_row("V3", "new"), _row("V0", "older"), _row(entity="E2", payload="other")],
                )
            ]
        )["rows_inserted"]
        == 3
    )
    rows = _state(catalog)[1]
    assert {(row["entity"], row["version"], row["payload"]) for row in rows} == {
        ("E1", "V1", "original"),
        ("E1", "V3", "new"),
        ("E1", "V0", "older"),
        ("E2", "V1", "other"),
    }
    properties = catalog.load_table("raw.versions").properties
    assert (
        properties[history.FINGERPRINT_PROPERTY]
        == hashlib.sha256(properties[history.POLICY_PROPERTY].encode()).hexdigest()
    )
    definition = json.loads(properties[history.POLICY_PROPERTY])
    assert definition["entity_key"] == {"name": "entity", "id": 1, "type": "string"}
    assert definition["payload_columns"] == [{"name": "payload", "id": 3, "type": "string"}]


def test_identical_duplicates_across_files_insert_once(catalog, tmp_path):
    left = _file(tmp_path, [_row(), _row("V2", "second")], "left")
    right = _file(
        tmp_path,
        [_row(_phlo_ingested_at="later"), _row("V2", "second"), _row("V2", "second")],
        "right",
    )
    assert _write([left, right]) == {
        "rows_inserted": 2,
        "rows_deleted": 0,
        "rows_skipped": 3,
        "rows_conflicting": 0,
    }
    assert len(catalog.load_table("raw.versions").snapshots()) == 1
    assert len(_state(catalog)[1]) == 2


@pytest.mark.parametrize("crossed_pairs", [False, True])
def test_lookup_pushes_exact_composite_identities_into_real_scan(
    catalog, tmp_path, monkeypatch, crossed_pairs
):
    stored = [_row(entity=f"E{i}", payload=f"payload-{i}") for i in range(101)]
    stored.extend([_row("V2", "second", entity="E1"), _row("V2", "unrelated", entity="E0")])
    _write([_file(tmp_path, stored, "seed")])
    snapshot = catalog.load_table("raw.versions").current_snapshot().snapshot_id
    incoming = [stored[0], stored[-2]] if crossed_pairs else [stored[0]]
    scans = []
    scan_arrow = DataScan.to_arrow

    def capture(scan):
        result = scan_arrow(scan)
        scans.append((scan.snapshot_id, result.to_pylist(), result.column_names))
        return result

    monkeypatch.setattr(DataScan, "to_arrow", capture)
    assert _write([_file(tmp_path, incoming)])["rows_skipped"] == len(incoming)
    assert len(scans) == 1
    head, rows, columns = scans[0]
    assert head == snapshot
    assert set(columns) == {"entity", "version", "payload"}
    assert {(row["entity"], row["version"]) for row in rows} == (
        {("E0", "V1"), ("E1", "V2")} if crossed_pairs else {("E0", "V1")}
    )
    assert len(rows) == len(incoming)


@pytest.mark.parametrize("committed", [False, True])
def test_payload_conflict_rejects_entire_multifile_batch_without_metadata_mutation(
    catalog, tmp_path, committed
):
    if committed:
        _write([_file(tmp_path, [_row()], "seed")])
    before = _state(catalog)
    left = _file(tmp_path, [_row(), _row("V2", "new")], "left")
    extra_schema = pa.schema([*SCHEMA, pa.field("note", pa.string())])
    right = _file(
        tmp_path, [_row(payload="conflict", note="must not publish")], "right", extra_schema
    )
    with pytest.raises(HistoryConflictError) as failure:
        _write([left, right], schema_policy="additive")
    assert failure.value.metrics == {
        "rows_inserted": 0,
        "rows_deleted": 0,
        "rows_skipped": 0,
        "rows_conflicting": 2,
    }
    assert _state(catalog) == before


def test_supplied_hash_controls_comparison_not_raw_payload(catalog, tmp_path):
    policy = HistoryPolicy("entity", "version", payload_hash_column="hash")
    _write([_file(tmp_path, [_row()])], policy)
    before = _state(catalog)
    assert (
        _write([_file(tmp_path, [_row(payload="different ignored payload")])], policy)[
            "rows_skipped"
        ]
        == 1
    )
    assert _state(catalog) == before
    with pytest.raises(HistoryConflictError) as failure:
        _write([_file(tmp_path, [_row(hash="def"), _row("V2")])], policy)
    assert failure.value.metrics["rows_conflicting"] == 1
    assert _state(catalog) == before


def test_null_payload_replays_and_nonnull_conflicts(catalog, tmp_path):
    _write([_file(tmp_path, [_row(payload=None)])])
    assert _write([_file(tmp_path, [_row(payload=None)])])["rows_skipped"] == 1
    before = _state(catalog)
    with pytest.raises(HistoryConflictError):
        _write([_file(tmp_path, [_row(payload="value")])])
    assert _state(catalog) == before


@pytest.mark.parametrize(
    "column,value",
    [
        ("entity", None),
        ("entity", ""),
        ("version", "  "),
        ("version", None),
        ("hash", None),
        ("hash", ""),
    ],
)
def test_invalid_identity_or_hash_fails_before_mutation(catalog, tmp_path, column, value):
    before = _state(catalog)
    with pytest.raises(ValueError, match="null or empty"):
        _write(
            [_file(tmp_path, [_row(**{column: value})])],
            HistoryPolicy("entity", "version", payload_hash_column="hash"),
        )
    assert _state(catalog) == before


@pytest.mark.parametrize(
    "policy",
    [
        HistoryPolicy("version", "entity", ("payload",)),
        HistoryPolicy("entity", "version", ("hash",)),
        HistoryPolicy("entity", "version", payload_hash_column="hash"),
    ],
)
def test_incompatible_policy_requires_migration(catalog, tmp_path, policy):
    path = _file(tmp_path, [_row()])
    _write([path])
    before = _state(catalog)
    with pytest.raises(ValueError, match="Incompatible history policy"):
        _write([path], policy)
    assert _state(catalog) == before


def test_unmarked_nonempty_table_cannot_be_adopted(catalog, tmp_path):
    path = _file(tmp_path, [_row()])
    IcebergResource(ref="dev").append_parquet("raw.versions", str(path))
    before = _state(catalog)
    with pytest.raises(ValueError, match="unmarked nonempty"):
        _write([path])
    assert _state(catalog) == before


def test_explicit_adoption_preserves_rows_and_enables_native_history(catalog, tmp_path):
    catalog.load_table("raw.versions").append(pa.Table.from_pylist([_row()], schema=SCHEMA))
    before = _state(catalog)
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    assert report["compatible"] is True
    assert report["rows_scanned"] == report["distinct_versions"] == 1
    assert report["duplicate_rows"] == 0
    assert _state(catalog) == before
    receipt = adopt_history_table(
        "raw.versions",
        POLICY,
        ref="dev",
        expected_metadata_location=report["metadata_location"],
        operation_id="migration-1133",
        operator="test",
        reason="replace legacy ingestion",
    )
    adopted = _state(catalog)
    assert adopted[1] == before[1]
    assert receipt["snapshot_before"] == report["snapshot_id"]
    assert receipt["snapshot_after"] != receipt["snapshot_before"]
    assert _write([_file(tmp_path, [_row(_phlo_ingested_at="replay")])])["rows_skipped"] == 1
    assert _state(catalog) == adopted
    with pytest.raises(HistoryConflictError):
        _write([_file(tmp_path, [_row(payload="conflict")])])
    assert _state(catalog) == adopted
    assert _write([_file(tmp_path, [_row("V2", "new")])])["rows_inserted"] == 1


def _adopt(report, policy=POLICY, **kwargs):
    return adopt_history_table(
        "raw.versions",
        policy,
        ref="dev",
        expected_metadata_location=report["metadata_location"],
        operation_id="migration-1133",
        operator="test",
        reason="replace legacy ingestion",
        **kwargs,
    )


@pytest.mark.parametrize("seeded", [False, True])
def test_adoption_is_audited_and_repeatable_without_another_commit(catalog, monkeypatch, seeded):
    if seeded:
        catalog.load_table("raw.versions").append(
            pa.Table.from_pylist([_row(), _row(_phlo_ingested_at="second")], schema=SCHEMA)
        )
    events = []
    monkeypatch.setattr("phlo_iceberg.evidence.emit_observation", lambda **e: events.append(e))
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    before = _state(catalog)
    files = list(catalog.load_table("raw.versions").scan().plan_files())
    receipt = _adopt(report, evidence_context={"project_id": "project", "run_id": "run"})
    table = catalog.load_table("raw.versions")
    assert _state(catalog)[1] == before[1]
    assert table.schema().model_dump(mode="json") == report["schema"]
    assert list(table.scan().plan_files()) == files
    assert receipt["rows_scanned"] == (2 if seeded else 0)
    assert receipt["duplicate_rows"] == (1 if seeded else 0)
    assert json.loads(table.properties[history_adoption.ADOPTION_PROPERTY]) == receipt
    assert table.current_snapshot().summary["phlo.history.adoption.id"] == "migration-1133"
    adopted = _state(catalog)
    assert _adopt(report) == receipt
    assert _state(catalog) == adopted
    assert events[0]["status"] == "success"
    assert events[0]["metrics"] == {"rows_inserted": 0, "rows_deleted": 0}
    assert events[0]["resources"][0]["metadata"]["adoption"] == receipt


def test_adoption_reports_exact_conflicting_and_malformed_rows_without_changes(catalog):
    catalog.load_table("raw.versions").append(
        pa.Table.from_pylist(
            [_row(), _row(payload="other"), _row(entity=None), _row(version=" ")], schema=SCHEMA
        )
    )
    before = _state(catalog)
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    assert report["compatible"] is False
    assert report["rows_scanned"] == 4
    assert report["issues"] == [
        {"row": 2, "first_row": 1, "reason": "conflicting immutable payload"},
        {"row": 3, "reason": "History column 'entity' contains a null or empty identity/hash"},
        {"row": 4, "reason": "History column 'version' contains a null or empty identity/hash"},
    ]
    with pytest.raises(ValueError, match="incompatible"):
        _adopt(report)
    assert _state(catalog) == before


@pytest.mark.parametrize(
    "schema,policy,column,reason",
    [
        (
            pa.schema([("entity", pa.string()), ("version", pa.string())]),
            POLICY,
            "payload",
            "missing top-level history column",
        ),
        (
            pa.schema(
                [
                    ("entity", pa.list_(pa.string())),
                    ("version", pa.string()),
                    ("payload", pa.string()),
                ]
            ),
            POLICY,
            "entity",
            "identity must have a scalar type",
        ),
        (
            pa.schema([("entity", pa.string()), ("version", pa.string()), ("hash", pa.int64())]),
            HistoryPolicy("entity", "version", payload_hash_column="hash"),
            "hash",
            "payload hash must be string or binary",
        ),
    ],
)
def test_adoption_reports_incompatible_schema_even_when_empty(
    catalog, schema, policy, column, reason
):
    catalog.drop_table("raw.versions")
    catalog.create_table("raw.versions", schema=schema)
    before = _state(catalog)
    report = validate_history_adoption("raw.versions", policy, ref="dev")
    assert report["compatible"] is False
    assert report["issues"] == [{"column": column, "reason": reason}]
    with pytest.raises(ValueError):
        _adopt(report, policy)
    assert _state(catalog) == before


@pytest.mark.parametrize("conflict", [False, True])
def test_adoption_streams_all_pages_and_compares_versions_across_files(catalog, conflict):
    table = catalog.load_table("raw.versions")
    table.append(
        pa.Table.from_pylist(
            [_row(entity=f"E{i}", payload=f"p{i}") for i in range(140_003)], schema=SCHEMA
        )
    )
    table.append(
        pa.Table.from_pylist(
            [_row(entity="E0", payload="different" if conflict else "p0")], schema=SCHEMA
        )
    )
    before = table.metadata_location
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    assert report["rows_scanned"] == 140_004
    assert report["distinct_versions"] == 140_003
    assert report["duplicate_rows"] == (0 if conflict else 1)
    assert report["compatible"] is not conflict
    assert len(report["issues"]) == int(conflict)
    assert catalog.load_table("raw.versions").metadata_location == before
    if conflict:
        with pytest.raises(ValueError, match="incompatible"):
            _adopt(report)
    else:
        assert _adopt(report)["rows_scanned"] == 140_004
        assert catalog.load_table("raw.versions").scan().count() == 140_004


def test_adoption_hash_policy_uses_native_opaque_digest_contract(catalog, tmp_path):
    policy = HistoryPolicy("entity", "version", payload_hash_column="hash")
    catalog.load_table("raw.versions").append(
        pa.Table.from_pylist([_row(), _row(payload="ignored by hash policy")], schema=SCHEMA)
    )
    report = validate_history_adoption("raw.versions", policy, ref="dev")
    assert report["compatible"] is True
    assert report["duplicate_rows"] == 1
    _adopt(report, policy)
    assert _write([_file(tmp_path, [_row()])], policy)["rows_skipped"] == 1
    with pytest.raises(HistoryConflictError):
        _write([_file(tmp_path, [_row(hash="different")])], policy)


@pytest.mark.parametrize("change", ["append", "schema", "properties"])
def test_adoption_rejects_stale_reviewed_revision(catalog, change):
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    table = catalog.load_table("raw.versions")
    if change == "append":
        table.append(pa.Table.from_pylist([_row()], schema=SCHEMA))
    elif change == "schema":
        from pyiceberg.types import StringType

        with table.update_schema() as update:
            update.add_column("note", StringType())
    else:
        with table.transaction() as transaction:
            transaction.set_properties({"external": "changed"})
    before = _state(catalog)
    with pytest.raises(ValueError, match="revision changed"):
        _adopt(report)
    assert _state(catalog) == before


@pytest.mark.parametrize("change", ["append", "schema", "adopt"])
def test_adoption_atomic_guards_reject_competing_commit_without_retry(catalog, monkeypatch, change):
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    commit = catalog.commit_table
    competing = None

    def race(table, requirements, updates):
        nonlocal competing
        monkeypatch.setattr(catalog, "commit_table", commit)
        if change == "append":
            catalog.load_table("raw.versions").append(pa.Table.from_pylist([_row()], schema=SCHEMA))
        elif change == "schema":
            from pyiceberg.types import StringType

            with catalog.load_table("raw.versions").update_schema() as update:
                update.add_column("note", StringType())
        else:
            _adopt(report, HistoryPolicy("entity", "version", payload_hash_column="hash"))
        competing = _state(catalog)
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", race)
    with pytest.raises(CommitFailedException):
        _adopt(report)
    assert _state(catalog) == competing


@pytest.mark.parametrize("landed", [False, True])
def test_interrupted_adoption_reports_unknown_and_recovers_explicitly(catalog, monkeypatch, landed):
    catalog.load_table("raw.versions").append(pa.Table.from_pylist([_row()], schema=SCHEMA))
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    before = _state(catalog)
    commit = catalog.commit_table
    attempts = 0

    def unknown(table, requirements, updates):
        nonlocal attempts
        attempts += 1
        if landed:
            commit(table, requirements, updates)
        raise TimeoutError("lost response")

    events = []
    monkeypatch.setattr("phlo_iceberg.evidence.emit_observation", lambda **e: events.append(e))
    monkeypatch.setattr(catalog, "commit_table", unknown)
    with pytest.raises(HistoryCommitUnknownError) as failure:
        _adopt(report, evidence_context={"project_id": "project", "run_id": "run"})
    assert attempts == 1
    assert failure.value.metrics == {}
    assert failure.value.reconciliation["adoption_record_matches"] is landed
    assert failure.value.reconciliation["policy_matches"] is landed
    assert _state(catalog)[1] == before[1]
    assert events[0]["status"] == "failed"
    assert events[0]["resources"][0]["metadata"]["outcome"] == "unknown"
    if not landed:
        assert _state(catalog) == before
    interrupted = _state(catalog)
    monkeypatch.setattr(catalog, "commit_table", commit)
    receipt = _adopt(report)
    assert receipt["operation_id"] == "migration-1133"
    if landed:
        assert _state(catalog) == interrupted
    assert _state(catalog)[1] == before[1]


def test_partial_scan_failure_never_binds_policy(catalog, monkeypatch):
    catalog.load_table("raw.versions").append(pa.Table.from_pylist([_row()], schema=SCHEMA))
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    before = _state(catalog)
    read = DataScan.to_arrow_batch_reader

    def interrupted(scan):
        reader = read(scan)

        def batches():
            with reader:
                yield reader.read_next_batch()
                raise OSError("interrupted after first page")

        return pa.RecordBatchReader.from_batches(reader.schema, batches())

    with monkeypatch.context() as patch:
        patch.setattr(DataScan, "to_arrow_batch_reader", interrupted)
        with pytest.raises(OSError, match="after first page"):
            validate_history_adoption("raw.versions", POLICY, ref="dev")
        with pytest.raises(OSError, match="after first page"):
            _adopt(report)
    assert _state(catalog) == before


def test_adoption_identity_index_does_not_treat_hash_collisions_as_identity(catalog, tmp_path):
    schema = pa.schema([("entity", pa.int64()), ("version", pa.string()), ("payload", pa.string())])
    catalog.drop_table("raw.versions")
    catalog.create_table("raw.versions", schema=schema)
    # Python gives -1 and -2 the same hash. They remain distinct entity IDs.
    rows = [_row(entity=-1, payload="left"), _row(entity=-2, payload="right")]
    catalog.load_table("raw.versions").append(pa.Table.from_pylist(rows, schema=schema))
    report = validate_history_adoption("raw.versions", POLICY, ref="dev")
    assert report["compatible"] is True
    assert report["distinct_versions"] == 2
    assert report["duplicate_rows"] == 0
    _adopt(report)
    assert _write([_file(tmp_path, rows, schema=schema)])["rows_skipped"] == 2


def test_empty_batch_never_binds_policy_or_publishes_additions(catalog, tmp_path):
    before = _state(catalog)
    schema = pa.schema([*SCHEMA, pa.field("note", pa.string())])
    assert (
        _write([_file(tmp_path, [], schema=schema)], schema_policy="additive")["rows_inserted"] == 0
    )
    assert _state(catalog) == before


def test_additive_schema_policy_and_first_policy_binding_publish_together(catalog, tmp_path):
    schema = pa.schema([*SCHEMA, pa.field("note", pa.string())])
    path = _file(tmp_path, [_row(note="retained")], schema=schema)
    before = _state(catalog)
    with pytest.raises(ValueError, match="Unexpected columns"):
        _write([path])
    assert _state(catalog) == before
    assert _write([path], schema_policy="additive")["rows_inserted"] == 1
    table = catalog.load_table("raw.versions")
    assert table.scan().to_arrow()["note"].to_pylist() == ["retained"]
    assert history.POLICY_PROPERTY in table.properties
    assert len(table.snapshots()) == 1


@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_writer_forces_complete_lookup_retry(
    catalog, tmp_path, monkeypatch, seeded, conflicting
):
    if seeded:
        _write([_file(tmp_path, [_row("V0", "seed")], "seed")])
    pending = _file(tmp_path, [_row(), _row("V2", "distinct")], "pending")
    competing = _file(
        tmp_path, [_row(payload="different" if conflicting else "original")], "competing"
    )
    commit = catalog.commit_table
    calls = 0

    def race(table, requirements, updates):
        nonlocal calls
        calls += 1
        if calls == 1:
            _write([competing])
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", race)
    if conflicting:
        with pytest.raises(HistoryConflictError) as failure:
            _write([pending])
        assert failure.value.metrics["rows_conflicting"] == 1
        expected = {("V1", "different")}
        assert calls == 2
    else:
        assert _write([pending]) == {
            "rows_inserted": 1,
            "rows_deleted": 0,
            "rows_skipped": 1,
            "rows_conflicting": 0,
        }
        expected = {("V1", "original"), ("V2", "distinct")}
        assert calls == 3
    if seeded:
        expected.add(("V0", "seed"))
    assert {(r["version"], r["payload"]) for r in _state(catalog)[1]} == expected


@pytest.mark.parametrize("exhausted", [False, True])
def test_definite_retry_bound_and_atomic_policy_binding(catalog, tmp_path, monkeypatch, exhausted):
    path = _file(tmp_path, [_row()])
    before = _state(catalog)
    commit = catalog.commit_table
    calls = 0

    def fail(table, requirements, updates):
        nonlocal calls
        calls += 1
        if exhausted or calls < 3:
            raise CommitFailedException("definite rejection")
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", fail)
    if exhausted:
        with pytest.raises(CommitFailedException):
            _write([path])
        assert _state(catalog) == before
    else:
        assert _write([path])["rows_inserted"] == 1
        assert len(_state(catalog)[1]) == 1
    assert calls == 3


@pytest.mark.parametrize("landed", [False, True])
@pytest.mark.parametrize("error", [CommitStateUnknownException, TimeoutError])
def test_unknown_commit_reconciles_and_fails_without_retry_or_invented_counts(
    catalog, tmp_path, monkeypatch, landed, error
):
    path = _file(tmp_path, [_row(), _row(), _row("V2", "second")], "pending")
    commit = catalog.commit_table
    calls = 0

    def unknown(table, requirements, updates):
        nonlocal calls
        calls += 1
        if landed:
            commit(table, requirements, updates)
        raise error("lost response")

    monkeypatch.setattr(catalog, "commit_table", unknown)
    events = []
    monkeypatch.setattr(
        "phlo_iceberg.evidence.emit_observation", lambda **event: events.append(event)
    )
    with pytest.raises(HistoryCommitUnknownError) as failure:
        _write([path], evidence_context={"project_id": "project", "run_id": "run"})
    assert calls == 1
    assert failure.value.metrics == {}
    assert failure.value.reconciliation["state"] == "observed"
    assert failure.value.reconciliation["versions_present"] == (2 if landed else 0)
    assert failure.value.reconciliation["versions_missing"] == (0 if landed else 2)
    assert failure.value.reconciliation["versions_conflicting"] == 0
    assert "rows_present" not in failure.value.reconciliation
    assert failure.value.reconciliation["policy_bound"] is landed
    assert len(_state(catalog)[1]) == (2 if landed else 0)
    assert events[0]["status"] == "failed"
    assert events[0]["metrics"] == {}
    assert events[0]["resources"][0]["metadata"]["outcome"] == "unknown"
    assert events[0]["resources"][0]["metadata"]["reconciliation"] == failure.value.reconciliation


def test_unknown_reconciliation_partitions_distinct_versions_not_duplicate_rows(
    catalog, tmp_path, monkeypatch
):
    committed = _file(tmp_path, [_row(), _row("V2", "conflicting")], "competing")
    pending = _file(
        tmp_path,
        [_row(), _row(), _row(), _row("V2", "expected"), _row("V3", "missing")],
        "pending",
    )
    commit = catalog.commit_table
    calls = 0

    def unknown(table, requirements, updates):
        nonlocal calls
        calls += 1
        if calls == 1:
            _write([committed])
            raise CommitStateUnknownException("lost response")
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", unknown)
    with pytest.raises(HistoryCommitUnknownError) as failure:
        _write([pending])
    assert calls == 2  # One uncertain attempt and one competing writer, no retry.
    assert failure.value.metrics == {}
    assert failure.value.reconciliation == {
        "state": "conflicting",
        "snapshot_id": str(catalog.load_table("raw.versions").current_snapshot().snapshot_id),
        "versions_present": 1,
        "versions_missing": 1,
        "versions_conflicting": 1,
        "policy_bound": True,
    }
    assert {(row["version"], row["payload"]) for row in _state(catalog)[1]} == {
        ("V1", "original"),
        ("V2", "conflicting"),
    }


def test_conflict_evidence_reports_incoming_counts(catalog, tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(
        "phlo_iceberg.evidence.emit_observation", lambda **event: events.append(event)
    )
    path = _file(tmp_path, [_row(), _row(payload="conflict"), _row("V2")])
    with pytest.raises(HistoryConflictError):
        _write([path], evidence_context={"project_id": "project", "run_id": "run"})
    assert events[0]["metrics"] == {
        "rows_inserted": 0,
        "rows_deleted": 0,
        "rows_skipped": 0,
        "rows_conflicting": 2,
    }
    assert events[0]["resources"][0]["metadata"]["outcome"] == "failed"
    assert _state(catalog)[1] == []


def test_each_file_must_supply_comparison_columns_before_null_backfill(catalog, tmp_path):
    before = _state(catalog)
    complete = _file(tmp_path, [_row()], "complete")
    missing = _file(
        tmp_path,
        [_row("V2")],
        "missing",
        pa.schema([("entity", pa.string()), ("version", pa.string())]),
    )
    with pytest.raises(ValueError, match="History columns missing"):
        _write([complete, missing])
    assert _state(catalog) == before


def test_replay_spanning_multiple_lookup_chunks_preserves_every_version(
    catalog, tmp_path, monkeypatch
):
    path = _file(tmp_path, [_row(entity=f"E{i}", payload=f"payload-{i}") for i in range(1003)])
    assert _write([path])["rows_inserted"] == 1003
    _write([_file(tmp_path, [_row(entity="unrelated")], "unrelated")])
    before = _state(catalog)
    snapshot = catalog.load_table("raw.versions").current_snapshot().snapshot_id
    scans = []
    scan_arrow = DataScan.to_arrow

    def capture(scan):
        result = scan_arrow(scan)
        scans.append((scan.snapshot_id, result.to_pylist()))
        return result

    monkeypatch.setattr(DataScan, "to_arrow", capture)
    assert _write([path])["rows_skipped"] == 1003
    assert len(scans) > 1
    assert sum(len(rows) for _, rows in scans) == 1003
    assert all(head == snapshot for head, _ in scans)
    assert {row["entity"] for _, rows in scans for row in rows} == {f"E{i}" for i in range(1003)}
    assert _state(catalog) == before


def test_comparison_column_order_is_not_a_policy_change(catalog, tmp_path):
    path = _file(tmp_path, [_row()])
    _write([path], HistoryPolicy("entity", "version", ("hash", "payload")))
    before = _state(catalog)
    assert (
        _write([path], HistoryPolicy("entity", "version", ("payload", "hash")))["rows_skipped"] == 1
    )
    assert _state(catalog) == before


def test_nested_payload_nulls_and_nan_are_replay_safe(catalog, tmp_path):
    schema = pa.schema(
        [
            ("entity", pa.string()),
            ("version", pa.string()),
            ("payload", pa.struct([("values", pa.list_(pa.float64())), ("label", pa.string())])),
        ]
    )
    catalog.drop_table("raw.versions")
    catalog.create_table("raw.versions", schema=schema)
    path = _file(
        tmp_path,
        [_row(payload={"values": [1.5, None, float("nan")], "label": None})],
        schema=schema,
    )
    assert _write([path])["rows_inserted"] == 1
    assert _write([path])["rows_skipped"] == 1
    snapshot = catalog.load_table("raw.versions").current_snapshot().snapshot_id
    with pytest.raises(HistoryConflictError):
        _write(
            [
                _file(
                    tmp_path,
                    [_row(payload={"values": [1.5, 2.5, float("nan")], "label": None})],
                    schema=schema,
                )
            ]
        )
    assert catalog.load_table("raw.versions").current_snapshot().snapshot_id == snapshot


@pytest.mark.parametrize("compatible", [False, True])
def test_additive_concurrent_writers_reconcile_complete_history_batch(
    catalog, tmp_path, monkeypatch, compatible
):
    schema = pa.schema([*SCHEMA, pa.field("note", pa.string())])
    path = _file(tmp_path, [_row(note="kept")], schema=schema)
    commit = catalog.commit_table
    calls = 0

    def race(table, requirements, updates):
        nonlocal calls
        calls += 1
        if calls == 1:
            with catalog.load_table("raw.versions").update_schema() as update:
                from pyiceberg.types import LongType, StringType

                update.add_column("note", StringType() if compatible else LongType())
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", race)
    if compatible:
        assert _write([path], schema_policy="additive")["rows_inserted"] == 1
        assert catalog.load_table("raw.versions").scan().to_arrow()["note"].to_pylist() == ["kept"]
        assert calls == 3
    else:
        with pytest.raises(ValueError, match="Conflicting concurrent definition"):
            _write([path], schema_policy="additive")
        assert _state(catalog)[1] == []
        assert history.POLICY_PROPERTY not in catalog.load_table("raw.versions").properties
        assert calls == 2


def test_competing_initial_policies_cannot_both_publish(catalog, tmp_path, monkeypatch):
    pending = _file(tmp_path, [_row()], "pending")
    competing = _file(tmp_path, [_row("V2")], "competing")
    commit = catalog.commit_table
    calls = 0

    def race(table, requirements, updates):
        nonlocal calls
        calls += 1
        if calls == 1:
            _write([competing], HistoryPolicy("entity", "version", payload_hash_column="hash"))
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", race)
    with pytest.raises(ValueError, match="Incompatible history policy"):
        _write([pending])
    assert calls == 2
    assert [row["version"] for row in _state(catalog)[1]] == ["V2"]
