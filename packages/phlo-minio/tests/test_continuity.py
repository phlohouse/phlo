"""Tests for the MinIO object backup contribution (Plan 011 Step 2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from phlo.capabilities.continuity import SET_MANIFEST_NAME, sha256_bytes
from phlo_minio.continuity import MinioBackupContributor

_OBJECTS: dict[tuple[str, str], bytes] = {
    ("evidence", "runs/run-1/report.json"): b'{"ok": true}',
    ("lake", "warehouse/db/table/data.parquet"): b"parquet-bytes",
    ("lake", "warehouse/db/table/metadata/0.json"): b"{}",
}

_BUCKETS = ["evidence", "lake", "minio"]


def _fake_mc(
    objects: dict[tuple[str, str], bytes] | None = None,
    buckets: list[str] | None = None,
    error: Exception | None = None,
) -> Callable[[list[str]], str | bytes]:
    resolved_objects = objects if objects is not None else _OBJECTS
    resolved_buckets = buckets if buckets is not None else _BUCKETS

    def mc(args: list[str]) -> str | bytes:
        if error is not None:
            raise error
        if args[:2] == ["ls", "--json"]:
            return "\n".join(json.dumps({"key": f"{bucket}/"}) for bucket in resolved_buckets)
        if args[:3] == ["ls", "--recursive", "--json"]:
            bucket = args[3].removeprefix("local/")
            return "\n".join(
                json.dumps({"key": key, "size": len(payload)})
                for (obj_bucket, key), payload in sorted(resolved_objects.items())
                if obj_bucket == bucket
            )
        if args[:1] == ["cat"]:
            target = args[1].removeprefix("local/")
            bucket, key = target.split("/", 1)
            # Raw object bytes, unfaithful to any text mould.
            return resolved_objects[(bucket, key)]
        raise AssertionError(f"unexpected mc invocation: {args}")

    return mc


def _contributor(
    objects: dict[tuple[str, str], bytes] | None = None,
    buckets: list[str] | None = None,
    error: Exception | None = None,
) -> MinioBackupContributor:
    """Build a contributor injecting the same fake as both text and bytes runner."""
    mc = _fake_mc(objects=objects, buckets=buckets, error=error)
    return MinioBackupContributor(mc_runner=mc, mc_bytes_runner=mc)


def test_contributor_copies_objects_and_writes_listing(tmp_path: Path) -> None:
    contributor = _contributor()
    destination = tmp_path / "set" / "minio"
    result = contributor.contribute(destination, operation_id="backup.create:set-1")

    assert result.state.value == "succeeded"
    # System bucket excluded; every user object copied preserving key paths.
    assert (destination / "lake" / "warehouse" / "db" / "table" / "data.parquet").read_bytes() == (
        b"parquet-bytes"
    )
    assert not (destination / "minio").exists() or not any((destination / "minio").iterdir())

    listing = json.loads((destination / "objects.json").read_text(encoding="utf-8"))
    assert listing["operation_id"] == "backup.create:set-1"
    assert len(listing["objects"]) == 3
    first = listing["objects"][0]
    assert first["sha256"] == sha256_bytes(b'{"ok": true}')

    listed_paths = {artifact.relative_path for artifact in result.artifacts}
    assert "minio/objects.json" in listed_paths
    assert "minio/lake/warehouse/db/table/data.parquet" in listed_paths
    for artifact in result.artifacts:
        assert artifact.relative_path.startswith("minio/")


def test_contributor_failure_is_sanitized(tmp_path: Path) -> None:
    contributor = _contributor(error=RuntimeError("mc denied access_key=AKIASECRET"))
    result = contributor.contribute(tmp_path / "set" / "minio", operation_id="op")

    assert result.state.value == "failed"
    assert result.failure is not None
    assert "AKIASECRET" not in result.failure["reason"]


def test_contributor_never_writes_outside_its_prefix_or_finalizes(tmp_path: Path) -> None:
    contributor = _contributor()
    set_dir = tmp_path / "set"
    contributor.contribute(set_dir / "minio", operation_id="op")

    assert not (set_dir / SET_MANIFEST_NAME).exists()
    assert {path.name for path in set_dir.iterdir()} == {"minio"}


# --- binary object content (byte-faithful capture) -------------------------


def _binary_objects() -> dict[tuple[str, str], bytes]:
    return {
        ("lake", "b/invalid-utf8.bin"): b"\xff\xfe\x00\x80binary\xc3\x28",
        ("lake", "b/nul.bin"): b"head\x00mid\x00tail",
        ("lake", "b/parquet.parquet"): bytes(range(256)),
    }


def test_contributor_captures_non_utf8_bytes_byte_faithfully(tmp_path: Path) -> None:
    contributor = _contributor(objects=_binary_objects(), buckets=["lake"])
    destination = tmp_path / "set" / "minio"
    result = contributor.contribute(destination, operation_id="op")

    assert result.state.value == "succeeded"
    listing = json.loads((destination / "objects.json").read_text(encoding="utf-8"))
    assert len(listing["objects"]) == 3
    listed = {entry["key"]: entry for entry in listing["objects"]}
    artifacts = {
        artifact.name: artifact for artifact in result.artifacts if artifact.name != "objects.json"
    }

    for (bucket, key), payload in _binary_objects().items():
        rel = destination / bucket / key
        # Object lands on disk as the exact source bytes (no text round-trip).
        assert rel.read_bytes() == payload
        entry = listed[key]
        assert entry["size_bytes"] == len(payload)
        assert entry["sha256"] == sha256_bytes(payload)
        assert artifacts[key.rsplit("/", 1)[-1]].sha256 == sha256_bytes(payload)


def test_contributor_roundtrips_arbitrary_binary_content(tmp_path: Path) -> None:
    payload = b"\x00\x01\xff\xfeGIF89a\n\x00binary-arbitrary\x00\xff"
    contributor = _contributor(objects={("lake", "blob/asset.dat"): payload}, buckets=["lake"])
    destination = tmp_path / "set" / "minio"
    result = contributor.contribute(destination, operation_id="op")

    assert result.state.value == "succeeded"
    restored_file = destination / "lake" / "blob" / "asset.dat"
    assert restored_file.read_bytes() == payload

    # Captured bytes survive a restore + reconcile digest check intact.
    from phlo.capabilities.continuity import RestoreTarget

    artifact = next(a for a in result.artifacts if a.name == "asset.dat")
    target = RestoreTarget.of(tmp_path / "target")
    step = contributor.restore(target, [artifact], "tok", str(destination.parent))
    assert step.state.value == "succeeded"
    assert contributor.reconcile(target, [artifact], "tok", str(destination.parent))["ok"] is True
    assert (Path(target.location) / "minio" / "lake" / "blob" / "asset.dat").read_bytes() == payload


# --- restore / reconcile (Plan 012 Step 3) --------------------------------


def test_restore_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import BackupArtifact, RestoreTarget, sha256_bytes

    content = b"parquet-bytes"
    set_dir = tmp_path / "_set"
    obj_dir = set_dir / "minio" / "lake" / "warehouse"
    obj_dir.mkdir(parents=True)
    (obj_dir / "t.parquet").write_bytes(content)
    artifact = BackupArtifact(
        provider="minio",
        name="t.parquet",
        relative_path="minio/lake/warehouse/t.parquet",
        size_bytes=len(content),
        sha256=sha256_bytes(content),
        metadata={"bucket": "lake"},
    )
    target = RestoreTarget.of(tmp_path / "target")
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
    step = contributor.restore(target, [artifact], "tok", str(set_dir))
    assert step.state.value == "succeeded"
    reconciliation = contributor.reconcile(target, [artifact], "tok", str(set_dir))
    assert reconciliation["ok"] is True
    assert (Path(target.location) / "minio" / "lake" / "warehouse" / "t.parquet").read_bytes() == (
        content
    )


def test_restore_reconcile_detects_corrupted_object(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import BackupArtifact, RestoreTarget, sha256_bytes

    content = b"parquet-bytes"
    set_dir = tmp_path / "_set"
    obj_dir = set_dir / "minio" / "lake"
    obj_dir.mkdir(parents=True)
    (obj_dir / "t.parquet").write_bytes(content)
    artifact = BackupArtifact(
        provider="minio",
        name="t.parquet",
        relative_path="minio/lake/t.parquet",
        size_bytes=len(content),
        sha256=sha256_bytes(content),
        metadata={"bucket": "lake"},
    )
    target = RestoreTarget.of(tmp_path / "target")
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
    contributor.restore(target, [artifact], "tok", str(set_dir))
    (Path(target.location) / "minio" / "lake" / "t.parquet").write_bytes(b"evil")
    reconciliation = contributor.reconcile(target, [artifact], "tok", str(set_dir))
    assert reconciliation["ok"] is False
    assert "digest_mismatch" in reconciliation["reason"]


# --- upgrade (Plan 013 Step 3) ---------------------------------------------


def test_upgrade_step_succeeds_and_reconciles(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import SUPPORTED_TO_VERSION, UpgradeStepDef

    defn = UpgradeStepDef("minio", "minio.policy", "policy", False, False)
    target = RestoreTarget.of(tmp_path / "deploy")
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
    step = contributor.upgrade_step(defn, target, "0.14.0", SUPPORTED_TO_VERSION, "tok")
    assert step.state.value == "succeeded"
    assert contributor.upgrade_reconcile(target, SUPPORTED_TO_VERSION, "tok")["ok"] is True


def test_upgrade_step_submission_failure_phase(tmp_path: Path) -> None:
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import UpgradeStepDef

    defn = UpgradeStepDef("minio", "minio.policy", "policy", False, False)
    blocker = tmp_path / "is-a-file"
    blocker.write_text("x")
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
    step = contributor.upgrade_step(defn, RestoreTarget.of(blocker), "0.14.0", "0.15.0", "t")
    assert step.state.value == "failed"
    assert step.phase.value == "submission"
