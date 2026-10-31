"""Reviewed CSV artifacts preserve source values and prevent unreviewed execution."""

import hashlib
import json
from datetime import date
from types import SimpleNamespace

import dlt
import pytest

from phlo.exceptions import PhloConfigError
from phlo_dlt.csv_upload import author_csv_upload, reviewed_rows, register_csv_upload
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets


@pytest.fixture
def artifact(tmp_path):
    upload_id = "a" * 32
    raw = b"id,result,date\r\n9007199254740993,ND,24/09/2026\r\n2,<LOQ,2026-09-25\r\n"
    path = tmp_path / ".phlo/uploads" / upload_id / "source.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    columns = [
        {"column": name, "name": name, "type": kind, "nullable": False, "date_format": "day_first"}
        for name, kind in [("id", "string"), ("result", "string"), ("date", "date")]
    ]
    manifest = {
        "upload_id": upload_id,
        "checksum": hashlib.sha256(raw).hexdigest(),
        "row_count": 2,
        "columns": columns,
        "record_key": "upload_record_id",
        "table": "reviewed_upload",
        "candidate_ref": f"sandbox-upload-{upload_id}",
        "actor": "alice",
        "env": "sandbox",
        "review_version": 4,
        "recipe_digest": "b" * 64,
        "parser_version": 1,
    }
    return tmp_path, manifest


def test_shared_review_contract_preserves_flags_large_identifiers_dates_and_row_identity(artifact):
    root, manifest = artifact
    rows = reviewed_rows(root, manifest)
    assert rows[0]["id"] == "9007199254740993" and rows[0]["result"] == "ND"
    assert rows[1]["result"] == "<LOQ"
    assert rows[0]["date"] == date(2026, 9, 24) and rows[1]["date"] == date(2026, 9, 25)
    assert rows[0]["upload_record_id"] != rows[1]["upload_record_id"]
    assert rows == reviewed_rows(root, manifest)
    (root / ".phlo/uploads" / manifest["upload_id"] / "source.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        reviewed_rows(root, manifest)


def test_authoring_is_immutable_and_only_writes_narrow_generated_paths(artifact):
    root, manifest = artifact
    result = author_csv_upload(root, {"manifest": manifest})
    assert author_csv_upload(root, {"manifest": manifest}) == result
    assert result["files"] == [
        f"contracts/uploads/{manifest['upload_id']}.json",
        f"workflows/uploads/{manifest['upload_id']}.py",
    ]
    changed = {**manifest, "review_version": 5}
    with pytest.raises(FileExistsError):
        author_csv_upload(root, {"manifest": changed})
    assert json.loads((root / result["files"][0]).read_text())["review_version"] == 4
    assert not (root / "pyproject.toml").exists()


def test_scoped_upload_registration_preserves_physical_relation_and_legacy_keys(artifact):
    root, manifest = artifact
    clear_ingestion_assets()
    try:
        for upload_id in ("a" * 32, "b" * 32):
            scoped = {
                **manifest,
                "upload_id": upload_id,
                "asset_id": f"dlt_upload_{upload_id}",
                "candidate_ref": f"sandbox-upload-{upload_id}",
            }
            result = author_csv_upload(root, {"manifest": scoped})
            register_csv_upload(root, result["files"][0], result["metadata"]["contract_digest"])
        legacy = {**manifest, "upload_id": "c" * 32}
        result = author_csv_upload(root, {"manifest": legacy})
        register_csv_upload(root, result["files"][0], result["metadata"]["contract_digest"])
        assets = get_ingestion_assets()
        assert {asset.key for asset in assets} == {
            "dlt_upload_" + "a" * 32,
            "dlt_upload_" + "b" * 32,
            "dlt_reviewed_upload",
        }
        assert all(asset.metadata["phlo/relation"] == "bronze.reviewed_upload" for asset in assets)
        assert all(check.asset_key == asset.key for asset in assets for check in asset.checks)
    finally:
        clear_ingestion_assets()


@pytest.mark.parametrize(
    "tags",
    [
        {"phlo/actor": "bob", "phlo/ref": "sandbox-upload-" + "a" * 32},
        {"phlo/actor": "alice", "phlo/ref": "main"},
        {
            "phlo/actor": "alice",
            "phlo/ref": "sandbox-upload-" + "a" * 32,
            "phlo/wap_branch": "wap-any",
        },
    ],
)
def test_candidate_runtime_rejects_wrong_actor_main_and_wap_before_source_read(artifact, tags):
    root, manifest = artifact
    result = author_csv_upload(root, {"manifest": manifest})
    clear_ingestion_assets()
    try:
        register_csv_upload(root, result["files"][0], result["metadata"]["contract_digest"])
        asset = get_ingestion_assets()[0]
        assert asset.run.cron is None and asset.partitions is None
        assert asset.run.isolated_write.ref == manifest["candidate_ref"]
        assert asset.run.isolated_write.actor == manifest["actor"]
        assert asset.metadata["phlo/relation"] == "bronze.reviewed_upload"
        (root / ".phlo/uploads" / manifest["upload_id"] / "source.csv").unlink()
        runtime = SimpleNamespace(tags=tags, partition_key=None, resources={}, run_id="run-1")
        with pytest.raises(PhloConfigError, match="restricted"):
            list(asset.run.fn(runtime))
    finally:
        clear_ingestion_assets()


@pytest.mark.parametrize("split", [False, True])
def test_candidate_staging_supplies_required_provenance_to_the_real_iceberg_schema(
    artifact, monkeypatch, split
):
    """The real DLT filesystem stage must align with Iceberg's derived contract."""
    import pyarrow.parquet as pq
    from phlo.logging import get_logger
    from phlo_iceberg.schema_conversion import pandera_to_iceberg
    from phlo_iceberg.schema_alignment import prepare_arrow_write

    root, manifest = artifact
    manifest["table"] = "reviewed_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    manifest["asset_id"] = f"dlt_upload_{manifest['upload_id']}"
    if split:
        path = root / ".phlo/uploads" / manifest["upload_id"] / "source.csv"
        data = path.read_bytes() + b"3,98.20,24/09/2026\r\n"
        path.write_bytes(data)
        manifest["checksum"] = hashlib.sha256(data).hexdigest()
        manifest["row_count"] = 3
        manifest["columns"][1]["qualifiers"] = {
            "value_name": "result_value",
            "flag_name": "result_flag",
        }
    monkeypatch.chdir(root)
    result = author_csv_upload(root, {"manifest": manifest})

    def setup_pipeline(pipeline_name, dataset_name):
        # The default /tmp/phlo/dlt is shared by every process, and both
        # parametrisations use the same pipeline name, so stage under tmp_path.
        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            destination=dlt.destinations.filesystem(bucket_url=str(root / "bucket")),
            dataset_name=dataset_name,
            pipelines_dir=str(root / "pipelines"),
        )
        return pipeline, root / "pipelines" / pipeline_name

    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", setup_pipeline)

    class Store:
        support = SimpleNamespace(schema_policies=frozenset({"strict"}))
        rows = []
        declaration = None

        def schema_from_validation_schema(self, validation_schema):
            return pandera_to_iceberg(validation_schema)

        def ensure_table(self, **kwargs):
            assert kwargs["override_ref"] == manifest["candidate_ref"]
            self.declaration = kwargs["schema"]

        def merge_parquet(self, **kwargs):
            assert kwargs["override_ref"] == manifest["candidate_ref"]
            source = pq.read_table(kwargs["data_path"])
            table = SimpleNamespace(
                schema=lambda: self.declaration, transaction=lambda: SimpleNamespace()
            )
            _, aligned, _ = prepare_arrow_write(
                table, source, table_name="bronze.reviewed_upload", schema_policy="strict"
            )
            self.rows.extend(aligned.to_pylist())
            return {"rows_inserted": len(aligned), "rows_deleted": 0}

        def observe_table_state(self, **kwargs):
            return {
                "state": "present" if self.rows else "absent",
                "snapshot_id": "123" if self.rows else None,
                "schema_hash": "test-schema",
                "metadata": {"row_count": len(self.rows)},
            }

    store = Store()
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda runtime: (store, "representative-iceberg-contract"),
    )
    clear_ingestion_assets()
    try:
        register_csv_upload(root, result["files"][0], result["metadata"]["contract_digest"])
        runtime = SimpleNamespace(
            tags={
                "phlo/actor": "alice",
                "phlo/ref": manifest["candidate_ref"],
                "phlo/project_id": "reviewed-csv-test",
            },
            partition_key=None,
            resources={},
            run_id="provenance-regression",
            logger=get_logger("csv-provenance-test"),
        )
        list(get_ingestion_assets()[0].run.fn(runtime))
        assert len(store.rows) == manifest["row_count"]
        assert store.rows[0]["result"] == "ND" and store.rows[1]["result"] == "<LOQ"
        assert all(row["_phlo_row_id"] and row["_phlo_ingested_at"] for row in store.rows)
        assert all(row["_phlo_run_id"] == "provenance-regression" for row in store.rows)
        if split:
            assert [row["result_value"] for row in store.rows] == [None, None, 98.2]
            assert [row["result_flag"] for row in store.rows] == ["ND", "<LOQ", None]
            assert store.rows[2]["result"] == "98.20"
    finally:
        clear_ingestion_assets()
