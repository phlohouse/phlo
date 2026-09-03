"""Tests for the Iceberg metadata backup contribution (Plan 011 Step 2)."""

from __future__ import annotations

import json
from pathlib import Path

from phlo.capabilities.continuity import SET_MANIFEST_NAME, sha256_bytes
from phlo_iceberg.continuity import IcebergBackupContributor


def _inventory() -> list[dict[str, object]]:
    return [
        {"table_name": "lake.orders", "snapshot_id": 42, "records": 10, "size_bytes": 100},
        {"table_name": "raw.events", "snapshot_id": 7, "records": 3, "size_bytes": 30},
    ]


def test_contributor_writes_sorted_inventory(tmp_path: Path) -> None:
    contributor = IcebergBackupContributor(inventory_fn=_inventory)
    destination = tmp_path / "set" / "iceberg"
    result = contributor.contribute(destination, operation_id="backup.create:set-1")

    assert result.state.value == "succeeded"
    artifact_path = destination / "inventory.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["operation_id"] == "backup.create:set-1"
    names = [table["table_name"] for table in payload["tables"]]
    assert names == sorted(names)
    artifact = result.artifacts[0]
    assert artifact.relative_path == "iceberg/inventory.json"
    assert artifact.sha256 == sha256_bytes(artifact_path.read_bytes())


def test_contributor_failure_is_sanitized(tmp_path: Path) -> None:
    def failing() -> list[dict[str, object]]:
        raise RuntimeError("catalog unavailable credential=abc")

    contributor = IcebergBackupContributor(inventory_fn=failing)
    result = contributor.contribute(tmp_path / "set" / "iceberg", operation_id="op")

    assert result.state.value == "failed"
    assert result.failure is not None
    assert "credential" not in result.failure["reason"]


def test_contributor_never_writes_outside_its_prefix_or_finalizes(tmp_path: Path) -> None:
    contributor = IcebergBackupContributor(inventory_fn=_inventory)
    set_dir = tmp_path / "set"
    contributor.contribute(set_dir / "iceberg", operation_id="op")

    assert not (set_dir / SET_MANIFEST_NAME).exists()
    assert {path.name for path in set_dir.iterdir()} == {"iceberg"}
