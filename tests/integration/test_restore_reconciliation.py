"""Integration: restore reconciliation across every authority (Plan 012 Step 4)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from phlo.capabilities.continuity import RestoreTarget
from phlo.operations import restore as restore_module
from phlo.operations.backup import create_backup_set
from phlo.operations.journal import InMemoryOperationJournalStore
from phlo.operations.restore import plan_restore, restore_apply


def _providers() -> dict[str, Any]:
    from phlo_iceberg.continuity import IcebergBackupContributor
    from phlo_minio.continuity import MinioBackupContributor
    from phlo_nessie.continuity import NessieBackupContributor
    from phlo_postgres.continuity import PostgresBackupContributor

    objects = {("lake", "warehouse/db/t.parquet"): b"parquet-bytes"}
    buckets = ["lake"]

    def mc(args: list[str]) -> str:
        if args[:2] == ["ls", "--json"]:
            return "\n".join(json.dumps({"key": f"{bucket}/"}) for bucket in buckets)
        if args[:1] == ["cat"]:
            target = args[1].removeprefix("local/")
            bucket, key = target.split("/", 1)
            return objects[(bucket, key)].decode("utf-8")
        bucket = args[3].removeprefix("local/")
        return "\n".join(
            json.dumps({"key": key})
            for (obj_bucket, key) in sorted(objects)
            if obj_bucket == bucket
        )

    return {
        "postgres": PostgresBackupContributor(dump_runner=lambda: "SELECT 1;"),
        "nessie": NessieBackupContributor(
            client=SimpleNamespace(
                list_branches=lambda: [SimpleNamespace(name="main", hash="abc123")]
            )
        ),
        "minio": MinioBackupContributor(mc_runner=mc),
        "iceberg": IcebergBackupContributor(
            inventory_fn=lambda: [{"table_name": "lake.t", "snapshot_id": 1}]
        ),
    }


def _make_set(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    providers = _providers()
    result = create_backup_set(
        target=tmp_path / "backup",
        contributors=[
            ("postgres", providers["postgres"]),
            ("nessie", providers["nessie"]),
            ("minio", providers["minio"]),
            ("iceberg", providers["iceberg"]),
        ],
        journal=InMemoryOperationJournalStore(),
        deployment_id="deploy-source",
        versions={"phlo": "0.1"},
    )
    assert result.accepted
    set_dir = Path(result.target) / result.set_id
    return set_dir, providers


def _restore(tmp_path: Path, set_dir: Path, providers: dict[str, Any]) -> tuple[Any, Any, Path]:
    target = RestoreTarget.of(tmp_path / "restore-target")
    plan = plan_restore(backup_set_dir=set_dir, target=target)
    result = restore_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )
    return result, plan, Path(plan.target.location)


def test_positive_restore_reconciles_all_authorities(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    result, _plan, target_root = _restore(tmp_path, set_dir, providers)

    assert result.accepted is True
    assert result.reconciliation is not None
    assert result.reconciliation.ok is True
    assert set(result.reconciliation.checks.keys()) == {"postgres", "nessie", "minio", "iceberg"}
    # source state is unchanged
    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_deployment_id"] == "deploy-source"
    # every restored authority present
    assert (target_root / "postgres" / "restored.sql").is_file()
    assert (target_root / "nessie" / "catalog.json").is_file()
    assert (target_root / "minio" / "lake" / "warehouse" / "db" / "t.parquet").is_file()
    assert (target_root / "iceberg" / "inventory.json").is_file()


def _reconcile(set_dir: Path, providers: dict[str, Any], plan):
    manifest = restore_module._manifest_from_dir(set_dir)
    return restore_module._reconcile(providers, plan, manifest)


def test_corrupted_object_fails_reconciliation(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    result, plan, target_root = _restore(tmp_path, set_dir, providers)
    assert result.accepted is True

    (target_root / "minio" / "lake" / "warehouse" / "db" / "t.parquet").write_bytes(b"evil")
    reconciliation = _reconcile(set_dir, providers, plan)
    assert reconciliation.ok is False
    assert any(
        "minio" in reason and "digest_mismatch" in reason for reason in reconciliation.reasons
    )


def test_corrupted_catalog_fails_reconciliation(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    result, plan, target_root = _restore(tmp_path, set_dir, providers)
    assert result.accepted is True

    tampered = {
        "schema_version": "1",
        "operation_id": "x",
        "branches": [{"name": "evil", "hash": "deadbeef"}],
    }
    (target_root / "nessie" / "catalog.json").write_text(json.dumps(tampered), encoding="utf-8")
    reconciliation = _reconcile(set_dir, providers, plan)
    assert reconciliation.ok is False


def test_missing_restored_dump_fails_reconciliation(tmp_path) -> None:
    set_dir, providers = _make_set(tmp_path)
    result, plan, target_root = _restore(tmp_path, set_dir, providers)
    assert result.accepted is True
    (target_root / "postgres" / "restored.sql").unlink()
    reconciliation = _reconcile(set_dir, providers, plan)
    assert reconciliation.ok is False
    assert any("postgres" in reason for reason in reconciliation.reasons)
