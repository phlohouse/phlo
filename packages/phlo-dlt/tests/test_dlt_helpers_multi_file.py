"""Tests for multi-file DLT staging and write helpers.

Covers parquet collection across load packages, merges into the table store via
the neutral observer surface only, staged object inventory identity, and
source-identity normalization that redacts query credentials.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType

from phlo_dlt.dlt_helpers import merge_to_table_store, stage_to_parquet
from phlo_dlt.evidence import table_state
from phlo_dlt.evidence import normalize_source_identity, staged_object_inventory
from phlo_dlt.registry import TableConfig


def test_stage_to_parquet_collects_all_files_across_load_packages(tmp_path) -> None:
    context = SimpleNamespace(
        log=SimpleNamespace(
            info=lambda *_args, **_kwargs: None,
            debug=lambda *_args, **_kwargs: None,
        )
    )
    relative_file = tmp_path / "staging" / "part-1.parquet"
    absolute_file = tmp_path / "part-2.parquet"
    relative_file.parent.mkdir(parents=True, exist_ok=True)
    relative_file.write_text("", encoding="utf-8")
    absolute_file.write_text("", encoding="utf-8")
    load_info = SimpleNamespace(
        load_packages=[
            SimpleNamespace(
                jobs={"completed_jobs": [SimpleNamespace(file_path="staging/part-1.parquet")]}
            ),
            SimpleNamespace(
                jobs={"completed_jobs": [SimpleNamespace(file_path=str(absolute_file))]}
            ),
        ]
    )
    pipeline = SimpleNamespace(
        pipeline_name="test_pipeline",
        run=lambda _source, loader_file_format="parquet": load_info,
    )

    parquet_paths, _elapsed = stage_to_parquet(context, pipeline, object(), tmp_path)

    assert parquet_paths == [relative_file.resolve(), absolute_file]


def test_merge_to_table_store_appends_all_files(tmp_path) -> None:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    pd.DataFrame([{"name": "alpha"}]).to_parquet(left_path)
    pd.DataFrame([{"name": "beta"}]).to_parquet(right_path)

    append_calls: list[str] = []

    class TableStoreStub:
        def ensure_table(self, **_kwargs):
            return None

        def append_parquet(
            self, *, table_name: str, data_path: str, override_ref: str | None = None
        ):
            append_calls.append(data_path)
            return {"rows_inserted": 1, "rows_deleted": 0}

    context = SimpleNamespace(log=SimpleNamespace(info=lambda *_args, **_kwargs: None))
    metrics = merge_to_table_store(
        context=context,
        table_store=TableStoreStub(),
        table_config=TableConfig(
            table_name="entries",
            table_schema=Schema(
                NestedField(field_id=1, name="name", field_type=StringType(), required=False)
            ),
            validation_schema=None,
            unique_key="name",
            group_name="raw",
        ),
        parquet_paths=[left_path, right_path],
        branch_name="main",
        merge_strategy="append",
    )

    assert metrics == {"rows_inserted": 2, "rows_deleted": 0}
    assert len(append_calls) == 2


def test_merge_to_table_store_forwards_deduplication_options(tmp_path) -> None:
    parquet_path = tmp_path / "updates.parquet"
    pd.DataFrame([{"event_id": "e1", "status": "sent"}]).to_parquet(parquet_path)

    merge_calls: list[dict] = []

    class TableStoreStub:
        def ensure_table(self, **_kwargs):
            return None

        def merge_parquet(
            self,
            *,
            table_name: str,
            data_path: str,
            unique_key: str,
            override_ref: str | None = None,
            **merge_options,
        ):
            merge_calls.append({"data_path": data_path, **merge_options})
            return {"rows_inserted": 1, "rows_deleted": 1}

    context = SimpleNamespace(log=SimpleNamespace(info=lambda *_args, **_kwargs: None))
    metrics = merge_to_table_store(
        context=context,
        table_store=TableStoreStub(),
        table_config=TableConfig(
            table_name="events",
            table_schema=Schema(
                NestedField(field_id=1, name="event_id", field_type=StringType(), required=False)
            ),
            validation_schema=None,
            unique_key="event_id",
            group_name="raw",
        ),
        parquet_paths=[parquet_path],
        branch_name="main",
        merge_strategy="merge",
        merge_config={
            "deduplication": True,
            "deduplication_method": "last",
            "deduplication_order_by": "updated_at",
        },
    )

    assert metrics == {"rows_inserted": 1, "rows_deleted": 1}
    assert len(merge_calls) == 1
    call = merge_calls[0]
    # Data path points at the schema-coerced staging copy, not the source parquet.
    assert call.pop("data_path").endswith("coerced.parquet")
    assert call == {
        "deduplication_method": "last",
        "deduplication_order_by": "updated_at",
    }


def test_table_state_uses_only_the_neutral_observer_surface() -> None:
    class NonIcebergTableStore:
        def get_catalog(self, **_kwargs):  # pragma: no cover - boundary tripwire
            raise AssertionError("dlt must not load provider catalogs")

    assert table_state(NonIcebergTableStore(), "raw.events", "main")["state"] == "unavailable"

    class NeutralTableStore:
        def observe_table_state(self, *, table_name: str, override_ref: str | None = None):
            assert (table_name, override_ref) == ("raw.events", "main")
            return {
                "state": "present",
                "revision": "snapshot-1",
                "schema_hash": "schema-1",
                "metadata": {"provider": "neutral"},
            }

    observed = table_state(NeutralTableStore(), "raw.events", "main")
    assert observed["snapshot_id"] == "snapshot-1"
    assert observed["schema_hash"] == "schema-1"


def test_merge_to_table_store_supports_pyarrow_table_schema(tmp_path) -> None:
    parquet_path = tmp_path / "pokemon.parquet"
    pd.DataFrame([{"pokemon_id": 1, "name": "bulbasaur"}]).to_parquet(parquet_path)

    append_calls: list[str] = []

    class TableStoreStub:
        def ensure_table(self, **_kwargs):
            return None

        def append_parquet(
            self, *, table_name: str, data_path: str, override_ref: str | None = None
        ):
            append_calls.append(data_path)
            return {"rows_inserted": 1, "rows_deleted": 0}

    context = SimpleNamespace(log=SimpleNamespace(info=lambda *_args, **_kwargs: None))
    metrics = merge_to_table_store(
        context=context,
        table_store=TableStoreStub(),
        table_config=TableConfig(
            table_name="pokemon_species",
            table_schema=pa.schema(
                [
                    pa.field("pokemon_id", pa.int64()),
                    pa.field("name", pa.string()),
                ]
            ),
            validation_schema=None,
            unique_key="pokemon_id",
            group_name="pokemon",
        ),
        parquet_paths=[parquet_path],
        branch_name="main",
        merge_strategy="append",
    )

    assert metrics == {"rows_inserted": 1, "rows_deleted": 0}
    assert len(append_calls) == 1


def test_staged_inventory_tracks_final_file_content_and_collision_safe_identity(tmp_path) -> None:
    path = tmp_path / "part.parquet"
    pd.DataFrame([{"name": "before"}]).to_parquet(path)
    before = staged_object_inventory([path])[0]

    pd.DataFrame([{"name": "after"}, {"name": "after-2"}]).to_parquet(path)
    after = staged_object_inventory([path])[0]

    assert before["identity"].startswith("sha256:")
    assert before["identity"] != after["identity"]
    assert before["checksum"] != after["checksum"]
    assert before["record_count"] == 1
    assert after["record_count"] == 2


def test_source_identity_preserves_port_and_redacts_query_credentials() -> None:
    assert (
        normalize_source_identity(None, "https://example.test:8443/api?client_secret=TOPSECRET")
        == "https://example.test:8443/api"
    )
    assert normalize_source_identity(None, "https://[2001:db8::1]:8443/api") == (
        "https://[2001:db8::1]:8443/api"
    )
