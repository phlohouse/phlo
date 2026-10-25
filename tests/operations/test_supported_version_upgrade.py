"""In-process version upgrade with injected provider boundaries (Plan 013 Step 5)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from phlo.capabilities.continuity import RestoreTarget
from phlo.operations.backup import create_backup_set
from phlo.operations.journal import InMemoryOperationJournalStore
from phlo.operations.upgrade import (
    SUPPORTED_FROM_VERSION,
    SUPPORTED_TO_VERSION,
    UPGRADE_PIPELINE,
    plan_upgrade,
    upgrade_apply,
)


def _real_providers() -> dict[str, Any]:
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
        "postgres": PostgresBackupContributor(dump_runner=lambda: "SELECT version();"),
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


def _make_source_set(tmp_path: Path) -> Path:
    """Create representative previous-version blessed state as a verified set."""
    providers = _real_providers()
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
        versions={"phlo": SUPPORTED_FROM_VERSION},
    )
    assert result.accepted
    return Path(result.target) / result.set_id


def test_supported_pair_upgrade_succeeds_and_reconciles(tmp_path) -> None:
    set_dir = _make_source_set(tmp_path)
    providers = _real_providers()
    deploy = RestoreTarget.of(tmp_path / "deployment")
    plan = plan_upgrade(
        from_version=SUPPORTED_FROM_VERSION,
        to_version=SUPPORTED_TO_VERSION,
        backup_set_dir=set_dir,
        target=deploy,
    )
    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )

    assert result.accepted is True
    assert result.state == "succeeded"
    assert result.reconciliation is not None and result.reconciliation["ok"] is True
    # every provider applied and left a candidate version marker
    marker_root = tmp_path / "deployment"
    owners = sorted(path.parent.name for path in marker_root.glob("*/upgraded-to.txt"))
    assert owners == sorted(defn.owner for defn in UPGRADE_PIPELINE)
    for provider_dir in marker_root.iterdir():
        marker = provider_dir / "upgraded-to.txt"
        assert marker.read_text(encoding="utf-8").strip().startswith(SUPPORTED_TO_VERSION)
    # source deployment unchanged
    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_deployment_id"] == "deploy-source"


def test_unsupported_pair_refuses_before_mutation(tmp_path) -> None:
    from phlo.operations.upgrade import UpgradeError

    set_dir = _make_source_set(tmp_path)
    with pytest.raises(UpgradeError, match="unsupported_pair"):
        plan_upgrade(
            from_version="0.13.0",
            to_version=SUPPORTED_TO_VERSION,
            backup_set_dir=set_dir,
            target=RestoreTarget.of(tmp_path / "deploy"),
        )
    assert not (tmp_path / "deploy").exists()


@pytest.mark.parametrize(
    ("blocker_subdir", "expected_rollback", "expects_forward_repair"),
    [("minio", None, True), ("postgres", "restore", False)],
    ids=["post-boundary", "pre-boundary"],
)
def test_failure_outcome_depends_on_rollback_boundary(
    tmp_path, blocker_subdir, expected_rollback, expects_forward_repair
) -> None:
    set_dir = _make_source_set(tmp_path)
    providers = _real_providers()
    deploy = RestoreTarget.of(tmp_path / "deployment")
    plan = plan_upgrade(
        from_version=SUPPORTED_FROM_VERSION,
        to_version=SUPPORTED_TO_VERSION,
        backup_set_dir=set_dir,
        target=deploy,
    )
    # Block a step directory by pre-creating it as a file: "minio" is past the
    # rollback boundary, "postgres" is the rollback-safe step.
    blocker = Path(deploy.location) / blocker_subdir
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("occupies path")

    result = upgrade_apply(
        plan=plan,
        confirmation_token=plan.plan_token,
        contributors=providers,
        journal=InMemoryOperationJournalStore(),
    )

    assert result.accepted is False
    assert result.rollback_action == expected_rollback
    assert (result.forward_repair is not None) is expects_forward_repair
    if expects_forward_repair:
        assert result.forward_repair["must_not_rollback"] is True
