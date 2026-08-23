"""Tests for the governance CLI commands, run against a cleared flow-declaration registry.

Each test starts from an empty global flow-declaration registry and
leaves nothing behind. Covers unsafe publishes failing with structured
warnings, contract-governed publishes passing, read-model export, and
clean errors for bad declaration modules.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

import phlo
from phlo.cli.commands.governance import governance_group
from phlo.cli.main import cli

pytestmark = pytest.mark.core_regression


@pytest.fixture(autouse=True)
def _clear_flow_declarations() -> Iterator[None]:
    # Decorator-declared flows accumulate in global registry state, so every
    # test starts from an empty registry and leaves nothing behind.
    phlo.clear_flow_declarations()
    yield
    phlo.clear_flow_declarations()


def test_governance_check_fails_for_unsafe_publish() -> None:
    @phlo.publish(table="gold.customer_health", audience=["sales"])
    def publish_customer_health() -> None:
        return None

    result = CliRunner().invoke(governance_group, ["check", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["warning_count"] == 2
    assert [warning["code"] for warning in payload["warnings"]] == [
        "missing_owner",
        "missing_access_policy",
    ]


def test_governance_check_passes_for_governed_publish() -> None:
    @phlo.contract(
        table="gold.customer_health",
        owner="data-platform",
        freshness_hours=6,
        lifecycle="production",
    )
    def customer_health_contract() -> None:
        return None

    @phlo.publish(table="gold.customer_health", owner="data-platform")
    def publish_customer_health() -> None:
        return None

    @phlo.access(table="gold.customer_health", roles=["sales_read"])
    def customer_health_access() -> None:
        return None

    result = CliRunner().invoke(governance_group, ["check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {"ok": True, "warning_count": 0, "warnings": []}


def test_governance_export_emits_read_model() -> None:
    @phlo.publish(table="gold.customer_health", owner="data-platform")
    def publish_customer_health() -> None:
        return None

    result = CliRunner().invoke(governance_group, ["export", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["tables"][0]["table"] == "gold.customer_health"
    assert payload["tables"][0]["published"] is True


def test_governance_check_imports_declaration_module(tmp_path: Path) -> None:
    workflow_file = tmp_path / "customer_health_flow.py"
    workflow_file.write_text(
        """
import phlo

@phlo.contract(
    table="gold.customer_health",
    owner="data-platform",
    freshness_hours=6,
    lifecycle="production",
)
def customer_health_contract():
    pass

@phlo.publish(table="gold.customer_health", owner="data-platform")
def publish_customer_health():
    pass

@phlo.access(table="gold.customer_health", roles=["sales_read"])
def customer_health_access():
    pass
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        governance_group,
        ["check", "--json", "--module", str(workflow_file)],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "warning_count": 0, "warnings": []}


def test_governance_check_with_module_clears_previous_declarations(tmp_path: Path) -> None:
    @phlo.publish(table="gold.leaked_table")
    def leaked_publish() -> None:
        return None

    workflow_file = tmp_path / "customer_health_flow.py"
    workflow_file.write_text(
        """
import phlo

@phlo.publish(table="gold.customer_health", owner="data-platform")
def publish_customer_health():
    pass

@phlo.access(table="gold.customer_health", roles=["sales_read"])
def customer_health_access():
    pass
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        governance_group,
        ["check", "--json", "--module", str(workflow_file)],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "warning_count": 0, "warnings": []}


def test_governance_check_bad_module_is_clean_error(tmp_path: Path) -> None:
    workflow_file = tmp_path / "bad_flow.py"
    workflow_file.write_text("def broken(:\n", encoding="utf-8")

    result = CliRunner().invoke(
        governance_group,
        ["check", "--json", "--module", str(workflow_file)],
    )

    assert result.exit_code == 1
    assert "Could not load governance module" in result.output
    assert "Traceback" not in result.output


def test_governance_group_is_registered_on_root_cli() -> None:
    result = CliRunner().invoke(cli, ["governance", "check", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "warning_count": 0, "warnings": []}
