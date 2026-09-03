"""Tests for the PostgreSQL backup contribution (Plan 011 Step 2)."""

from __future__ import annotations

import gzip
from pathlib import Path

from phlo.capabilities.continuity import SET_MANIFEST_NAME, sha256_bytes
from phlo_postgres.continuity import PostgresBackupContributor


def _dump_runner(payload: str = "CREATE TABLE t (id INT);"):
    return lambda: payload


def test_contributor_creates_dump_artifact_in_isolated_target(tmp_path: Path) -> None:
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    destination = tmp_path / "set" / "postgres"
    result = contributor.contribute(destination, operation_id="backup.create:set-1")

    assert result.state.value == "succeeded"
    assert result.provider == "postgres"
    artifact_path = destination / "phlo.sql.gz"
    assert artifact_path.is_file()
    assert gzip.decompress(artifact_path.read_bytes()).decode() == "CREATE TABLE t (id INT);"
    artifact = result.artifacts[0]
    assert artifact.relative_path == "postgres/phlo.sql.gz"
    assert artifact.sha256 == sha256_bytes(artifact_path.read_bytes())
    assert artifact.size_bytes == artifact_path.stat().st_size


def test_contributor_failure_is_sanitized(tmp_path: Path) -> None:
    def failing_runner() -> str:
        raise RuntimeError("pg_dump failed with password=secret123")

    contributor = PostgresBackupContributor(dump_runner=failing_runner)
    result = contributor.contribute(tmp_path / "set" / "postgres", operation_id="op")

    assert result.state.value == "failed"
    assert result.failure is not None
    assert "secret123" not in result.failure["reason"]
    assert list((tmp_path / "set" / "postgres").glob("*")) == []


def test_contributor_never_writes_outside_its_prefix_or_finalizes(tmp_path: Path) -> None:
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    set_dir = tmp_path / "set"
    contributor.contribute(set_dir / "postgres", operation_id="op")

    assert not (set_dir / SET_MANIFEST_NAME).exists()
    top_level = {path.name for path in set_dir.iterdir()}
    assert top_level == {"postgres"}


def test_contributor_writes_only_beneath_destination(tmp_path: Path) -> None:
    outside = tmp_path / "other-provider"
    outside.mkdir()
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    contributor.contribute(tmp_path / "set" / "postgres", operation_id="op")
    assert list(outside.iterdir()) == []


# --- restore / reconcile (Plan 012 Step 3) --------------------------------


def _set_artifact(tmp_path: Path):
    from phlo.capabilities.continuity import BackupArtifact, sha256_bytes

    set_dir = tmp_path / "_set"
    (set_dir / "postgres").mkdir(parents=True)
    dump = b"CREATE TABLE t (id int);"
    gz = gzip.compress(dump)
    artifact_path = set_dir / "postgres" / "phlo.sql.gz"
    artifact_path.write_bytes(gz)
    artifact = BackupArtifact(
        provider="postgres",
        name="phlo.sql.gz",
        relative_path="postgres/phlo.sql.gz",
        size_bytes=len(gz),
        sha256=sha256_bytes(gz),
        metadata={},
    )
    return set_dir, artifact, dump


def test_restore_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget, sha256_bytes

    set_dir, artifact, dump = _set_artifact(tmp_path)
    target = RestoreTarget.of(tmp_path / "target")
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())

    step = contributor.restore(target, [artifact], "plan-tok", str(set_dir))
    assert step.state.value == "succeeded"
    assert step.evidence["restored_sha256"] == sha256_bytes(dump)

    reconciliation = contributor.reconcile(target, [artifact], "plan-tok", str(set_dir))
    assert reconciliation["ok"] is True


def test_restore_missing_dump_fails_in_preflight(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget

    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    step = contributor.restore(RestoreTarget.of(tmp_path / "target"), [], "tok", str(tmp_path))
    assert step.state.value == "failed"
    assert step.phase.value == "preflight"
    assert step.retry_safe is False


# --- upgrade (Plan 013 Step 3) ---------------------------------------------


def test_upgrade_step_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import SUPPORTED_TO_VERSION, UpgradeStepDef

    defn = UpgradeStepDef("postgres", "postgres.schema", "migration", True, True)
    target = RestoreTarget.of(tmp_path / "deploy")
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    step = contributor.upgrade_step(defn, target, "0.14.0", SUPPORTED_TO_VERSION, "plan-tok")
    assert step.state.value == "succeeded"
    assert step.after["version"] == SUPPORTED_TO_VERSION
    assert contributor.upgrade_reconcile(target, SUPPORTED_TO_VERSION, "plan-tok")["ok"] is True


def test_upgrade_step_submission_failure_phase(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import UpgradeStepDef

    defn = UpgradeStepDef("postgres", "postgres.schema", "migration", True, True)
    blocker = tmp_path / "is-a-file"
    blocker.write_text("x")
    contributor = PostgresBackupContributor(dump_runner=_dump_runner())
    step = contributor.upgrade_step(defn, RestoreTarget.of(blocker), "0.14.0", "0.15.0", "t")
    assert step.state.value == "failed"
    assert step.phase.value == "submission"
