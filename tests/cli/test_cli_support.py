"""Tests that the support status JSON payload keeps its stable contract.

Locks the JSON keys and status values, the exit codes (1 for incompatible,
2 for indeterminate when the manifest cannot load), and that a compatible
alpha release still reports production_ready as False.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from phlo.capabilities.support_status import support_status
from phlo.cli.commands.support import support_group


def test_support_status_json_reports_stable_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.support.support_status",
        lambda: {
            "compatible": True,
            "production_ready": False,
            "release": {"version": "0.12.1", "maturity": "alpha"},
            "manifest": {
                "source": "bundled",
                "trust": "trusted",
                "staleness": {"status": "unknown"},
            },
            "items": [
                {
                    "kind": "package",
                    "name": "phlo",
                    "expected": "0.12.1",
                    "installed": "0.12.1",
                    "status": "compatible",
                }
            ],
            "gates": {"security": "blocked"},
        },
    )

    result = CliRunner().invoke(support_group, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["compatible"] is True
    assert payload["production_ready"] is False
    assert payload["items"][0]["status"] == "compatible"


def test_support_status_exit_codes_and_human_output(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.support.support_status",
        lambda: {
            "compatible": False,
            "production_ready": False,
            "manifest": {"source": "bundled", "trust": "trusted", "staleness": {}},
            "items": [],
            "gates": {},
        },
    )

    result = CliRunner().invoke(support_group, ["status"])

    assert result.exit_code == 1
    assert "Compatible: False" in result.output


def test_support_status_is_indeterminate_when_manifest_cannot_load(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.support.support_status",
        lambda: {
            "compatible": None,
            "production_ready": None,
            "manifest": {"source": "bundled", "trust": "unavailable", "staleness": {}},
            "items": [],
            "gates": {},
        },
    )

    result = CliRunner().invoke(support_group, ["status", "--json"])

    assert result.exit_code == 2


def test_compatible_alpha_is_not_production_ready(monkeypatch) -> None:
    class Distribution:
        metadata = {"Name": "phlo"}
        version = "0.12.1"

    monkeypatch.setattr(
        "phlo.capabilities.support_status.load_support_manifest",
        lambda: {
            "current_release": {
                "version": "0.12.1",
                "maturity": "alpha",
                "production_ready": False,
            },
            "release_set": {"packages": [{"name": "phlo", "version": "0.12.1"}]},
            "gates": {"status": {"security": "blocked"}},
        },
    )
    monkeypatch.setattr(
        "phlo.capabilities.support_status.metadata.distributions", lambda: [Distribution()]
    )

    status = support_status()

    assert status["compatible"] is True
    assert status["production_ready"] is False
