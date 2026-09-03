"""Tests for the guarded backup CLI (Plan 011, ADR 0049 §3)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from phlo.capabilities.continuity import BACKUP_PROVIDER_ORDER
from phlo.cli.commands.operations import backup_group


class _StubContributor:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def contribute(self, destination: Any, operation_id: str) -> Any:
        from phlo.capabilities.continuity import (
            BackupArtifact,
            BackupContributorResult,
            BackupContributorState,
            sha256_file,
        )

        destination = destination / f"{self.provider}.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.provider.encode())
        return BackupContributorResult(
            provider=self.provider,
            state=BackupContributorState.SUCCEEDED,
            artifacts=(
                BackupArtifact(
                    provider=self.provider,
                    name=f"{self.provider}.bin",
                    relative_path=f"{self.provider}/{self.provider}.bin",
                    size_bytes=destination.stat().st_size,
                    sha256=sha256_file(destination),
                    metadata={},
                ),
            ),
            operation_id=operation_id,
        )


@pytest.fixture()
def stubbed_contributors(monkeypatch):
    contributors = [(provider, _StubContributor(provider)) for provider in BACKUP_PROVIDER_ORDER]
    monkeypatch.setattr(
        "phlo.capabilities.list_capabilities",
        lambda _family: [provider for provider, _ in contributors],
    )
    monkeypatch.setattr(
        "phlo.capabilities.resolve_capability",
        lambda _family, name: next(
            (
                SimpleNamespace(name=provider, provider=contributor)
                for provider, contributor in contributors
                if provider == name
            ),
            None,
        ),
    )
    return contributors


def _invoke(args: list[str], journal_dir: Path | None = None) -> Any:
    return CliRunner().invoke(
        backup_group,
        args,
        env={"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir)} if journal_dir else {},
    )


def test_create_and_verify_round_trip(stubbed_contributors, tmp_path) -> None:
    target = tmp_path / "backup"
    result = _invoke(
        ["create", "--target", str(target), "--format", "json"],
        journal_dir=tmp_path / "journal",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["state"] == "succeeded"
    assert payload["accepted"] is True
    set_dir = target / payload["set_id"]
    assert (set_dir / "manifest.json").is_file()

    verified = _invoke(["verify", "--backup-set", str(set_dir), "--format", "json"])
    assert verified.exit_code == 0, verified.output
    verify_payload = json.loads(verified.output)
    assert verify_payload["accepted"] is True
    assert verify_payload["reasons"] == []


def test_create_fails_when_a_provider_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "phlo.capabilities.list_capabilities", lambda _family: list(BACKUP_PROVIDER_ORDER)
    )
    monkeypatch.setattr("phlo.capabilities.resolve_capability", lambda _family, name: None)
    result = _invoke(["create", "--target", str(tmp_path / "backup")])
    assert result.exit_code != 0
    assert "backup contributor" in result.output


def test_create_fails_closed_without_a_durable_journal(stubbed_contributors, tmp_path) -> None:
    result = _invoke(["create", "--target", str(tmp_path / "backup")])
    assert result.exit_code != 0
    assert "PHLO_OPERATIONS_JOURNAL_DIR" in result.output


def test_create_records_in_a_durable_journal(stubbed_contributors, tmp_path) -> None:
    journal_dir = tmp_path / "journal"
    target = tmp_path / "backup"
    result = _invoke(
        ["create", "--target", str(target), "--format", "json"], journal_dir=journal_dir
    )
    assert result.exit_code == 0, result.output
    assert list(journal_dir.glob("backup.create_*.json"))


def test_verify_reports_partial_set_with_nonzero_exit(stubbed_contributors, tmp_path) -> None:
    target = tmp_path / "backup"
    result = _invoke(
        ["create", "--target", str(target), "--format", "json"],
        journal_dir=tmp_path / "journal",
    )
    payload = json.loads(result.output)
    set_dir = target / payload["set_id"]
    manifest_path = set_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["complete"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verified = _invoke(["verify", "--backup-set", str(set_dir)])
    assert verified.exit_code == 1
    assert "partial_set" in verified.output
