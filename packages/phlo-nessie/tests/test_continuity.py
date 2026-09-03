"""Tests for the Nessie catalog backup contribution (Plan 011 Step 2)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from phlo.capabilities.continuity import SET_MANIFEST_NAME, sha256_bytes
from phlo_nessie.continuity import NessieBackupContributor


def _client(branches: list[SimpleNamespace] | None = None):
    return SimpleNamespace(
        list_branches=lambda: (
            branches
            if branches is not None
            else [
                SimpleNamespace(name="main", hash="abc123"),
                SimpleNamespace(name="feature/x", hash="def456"),
            ]
        )
    )


def test_contributor_writes_catalog_export_sorted(tmp_path: Path) -> None:
    contributor = NessieBackupContributor(client=_client())
    destination = tmp_path / "set" / "nessie"
    result = contributor.contribute(destination, operation_id="backup.create:set-1")

    assert result.state.value == "succeeded"
    artifact_path = destination / "catalog.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["operation_id"] == "backup.create:set-1"
    assert [branch["name"] for branch in payload["branches"]] == ["feature/x", "main"]
    artifact = result.artifacts[0]
    assert artifact.relative_path == "nessie/catalog.json"
    assert artifact.sha256 == sha256_bytes(artifact_path.read_bytes())


def test_contributor_failure_is_sanitized(tmp_path: Path) -> None:
    def failing() -> list[SimpleNamespace]:
        raise RuntimeError("nessie unreachable token=supersecret")

    contributor = NessieBackupContributor(client=SimpleNamespace(list_branches=failing))
    result = contributor.contribute(tmp_path / "set" / "nessie", operation_id="op")

    assert result.state.value == "failed"
    assert result.failure is not None
    assert "supersecret" not in result.failure["reason"]


def test_contributor_never_writes_outside_its_prefix_or_finalizes(tmp_path: Path) -> None:
    contributor = NessieBackupContributor(client=_client())
    set_dir = tmp_path / "set"
    contributor.contribute(set_dir / "nessie", operation_id="op")

    assert not (set_dir / SET_MANIFEST_NAME).exists()
    assert {path.name for path in set_dir.iterdir()} == {"nessie"}


# --- restore / reconcile (Plan 012 Step 3) --------------------------------


def test_restore_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import BackupArtifact, RestoreTarget, sha256_file

    payload = json.dumps(
        {
            "schema_version": "1",
            "operation_id": "op",
            "branches": [{"name": "main", "hash": "abc123"}],
        }
    ).encode()
    set_dir = tmp_path / "_set"
    (set_dir / "nessie").mkdir(parents=True)
    artifact_path = set_dir / "nessie" / "catalog.json"
    artifact_path.write_bytes(payload)
    artifact = BackupArtifact(
        provider="nessie",
        name="catalog.json",
        relative_path="nessie/catalog.json",
        size_bytes=len(payload),
        sha256=sha256_file(artifact_path),
        metadata={},
    )
    target = RestoreTarget.of(tmp_path / "target")
    contributor = NessieBackupContributor(client=_client())
    step = contributor.restore(target, [artifact], "tok", str(set_dir))
    assert step.state.value == "succeeded"
    reconciliation = contributor.reconcile(target, [artifact], "tok", str(set_dir))
    assert reconciliation["ok"] is True


def test_restore_missing_catalog_fails_in_preflight(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget

    contributor = NessieBackupContributor(client=_client())
    step = contributor.restore(RestoreTarget.of(tmp_path / "target"), [], "tok", str(tmp_path))
    assert step.state.value == "failed"
    assert step.phase.value == "preflight"
    assert step.retry_safe is False


# --- upgrade (Plan 013 Step 3) ---------------------------------------------


def test_upgrade_step_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import SUPPORTED_TO_VERSION, UpgradeStepDef

    defn = UpgradeStepDef("nessie", "nessie.catalog", "migration", False, False)
    target = RestoreTarget.of(tmp_path / "deploy")
    contributor = NessieBackupContributor(client=_client())
    step = contributor.upgrade_step(defn, target, "0.14.0", SUPPORTED_TO_VERSION, "tok")
    assert step.state.value == "succeeded"
    assert contributor.upgrade_reconcile(target, SUPPORTED_TO_VERSION, "tok")["ok"] is True


def test_upgrade_step_submission_failure_phase(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import UpgradeStepDef

    defn = UpgradeStepDef("nessie", "nessie.catalog", "migration", False, False)
    blocker = tmp_path / "is-a-file"
    blocker.write_text("x")
    contributor = NessieBackupContributor(client=_client())
    step = contributor.upgrade_step(defn, RestoreTarget.of(blocker), "0.14.0", "0.15.0", "t")
    assert step.state.value == "failed"
    assert step.phase.value == "submission"
