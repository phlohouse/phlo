"""Tests for the neutral continuity/backup-set contracts (Plan 011 Step 1)."""

from __future__ import annotations

import json

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
    BackupSetManifest,
    BackupVerificationReason,
    canonical_json_bytes,
    fail_contributor,
    redact_failure,
    redact_message,
    sha256_bytes,
    sha256_file,
    sha256_tree,
    validate_contained_path,
    write_manifest_atomically,
)


def _artifact(provider: str, name: str, content: bytes = b"data") -> BackupArtifact:
    return BackupArtifact(
        provider=provider,
        name=name,
        relative_path=f"{provider}/{name}",
        size_bytes=len(content),
        sha256=sha256_bytes(content),
        metadata={},
    )


def _manifest(**overrides: object) -> BackupSetManifest:
    values: dict[str, object] = {
        "schema_version": BACKUP_SET_SCHEMA_VERSION,
        "set_id": "set-123",
        "source_deployment_id": "deploy-1",
        "created_at": "2026-09-01T00:00:00+00:00",
        "versions": {"phlo": "0.15.0"},
        "quiesce": {"quiesced": True},
        "contributors": tuple(
            BackupContributorResult(
                provider=provider,
                state=BackupContributorState.SUCCEEDED,
                operation_id="backup.create:set-123",
            )
            for provider in BACKUP_PROVIDER_ORDER
        ),
        "artifacts": tuple(
            _artifact(provider, f"{provider}.bin") for provider in BACKUP_PROVIDER_ORDER
        ),
        "complete": True,
    }
    values.update(overrides)
    return BackupSetManifest(**values)  # type: ignore[arg-type]


def test_canonical_json_is_deterministic() -> None:
    payload = {"b": 1, "a": {"d": 2, "c": 3}}
    assert canonical_json_bytes(payload) == canonical_json_bytes({"a": {"c": 3, "d": 2}, "b": 1})


def test_manifest_round_trip_preserves_contract() -> None:
    manifest = _manifest()
    restored = BackupSetManifest.from_dict(json.loads(json.dumps(manifest.to_dict())))
    assert restored == manifest
    assert restored.manifest_digest() == manifest.manifest_digest()


def test_manifest_serialization_is_deterministic() -> None:
    first = _manifest().canonical_bytes()
    second = _manifest().canonical_bytes()
    assert first == second


def test_manifest_rejects_unknown_schema_version() -> None:
    data = _manifest().to_dict()
    data["schema_version"] = "999"
    with pytest.raises(BackupSetError) as excinfo:
        BackupSetManifest.from_dict(data)
    assert excinfo.value.reason is BackupVerificationReason.UNKNOWN_SCHEMA_VERSION


def test_manifest_rejects_missing_identity() -> None:
    data = _manifest().to_dict()
    data["source_deployment_id"] = ""
    with pytest.raises(BackupSetError):
        BackupSetManifest.from_dict(data)


def test_contributor_result_redacts_failures() -> None:
    result = fail_contributor("postgres", "pg_dump failed password=hunter2", "op:1")
    assert result.state is BackupContributorState.FAILED
    assert result.failure == {"reason": "pg_dump failed REDACTED"}


def test_redact_failure_handles_nested_secrets() -> None:
    redacted = redact_failure({"cmd": "x", "env": {"SECRET_KEY": "abc", "safe": "1"}})
    assert redacted is not None
    assert redacted["env"]["SECRET_KEY"] == "REDACTED"
    assert redacted["env"]["safe"] == "1"


def test_redact_message_only_redacts_secret_words() -> None:
    assert redact_message("token=abc and normal words") == "REDACTED and normal words"


def test_sha256_file_and_tree_are_stable(tmp_path) -> None:
    (tmp_path / "a").write_bytes(b"one")
    (tmp_path / "b").write_bytes(b"two")
    assert sha256_file(tmp_path / "a") == sha256_file(tmp_path / "a")
    assert sha256_tree(tmp_path) == sha256_tree(tmp_path)


def test_sha256_tree_is_path_sensitive(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root, content in ((first, b"one"), (second, b"two")):
        root.mkdir()
        (root / "file").write_bytes(content)
    assert sha256_tree(first) != sha256_tree(second)


def test_sha256_tree_rejects_symlinks(tmp_path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"x")
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(BackupSetError) as excinfo:
        sha256_tree(tmp_path)
    assert excinfo.value.reason is BackupVerificationReason.SYMLINK


def test_validate_contained_path_rejects_escape(tmp_path) -> None:
    with pytest.raises(BackupSetError) as excinfo:
        validate_contained_path(tmp_path, "../escape")
    assert excinfo.value.reason is BackupVerificationReason.PATH_ESCAPE
    with pytest.raises(BackupSetError):
        validate_contained_path(tmp_path, "/absolute/path")


def test_validate_contained_path_allows_nested_paths(tmp_path) -> None:
    resolved = validate_contained_path(tmp_path, "postgres/nested/file.bin")
    assert resolved.is_relative_to(tmp_path.resolve())


def test_write_manifest_atomically_finalizes_once(tmp_path) -> None:
    manifest = _manifest()
    write_manifest_atomically(tmp_path, manifest)
    assert (tmp_path / SET_MANIFEST_NAME).is_file()
    assert not (tmp_path / SET_MANIFEST_STAGING_NAME).exists()


def test_artifact_round_trip_preserves_fields() -> None:
    artifact = _artifact("postgres", "phlo.sql.gz")
    restored = BackupArtifact.from_dict(artifact.to_dict())
    assert restored == artifact


def test_contributor_result_round_trip() -> None:
    result = BackupContributorResult(
        provider="minio",
        state=BackupContributorState.SUCCEEDED,
        artifacts=(_artifact("minio", "objects.json"),),
        operation_id="backup.create:set-123",
    )
    restored = BackupContributorResult.from_dict(result.to_dict())
    assert restored == result
