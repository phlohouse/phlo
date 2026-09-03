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
