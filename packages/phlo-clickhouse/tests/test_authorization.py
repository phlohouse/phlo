"""Tests for ClickHouse authorization module.

Package-specific command-table coverage plus the shared surface-adapter
contract from ``phlo_testing.authorization_surface``: reads skip enforcement,
unknown commands deny closed, mutation commands honor policy allow/deny, and
the shared ``CliPrincipalResolver`` environment fallbacks hold.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from phlo.cli.authorization import CliPrincipalResolver
from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
    run_cli_principal_resolver_contract,
)
from phlo_clickhouse.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    ClickHouseSurfaceAdapter,
)

pytestmark = pytest.mark.core_regression


class TestCliPrincipalResolver:
    """Extra principal-resolver edge case beyond the shared contract."""

    def test_resolve_human_principal_empty_groups(self):
        """PHLO_AUTH_SUBJECT with no groups."""
        env = {
            "PHLO_AUTH_SUBJECT": "user@example.com",
            "PHLO_AUTH_TYPE": "user",
        }
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "user@example.com"
            assert principal.groups == ()


def test_cli_principal_resolver_contract():
    """The shared CLI principal resolver environment fallbacks hold."""
    run_cli_principal_resolver_contract()


class TestClickHouseSurfaceAdapter:
    """Tests for ClickHouseSurfaceAdapter."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        with reset_surface_adapter_singleton(ClickHouseSurfaceAdapter):
            yield

    def test_surface_name(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = ClickHouseSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = ClickHouseSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = ClickHouseSurfaceAdapter.get_instance()
        adapter2 = ClickHouseSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_command_mapped_correctly(self):
        """All mutation commands have resource and action mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for ClickHouse mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        with reset_surface_adapter_singleton(ClickHouseSurfaceAdapter):
            yield

    def test_read_commands_allow_without_enforcement(self):
        """Read commands allow without touching the enforcement path."""
        assert_read_commands_allow_without_enforcement(ClickHouseSurfaceAdapter())

    def test_unknown_command_denied_closed(self):
        """Unknown commands are denied closed."""
        assert_unknown_command_denied(ClickHouseSurfaceAdapter(), "clickhouse.unknown")

    def test_clickhouse_query_mutation_honors_policy_decision(self):
        """clickhouse.query flows a policy allow through and blocks a policy deny."""
        assert_mutation_enforcement_allows_and_denies(
            ClickHouseSurfaceAdapter(), "clickhouse.query"
        )


class TestMutationCommandLists:
    """Tests for command classification lists."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_clickhouse_commands_covered(self):
        """All clickhouse subcommands are classified."""
        all_clickhouse = {"clickhouse.query", "clickhouse.status"}
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_clickhouse - classified
        assert unclassified == set(), f"Unclassified clickhouse commands: {unclassified}"


class TestResourceActionMapping:
    """Tests for command to resource/action mapping."""

    def test_query_command_resource_mapping(self):
        """clickhouse.query maps to dataset resource."""
        assert COMMAND_RESOURCE_MAP["clickhouse.query"] == "dataset"

    def test_query_command_action_mapping(self):
        """clickhouse.query maps to dataset.write action."""
        assert COMMAND_ACTION_MAP["clickhouse.query"] == "dataset.write"

    def test_action_format(self):
        """All actions follow resource.action pattern."""
        for cmd, action in COMMAND_ACTION_MAP.items():
            parts = action.split(".")
            assert len(parts) >= 2, (
                f"Action {action} for {cmd} should have at least resource.action"
            )
