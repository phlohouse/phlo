"""Tests for the MinIO object backup contribution (Plan 011 Step 2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from phlo.capabilities.continuity import SET_MANIFEST_NAME, sha256_bytes
from phlo_minio.continuity import MinioBackupContributor

_OBJECTS: dict[tuple[str, str], str] = {
    ("evidence", "runs/run-1/report.json"): '{"ok": true}',
    ("lake", "warehouse/db/table/data.parquet"): "parquet-bytes",
    ("lake", "warehouse/db/table/metadata/0.json"): "{}",
}

_BUCKETS = ["evidence", "lake", "minio"]


def _fake_mc(
    objects: dict[tuple[str, str], str] | None = None,
    buckets: list[str] | None = None,
    error: Exception | None = None,
) -> Callable[[list[str]], str]:
    resolved_objects = objects if objects is not None else _OBJECTS
    resolved_buckets = buckets if buckets is not None else _BUCKETS

    def mc(args: list[str]) -> str:
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
            return resolved_objects[(bucket, key)]
        raise AssertionError(f"unexpected mc invocation: {args}")

    return mc


def test_contributor_copies_objects_and_writes_listing(tmp_path: Path) -> None:
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
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
    contributor = MinioBackupContributor(
        mc_runner=_fake_mc(error=RuntimeError("mc denied access_key=AKIASECRET"))
    )
    result = contributor.contribute(tmp_path / "set" / "minio", operation_id="op")

    assert result.state.value == "failed"
    assert result.failure is not None
    assert "AKIASECRET" not in result.failure["reason"]


def test_contributor_never_writes_outside_its_prefix_or_finalizes(tmp_path: Path) -> None:
    contributor = MinioBackupContributor(mc_runner=_fake_mc())
    set_dir = tmp_path / "set"
    contributor.contribute(set_dir / "minio", operation_id="op")

    assert not (set_dir / SET_MANIFEST_NAME).exists()
    assert {path.name for path in set_dir.iterdir()} == {"minio"}
