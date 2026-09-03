"""Tests for backup create/verify coordination (Plan 011 Steps 3-4)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    BACKUP_SET_SCHEMA_VERSION,
    SET_MANIFEST_NAME,
    SET_MANIFEST_STAGING_NAME,
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    BackupSetError,
    BackupVerificationReason,
    sha256_bytes,
    sha256_file,
)
from phlo.operations.backup import create_backup_set, verify_backup_set
from phlo.operations.journal import (
    InMemoryOperationJournalStore,
    OperationJournalError,
    OperationJournalState,
    claim_operation,
)


class FakeContributor:
    """Provider-owned contributor fake writing real files under its prefix."""

    def __init__(
        self,
        provider: str,
        files: dict[str, bytes] | None = None,
        error: Exception | None = None,
        bad_relative_path: str | None = None,
        declared_digest_override: str | None = None,
    ) -> None:
        self.provider = provider
        self.files = (
            files if files is not None else {f"{provider}.bin": f"{provider}:data".encode()}
        )
        self.error = error
        self.bad_relative_path = bad_relative_path
        self.declared_digest_override = declared_digest_override
        self.calls = 0

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        artifacts: list[BackupArtifact] = []
        for name, content in sorted(self.files.items()):
            path = destination / name
            path.write_bytes(content)
            relative = self.bad_relative_path or f"{self.provider}/{name}"
            artifacts.append(
                BackupArtifact(
                    provider=self.provider,
                    name=name,
                    relative_path=relative,
                    size_bytes=path.stat().st_size,
                    sha256=self.declared_digest_override or sha256_file(path),
                    metadata={"operation_id": operation_id},
                )
            )
        return BackupContributorResult(
            provider=self.provider,
            state=BackupContributorState.SUCCEEDED,
            artifacts=tuple(artifacts),
            operation_id=operation_id,
        )


def _contributors(**overrides: FakeContributor) -> list[tuple[str, FakeContributor]]:
    entries: list[tuple[str, FakeContributor]] = []
    for provider in BACKUP_PROVIDER_ORDER:
        entries.append((provider, overrides.get(provider, FakeContributor(provider))))
    return entries


def _create(tmp_path: Path, **kwargs: Any) -> Any:
    kwargs.setdefault("target", tmp_path / "backup")
    kwargs.setdefault("contributors", _contributors())
    kwargs.setdefault("journal", InMemoryOperationJournalStore())
    kwargs.setdefault("deployment_id", "deploy-1")
    kwargs.setdefault("versions", {"phlo": "0.15.0"})
    return create_backup_set(**kwargs)


def _set_dir(result: Any) -> Path:
    return Path(result.target) / result.set_id


# --- create: success -------------------------------------------------------


def test_create_finalizes_manifest_once(tmp_path) -> None:
    result = _create(tmp_path)
    assert result.accepted is True
    assert result.state == "succeeded"
    set_dir = _set_dir(result)
    assert (set_dir / SET_MANIFEST_NAME).is_file()
    assert not (set_dir / SET_MANIFEST_STAGING_NAME).exists()
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == BACKUP_SET_SCHEMA_VERSION
    assert manifest["complete"] is True
    assert [record["provider"] for record in manifest["contributors"]] == list(
        BACKUP_PROVIDER_ORDER
    )


def test_create_hashes_artifacts_from_disk(tmp_path) -> None:
    result = _create(tmp_path)
    manifest = result.manifest
    for artifact in manifest["artifacts"]:
        path = _set_dir(result) / artifact["relative_path"]
        assert artifact["size_bytes"] == path.stat().st_size
        assert artifact["sha256"] == sha256_file(path)


def test_create_artifacts_are_sorted_deterministically(tmp_path) -> None:
    result = _create(tmp_path)
    paths = [artifact["relative_path"] for artifact in result.manifest["artifacts"]]
    assert paths == sorted(paths)


def test_create_refuses_existing_nonempty_target(tmp_path) -> None:
    target = tmp_path / "backup"
    target.mkdir()
    (target / "stale").write_text("x")
    with pytest.raises(BackupSetError):
        _create(tmp_path, target=target)


def test_create_refuses_duplicate_providers(tmp_path) -> None:
    duplicate = FakeContributor("postgres")
    with pytest.raises(BackupSetError, match="duplicate"):
        _create(
            tmp_path,
            contributors=[("postgres", FakeContributor("postgres")), ("postgres", duplicate)],
        )
    assert duplicate.calls == 0


# --- create: journal integration ------------------------------------------


def test_create_records_submitted_before_mutation(tmp_path, monkeypatch) -> None:
    journal = InMemoryOperationJournalStore()
    order: list[str] = []
    original_mkdir = Path.mkdir
    state_at_first_write: OperationJournalState | None = None

    def tracking_mkdir(self, *args: Any, **kwargs: Any) -> None:
        nonlocal state_at_first_write
        order.append("mkdir")
        state_at_first_write = next(iter(journal.entries.values())).state
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", tracking_mkdir)

    def quiesce() -> dict[str, Any]:
        order.append("quiesce")
        return {"quiesced": True}

    result = _create(tmp_path, journal=journal, quiesce=quiesce)
    assert result.accepted
    assert order[:2] == ["quiesce", "mkdir"]
    assert state_at_first_write is OperationJournalState.SUBMITTED
    entry = journal.read(f"backup.create:{result.set_id}")
    assert entry is not None and entry.state is OperationJournalState.SUCCEEDED


def test_create_replays_stored_result_without_rerunning(tmp_path) -> None:
    journal = InMemoryJournalWithResult()
    contributors = _contributors()
    result = _create(tmp_path, journal=journal, contributors=contributors, set_id="set-fixed")
    assert result.accepted is True
    assert all(contributor.calls == 0 for _, contributor in contributors)


def test_create_with_conflicting_claim_is_rejected(tmp_path) -> None:
    journal = InMemoryOperationJournalStore()
    claim_operation(
        journal,
        operation_id="backup.create:set-fixed",
        subject="other-operator",
        action="backup.create",
        target=str(tmp_path / "backup"),
        plan_token="set-fixed",
    )
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _create(tmp_path, journal=journal, set_id="set-fixed")


def test_quiesce_failure_leaves_no_set_and_records_failure(tmp_path) -> None:
    journal = InMemoryOperationJournalStore()

    def quiesce() -> dict[str, Any]:
        raise RuntimeError("quiesce unavailable")

    result = _create(tmp_path, journal=journal, quiesce=quiesce, set_id="set-q")
    assert result.accepted is False
    assert result.state == "failed"
    assert not (Path(result.target) / "set-q").exists()
    entry = journal.read("backup.create:set-q")
    assert entry is not None and entry.state is OperationJournalState.FAILED


# --- create: failure injection ---------------------------------------------


@pytest.mark.parametrize("provider", BACKUP_PROVIDER_ORDER)
def test_provider_failure_yields_unusable_set(tmp_path, provider) -> None:
    journal = InMemoryOperationJournalStore()
    result = _create(
        tmp_path,
        journal=journal,
        contributors=_contributors(
            **{provider: FakeContributor(provider, error=RuntimeError("boom"))}
        ),
    )
    assert result.accepted is False
    assert not (_set_dir(result) / SET_MANIFEST_NAME).exists()
    entry = journal.read(f"backup.create:{result.set_id}")
    assert entry.state is OperationJournalState.FAILED
    failed = {record["provider"]: record for record in result.contributors}
    assert failed[provider]["failure"] is not None
    for other in BACKUP_PROVIDER_ORDER:
        if BACKUP_PROVIDER_ORDER.index(other) > BACKUP_PROVIDER_ORDER.index(provider):
            assert failed[other]["failure"]["reason"] == "not_attempted"


def test_contributor_outside_owned_prefix_is_rejected(tmp_path) -> None:
    result = _create(
        tmp_path,
        contributors=_contributors(
            minio=FakeContributor("minio", bad_relative_path="postgres/stolen.bin")
        ),
    )
    assert result.accepted is False
    assert not (_set_dir(result) / SET_MANIFEST_NAME).exists()


def test_contributor_lying_about_digest_is_rejected(tmp_path) -> None:
    result = _create(
        tmp_path,
        contributors=_contributors(
            nessie=FakeContributor("nessie", declared_digest_override="0" * 64)
        ),
    )
    assert result.accepted is False
    assert not (_set_dir(result) / SET_MANIFEST_NAME).exists()


def test_failed_failure_message_is_sanitized(tmp_path) -> None:
    result = _create(
        tmp_path,
        contributors=_contributors(
            postgres=FakeContributor("postgres", error=RuntimeError("password=hunter2 leaked"))
        ),
    )
    assert result.failure is not None
    assert "hunter2" not in result.failure["reason"]


# --- verify: matrix ---------------------------------------------------------


def test_verify_accepts_untouched_set(tmp_path) -> None:
    result = _create(tmp_path)
    verified = verify_backup_set(_set_dir(result))
    assert verified.accepted is True
    assert verified.reasons == ()
    assert verified.state == "succeeded"


def test_verify_is_mutation_free(tmp_path, monkeypatch) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    before = {
        path: path.stat().st_mtime_ns for path in sorted(set_dir.rglob("*")) if path.is_file()
    }

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("verify must not mutate the filesystem")

    monkeypatch.setattr(Path, "write_text", explode)
    monkeypatch.setattr(Path, "write_bytes", explode)
    monkeypatch.setattr(os, "replace", explode)
    monkeypatch.setattr(shutil, "rmtree", explode)

    verified = verify_backup_set(set_dir)
    assert verified.accepted is True
    after = {path: path.stat().st_mtime_ns for path in sorted(set_dir.rglob("*")) if path.is_file()}
    assert before == after


def test_verify_rejects_partial_set_without_manifest(tmp_path) -> None:
    result = _create(
        tmp_path,
        contributors=_contributors(postgres=FakeContributor("postgres", error=RuntimeError("x"))),
    )
    verified = verify_backup_set(_set_dir(result))
    assert verified.accepted is False
    assert BackupVerificationReason("missing_manifest") in [
        BackupVerificationReason(reason) for reason in verified.reasons
    ]


def test_verify_rejects_staging_only_manifest(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest_path = set_dir / SET_MANIFEST_NAME
    staging = set_dir / SET_MANIFEST_STAGING_NAME
    staging.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    verified = verify_backup_set(set_dir)
    assert verified.accepted is False
    assert "missing_manifest" in verified.reasons


def test_verify_rejects_corrupt_manifest(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    (set_dir / SET_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    verified = verify_backup_set(set_dir)
    assert verified.accepted is False
    assert "corrupt_manifest" in verified.reasons


def test_verify_rejects_unknown_schema_version(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["schema_version"] = "999"
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert verified.accepted is False
    assert "unknown_schema_version" in verified.reasons


def test_verify_rejects_missing_provider(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["contributors"] = [
        record for record in manifest["contributors"] if record["provider"] != "nessie"
    ]
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "missing_provider" in verified.reasons


def test_verify_rejects_unknown_provider(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["contributors"].append(
        {
            "provider": "mystery",
            "state": "succeeded",
            "operation_id": manifest["contributors"][0]["operation_id"],
            "artifacts": [],
            "failure": None,
        }
    )
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "unknown_provider" in verified.reasons


def test_verify_rejects_incomplete_set(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["complete"] = False
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "partial_set" in verified.reasons


def test_verify_rejects_wrong_owner(tmp_path) -> None:
    result = _create(tmp_path)
    verified = verify_backup_set(_set_dir(result), expected_deployment_id="other-deploy")
    assert "wrong_owner" in verified.reasons


def test_verify_accepts_matching_owner(tmp_path) -> None:
    result = _create(tmp_path)
    verified = verify_backup_set(_set_dir(result), expected_deployment_id="deploy-1")
    assert verified.accepted is True


def test_verify_rejects_missing_version_inventory(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["versions"] = {}
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "incompatible_version" in verified.reasons


def test_verify_rejects_mixed_run(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["contributors"][0]["operation_id"] = "backup.create:other-set"
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "mixed_run" in verified.reasons


def test_verify_rejects_path_escape(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["artifacts"][0]["relative_path"] = "../escaped.bin"
    _rewrite_manifest(set_dir, manifest)
    verified = verify_backup_set(set_dir)
    assert "path_escape" in verified.reasons


def test_verify_rejects_symlink_artifact(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    artifact = manifest["artifacts"][0]
    real = set_dir / "real-target.bin"
    real.write_bytes(b"x")
    target = set_dir / artifact["relative_path"]
    target.unlink()
    target.symlink_to(real)
    verified = verify_backup_set(set_dir)
    assert "symlink" in verified.reasons


def test_verify_rejects_missing_artifact(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    target = set_dir / manifest["artifacts"][0]["relative_path"]
    target.unlink()
    verified = verify_backup_set(set_dir)
    assert "missing_artifact" in verified.reasons


def test_verify_rejects_corrupted_bytes(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    manifest = json.loads((set_dir / SET_MANIFEST_NAME).read_text(encoding="utf-8"))
    target = set_dir / manifest["artifacts"][0]["relative_path"]
    target.write_bytes(b"tampered")
    verified = verify_backup_set(set_dir)
    assert "digest_mismatch" in verified.reasons
    assert "size_mismatch" in verified.reasons


def test_verify_rejects_extra_artifact(tmp_path) -> None:
    result = _create(tmp_path)
    set_dir = _set_dir(result)
    (set_dir / "postgres" / "smuggled.bin").write_bytes(b"extra")
    verified = verify_backup_set(set_dir)
    assert "extra_artifact" in verified.reasons


# --- helpers -----------------------------------------------------------------


class InMemoryJournalWithResult(InMemoryOperationJournalStore):
    """Journal pre-loaded with a succeeded result for replay tests."""

    def __init__(self) -> None:
        super().__init__()
        entry = claim_operation(
            self,
            operation_id="backup.create:set-fixed",
            subject="operator",
            action="backup.create",
            target="unused",
            plan_token="set-fixed",
        )
        self.transition(entry.operation_id, OperationJournalState.SUBMITTED)
        self.transition(
            entry.operation_id,
            OperationJournalState.SUCCEEDED,
            {
                "set_id": "set-fixed",
                "target": "unused",
                "state": "succeeded",
                "accepted": True,
                "manifest": None,
                "contributors": [],
                "failure": None,
            },
        )


def _rewrite_manifest(set_dir: Path, manifest: dict[str, Any]) -> None:
    """Write a tampered manifest the way the coordinator would (canonically)."""
    (set_dir / SET_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def test_sha256_helper_imported() -> None:
    # Guard against accidental regression to non-shared primitives.
    from phlo.capabilities import continuity

    assert continuity.sha256_bytes(b"abc") == sha256_bytes(b"abc")
