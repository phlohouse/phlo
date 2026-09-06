"""Tests for the Polaris release-ledger backup contributor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from phlo_polaris.continuity import PolarisBackupContributor


class FakeStore:
    def __init__(self) -> None:
        self._rows = [
            {"kind": "state", "table_name": "__state__", "revision": 1},
            {"kind": "release", "table_name": "bronze.events", "revision": 1},
        ]

    def rows(self) -> list[dict]:
        return list(self._rows)


class FakeCatalog:
    def __init__(self) -> None:
        self.store = FakeStore()


def test_contribute_writes_sorted_ledger_export(tmp_path: Path) -> None:
    contributor = PolarisBackupContributor(catalog=FakeCatalog())
    result = contributor.contribute(tmp_path, operation_id="op-1")

    artifact_path = tmp_path / "releases.json"
    payload = json.loads(artifact_path.read_text())
    assert payload["operation_id"] == "op-1"
    assert payload["schema_version"] == "1"
    assert len(payload["releases"]) == 2
    artifact = result.artifacts[0]
    assert artifact.provider == "polaris"
    assert artifact.name == "releases.json"
    assert artifact.size_bytes == artifact_path.stat().st_size
    assert artifact.sha256 == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert result.state.value == "succeeded"


def test_contribute_fails_closed_on_ledger_errors(tmp_path: Path) -> None:
    class BrokenStore:
        def rows(self):
            raise RuntimeError("catalog unavailable: secret=s3cr3t")

    class BrokenCatalog:
        store = BrokenStore()

    contributor = PolarisBackupContributor(catalog=BrokenCatalog())
    result = contributor.contribute(tmp_path, operation_id="op-2")
    assert result.state.value == "failed"
    assert "s3cr3t" not in str(result)
