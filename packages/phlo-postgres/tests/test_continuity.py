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
