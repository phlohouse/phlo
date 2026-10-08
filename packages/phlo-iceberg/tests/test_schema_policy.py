"""Schema policies publish validated data and nullable additions atomically."""

from decimal import Decimal
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.table import Transaction
from pyiceberg.types import DoubleType, IntegerType, LongType, NestedField, StringType

from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.schema_alignment import _align_arrow_table_to_target_schema, prepare_arrow_write
from phlo_iceberg.tables import append_to_table, ensure_table, merge_to_table, overwrite_table

TABLE = "raw.watering"
SCHEMA = Schema(
    NestedField(1, "event_id", LongType(), required=True),
    NestedField(2, "bed_id", StringType(), required=True),
    NestedField(3, "litres", DoubleType()),
    NestedField(4, "count", IntegerType()),
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = SqlCatalog(
        "schema", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=warehouse.as_uri()
    )
    catalog.create_namespace("raw")
    table = catalog.create_table(TABLE, SCHEMA)
    table.append(
        pa.Table.from_pylist(
            [{"event_id": 1, "bed_id": "old", "litres": 2.5, "count": None}],
            schema=SCHEMA.as_arrow(),
        )
    )
    for module in ("tables", "resource", "catalog"):
        monkeypatch.setattr(f"phlo_iceberg.{module}.get_catalog", lambda ref: catalog)
    yield catalog
    catalog.engine.dispose()


def write(tmp_path, arrow, operation="append", *, resource=False, **kwargs):
    path = tmp_path / "incoming.parquet"
    pq.write_table(arrow, path)
    options = {"unique_key": "event_id"} if operation == "merge" else {}
    if resource:
        return getattr(IcebergResource(), f"{operation}_parquet")(
            table_name=TABLE, data_path=str(path), **options, **kwargs
        )
    writer = {"append": append_to_table, "merge": merge_to_table, "overwrite": overwrite_table}[
        operation
    ]
    return writer(TABLE, path, **options, **kwargs)


def state(catalog):
    table = catalog.load_table(TABLE)
    return table.metadata_location, table.schema(), table.scan().to_arrow().to_pylist()


@pytest.mark.parametrize("operation", ["append", "merge", "overwrite"])
@pytest.mark.parametrize("resource", [False, True])
def test_strict_rejects_and_additive_preserves_garden_field(catalog, tmp_path, operation, resource):
    incoming = pa.table({"watering_method": ["drip"], "bed_id": ["new"], "event_id": [2]})
    before = state(catalog)
    with pytest.raises(ValueError, match="Unexpected columns"):
        write(tmp_path, incoming, operation, resource=resource)
    assert state(catalog) == before
    assert (
        write(tmp_path, incoming, operation, resource=resource, schema_policy="additive")[
            "rows_inserted"
        ]
        == 1
    )
    table = catalog.load_table(TABLE)
    assert [(f.name, f.field_id) for f in table.schema().fields[:4]] == [
        (f.name, f.field_id) for f in SCHEMA.fields
    ]
    assert table.schema().find_field("watering_method").required is False
    rows = sorted(table.scan().to_arrow().to_pylist(), key=lambda row: row["event_id"])
    if operation != "overwrite":
        assert rows[0] == {
            "event_id": 1,
            "bed_id": "old",
            "litres": 2.5,
            "count": None,
            "watering_method": None,
        }
    assert rows[-1] == {
        "event_id": 2,
        "bed_id": "new",
        "litres": None,
        "count": None,
        "watering_method": "drip",
    }


@pytest.mark.parametrize("operation", ["append", "merge", "overwrite"])
def test_drop_extra_is_explicit_and_still_checks_required_fields(catalog, tmp_path, operation):
    incoming = pa.table({"unused": ["discard"], "bed_id": ["new"], "event_id": [1]})
    write(tmp_path, incoming, operation, schema_policy="drop_extra")
    assert "unused" not in catalog.load_table(TABLE).schema().column_names
    rows = catalog.load_table(TABLE).scan().to_arrow().to_pylist()
    assert sorted(row["bed_id"] for row in rows) == (
        ["new", "old"] if operation == "append" else ["new"]
    )
    before = state(catalog)
    with pytest.raises(ValueError, match="Required target column 'bed_id'"):
        write(
            tmp_path,
            pa.table({"event_id": [1], "unused": [7]}),
            operation,
            schema_policy="drop_extra",
        )
    assert state(catalog) == before


@pytest.mark.parametrize("operation", ["append", "merge", "overwrite"])
@pytest.mark.parametrize(
    "bad", ["missing", "null", "narrow", "overflow", "string", "required_addition"]
)
def test_invalid_batches_do_not_publish_schema_or_data(
    catalog, tmp_path, monkeypatch, operation, bad
):
    columns = {
        "event_id": pa.array([1]),
        "bed_id": pa.array(["new"]),
        "new_field": pa.array(["extra"]),
    }
    if bad == "missing":
        del columns["bed_id"]
    elif bad == "null":
        columns["bed_id"] = pa.array([None], type=pa.string())
    elif bad == "narrow":
        columns["count"] = pa.array([7], type=pa.int64())
    elif bad == "overflow":
        columns["litres"] = pa.array([2**53 + 1], type=pa.int64())
    elif bad == "string":
        columns["event_id"] = pa.array(["1"])
    incoming = pa.table(columns)
    if bad == "required_addition":
        incoming = incoming.cast(
            pa.schema(
                [f.with_nullable(False) if f.name == "new_field" else f for f in incoming.schema]
            )
        )
    before = state(catalog)
    commit = MagicMock(wraps=catalog.commit_table)
    monkeypatch.setattr(catalog, "commit_table", commit)
    with pytest.raises(ValueError):
        write(tmp_path, incoming, operation, schema_policy="additive")
    commit.assert_not_called()
    assert state(catalog) == before


@pytest.mark.parametrize("operation", ["append", "merge", "overwrite"])
def test_precommit_failure_rolls_back_additions_with_data(
    catalog, tmp_path, monkeypatch, operation
):
    before = state(catalog)
    monkeypatch.setattr(
        Transaction, "commit_transaction", MagicMock(side_effect=RuntimeError("precommit"))
    )
    with pytest.raises(RuntimeError, match="precommit"):
        write(
            tmp_path,
            pa.table({"event_id": [1], "bed_id": ["new"], "method": ["drip"]}),
            operation,
            schema_policy="additive",
        )
    assert state(catalog) == before


@pytest.mark.parametrize("winner", ["same", "different_name", "conflicting_type", "required"])
@pytest.mark.parametrize("operation", ["append", "merge", "overwrite"])
def test_concurrent_additions_refresh_and_reconcile(
    catalog, tmp_path, monkeypatch, winner, operation
):
    commit = catalog.commit_table
    raced = False

    def race(table, requirements, updates):
        nonlocal raced
        if not raced:
            raced = True
            competing = catalog.load_table(TABLE)
            with competing.update_schema(allow_incompatible_changes=winner == "required") as update:
                update.add_column(
                    "other" if winner == "different_name" else "method",
                    LongType() if winner == "conflicting_type" else StringType(),
                    required=winner == "required",
                )
        return commit(table, requirements, updates)

    monkeypatch.setattr(catalog, "commit_table", race)
    incoming = pa.table({"event_id": [2], "bed_id": ["new"], "method": ["drip"]})
    if winner in ("conflicting_type", "required"):
        with pytest.raises(ValueError, match="Conflicting concurrent definition"):
            write(tmp_path, incoming, operation, schema_policy="additive")
        assert catalog.load_table(TABLE).current_snapshot().summary["total-records"] == "1"
    else:
        assert write(tmp_path, incoming, operation, schema_policy="additive")["rows_inserted"] == 1
        table = catalog.load_table(TABLE)
        assert len([f for f in table.schema().fields if f.name == "method"]) == 1
        rows = sorted(table.scan().to_arrow().to_pylist(), key=lambda row: row["event_id"])
        assert rows[-1]["method"] == "drip"
        if winner == "different_name":
            assert rows[-1]["other"] is None


def test_preparation_is_nonmutating_and_ensure_table_does_not_evolve(catalog, monkeypatch):
    table = catalog.load_table(TABLE)
    before = state(catalog)
    desired = Schema(*SCHEMA.fields, NestedField(19, "method", StringType()))
    with pytest.raises(ValueError, match="Unexpected column"):
        ensure_table(TABLE, desired)
    ensure_table(TABLE, desired, schema_policy="additive")
    commit = MagicMock(wraps=catalog.commit_table)
    monkeypatch.setattr(catalog, "commit_table", commit)
    transaction, aligned, additions = prepare_arrow_write(
        table,
        pa.table({"event_id": [2], "bed_id": ["new"], "method": ["drip"]}),
        table_name=TABLE,
        schema_policy="additive",
    )
    assert additions.names == ["method"]
    assert aligned.column_names == ["event_id", "bed_id", "litres", "count", "method"]
    assert "method" in transaction.table_metadata.schema().column_names
    commit.assert_not_called()
    assert state(catalog) == before


@pytest.mark.parametrize(
    "source,target,values",
    [
        (pa.int64(), pa.int32(), [7]),
        (pa.int64(), pa.int32(), [2**31]),
        (pa.float64(), pa.float32(), [1.234567890123]),
        (pa.string(), pa.int64(), ["7"]),
        (pa.float64(), pa.int64(), [7.0]),
        (pa.timestamp("ns"), pa.timestamp("us"), [1001]),
        (pa.decimal128(8, 3), pa.decimal128(7, 2), [Decimal("1.001")]),
    ],
)
def test_unsafe_casts_are_not_value_dependent(source, target, values):
    with pytest.raises(ValueError, match="Incompatible type|Unsafe cast"):
        _align_arrow_table_to_target_schema(
            pa.table({"value": pa.array(values, type=source)}),
            pa.schema([pa.field("value", target)]),
            table_name=TABLE,
        )


def test_reordering_widening_and_typed_nulls():
    aligned = _align_arrow_table_to_target_schema(
        pa.table({"bed_id": ["A"], "event_id": pa.array([17], type=pa.int32())}),
        SCHEMA.as_arrow(),
        table_name=TABLE,
    )
    assert aligned.schema == SCHEMA.as_arrow()
    assert aligned.to_pylist() == [{"event_id": 17, "bed_id": "A", "litres": None, "count": None}]


def test_required_metadata_fields_are_not_exempt():
    target = pa.schema([pa.field("_phlo_run_id", pa.string(), nullable=False)])
    with pytest.raises(ValueError, match="Required target column"):
        _align_arrow_table_to_target_schema(pa.table({"id": [1]}), target, table_name=TABLE)


def test_invalid_row_cannot_be_hidden_by_deduplication(catalog, tmp_path):
    before = state(catalog)
    incoming = pa.table({"event_id": [1, 1], "bed_id": [None, "valid"], "litres": [1.0, 2.0]})
    with pytest.raises(ValueError, match="Required target column 'bed_id' contains nulls"):
        write(tmp_path, incoming, "merge", deduplication_order_by="litres")
    assert state(catalog) == before


@pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
def test_existing_definition_changes_require_migration(catalog, policy):
    before = state(catalog)
    changed = Schema(NestedField(1, "event_id", IntegerType(), required=True), *SCHEMA.fields[1:])
    with pytest.raises(ValueError, match="requires an explicit migration"):
        ensure_table(TABLE, changed, schema_policy=policy)
    assert state(catalog) == before


def test_native_conversion_for_nested_and_decimal_additions(catalog, tmp_path):
    detail = pa.struct([pa.field("code", pa.string(), nullable=False)])
    incoming = pa.table(
        {
            "event_id": [2],
            "bed_id": ["new"],
            "detail": pa.array([{"code": "D"}], type=detail),
            "amount": pa.array([Decimal("12.34")], type=pa.decimal128(8, 2)),
            "tags": pa.array([["drip", "shade"]], type=pa.list_(pa.string())),
            "optional": pa.array([None], type=pa.string()),
        }
    )
    write(tmp_path, incoming, schema_policy="additive")
    rows = sorted(
        catalog.load_table(TABLE).scan().to_arrow().to_pylist(), key=lambda row: row["event_id"]
    )
    assert rows[0]["detail"] is None and rows[0]["amount"] is None and rows[0]["tags"] is None
    assert rows[1]["detail"] == {"code": "D"}
    assert rows[1]["amount"] == Decimal("12.34")
    assert rows[1]["tags"] == ["drip", "shade"]
    before = state(catalog)
    invalid = incoming.set_column(
        2,
        "detail",
        pa.array([{"code": None}], type=pa.struct([pa.field("code", pa.string(), nullable=True)])),
    )
    with pytest.raises(ValueError, match="Required target column 'detail.code' contains nulls"):
        write(tmp_path, invalid, schema_policy="additive")
    assert state(catalog) == before


@pytest.mark.parametrize(
    "nested",
    [
        pa.list_(pa.field("element", pa.string(), nullable=False)),
        pa.map_(pa.string(), pa.field("value", pa.string(), nullable=False)),
    ],
)
def test_required_nested_values_are_checked(nested):
    values = [[None]] if pa.types.is_list(nested) else [[("key", None)]]
    arrow = pa.table({"nested": pa.array(values, type=nested)})
    with pytest.raises(ValueError, match="Required target column"):
        _align_arrow_table_to_target_schema(arrow, arrow.schema, table_name=TABLE)


@pytest.mark.parametrize(
    "nested",
    [
        pa.struct([pa.field("code", pa.string(), nullable=False)]),
        pa.list_(pa.field("element", pa.string(), nullable=False)),
        pa.map_(pa.string(), pa.field("value", pa.string(), nullable=False)),
    ],
)
def test_null_optional_parent_does_not_require_nested_values(nested):
    target = pa.schema([pa.field("nested", nested)])
    aligned = _align_arrow_table_to_target_schema(
        pa.table({"nested": [None]}), target, table_name=TABLE
    )
    assert aligned.schema == target
    assert aligned.to_pylist() == [{"nested": None}]


@pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
@pytest.mark.parametrize("mode", ["append", "merge", "overwrite"])
def test_migration_executor_policy_reaches_all_chunks(catalog, tmp_path, monkeypatch, policy, mode):
    from types import SimpleNamespace

    from phlo.migrations import executor as module
    from phlo.migrations.specs import MigrationDestination, MigrationSource, MigrationSpec

    chunks = [
        [{"event_id": 2, "bed_id": "new", "method": "drip"}],
        [{"event_id": 3, "bed_id": "next", "method": "hose"}],
    ]
    adapter = SimpleNamespace(
        read_chunks=lambda *args, **kwargs: iter(chunks), estimate_row_count=lambda source: 2
    )
    executor = module.MigrationExecutor()
    monkeypatch.setattr(executor, "validate", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "resolve_source_adapter", lambda name: adapter)
    monkeypatch.setattr(
        module, "resolve_capability", lambda name: SimpleNamespace(provider=IcebergResource())
    )
    monkeypatch.setattr(module, "_HISTORY_PATH", tmp_path / "history.jsonl")
    spec = MigrationSpec(
        "probe",
        "1",
        "policy",
        MigrationSource("fixture"),
        MigrationDestination(TABLE, mode, "event_id", policy),
    )
    before = state(catalog)
    if policy == "strict":
        with pytest.raises(ValueError, match="Unexpected columns"):
            executor.execute(spec)
        assert state(catalog) == before
        return
    assert executor.execute(spec).rows_written == 2
    table = catalog.load_table(TABLE)
    rows = sorted(table.scan().to_arrow().to_pylist(), key=lambda row: row["event_id"])
    assert [row["event_id"] for row in rows] == ([2, 3] if mode == "overwrite" else [1, 2, 3])
    assert [(f.name, f.field_id) for f in table.schema().fields[:4]] == [
        (f.name, f.field_id) for f in SCHEMA.fields
    ]
    if policy == "additive":
        assert [row["method"] for row in rows[-2:]] == ["drip", "hose"]
        if mode != "overwrite":
            assert rows[0]["method"] is None
    else:
        assert "method" not in table.schema().column_names


@pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
def test_kafka_stager_policy_is_effective_and_not_a_type_migration(catalog, policy):
    from phlo_kafka.assets import KafkaConsumerConfig, _make_stager

    config = KafkaConsumerConfig(
        "probe", "raw", "events", TABLE, ["event_id"], schema_policy=policy
    )
    stager = _make_stager(config, None, IcebergResource())
    before = state(catalog)
    rows = [{"event_id": 2, "bed_id": "new", "method": "drip"}]
    if policy == "strict":
        with pytest.raises(ValueError, match="Unexpected columns"):
            stager("checkpoint", rows)
        assert state(catalog) == before
    else:
        assert stager("checkpoint", rows)["rows_merged"] == 1
        result = sorted(
            catalog.load_table(TABLE).scan().to_arrow().to_pylist(), key=lambda row: row["event_id"]
        )
        if policy == "additive":
            assert [row["method"] for row in result] == [None, "drip"]
        else:
            assert "method" not in result[-1]
    before = state(catalog)
    with pytest.raises(ValueError, match="Incompatible type"):
        stager("bad-checkpoint", [{"event_id": 1, "bed_id": "bad", "count": 7}])
    assert state(catalog) == before


def test_iceberg_conforms_to_optional_policy_contract():
    from phlo.capabilities import SchemaPolicyTableStore
    from phlo.capabilities.table_store import schema_policy_kwargs

    resource: SchemaPolicyTableStore = IcebergResource()
    assert isinstance(resource, SchemaPolicyTableStore)
    for policy in ("strict", "additive", "drop_extra"):
        assert schema_policy_kwargs(
            resource,
            policy,
            methods=("ensure_table", "append_parquet", "merge_parquet", "overwrite_parquet"),
        ) == {"schema_policy": policy}
