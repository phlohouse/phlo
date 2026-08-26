"""Tests for the Trino CLI authorization surface.

Package-specific command-table coverage plus the shared surface-adapter
contract from ``phlo_testing.authorization_surface``: reads skip enforcement,
unknown commands deny closed, mutation commands honor policy allow/deny, and
the shared ``CliPrincipalResolver`` environment fallbacks hold.
"""

from __future__ import annotations

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
    run_cli_principal_resolver_contract,
)
from phlo_trino.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    FRAMEWORK_TYPE,
    MUTATION_COMMANDS,
    SURFACE_NAME,
    TrinoCliSurfaceAdapter,
)


def test_cli_principal_resolver_contract():
    """The shared CLI principal resolver environment fallbacks hold."""
    run_cli_principal_resolver_contract()


class TestTrinoCliSurfaceAdapter:
    """Tests for Trino CLI surface adapter."""

    def test_singleton_instance(self) -> None:
        """Should return singleton instance."""
        adapter1 = TrinoCliSurfaceAdapter.get_instance()
        adapter2 = TrinoCliSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_surface_name(self) -> None:
        """Should return correct surface name."""
        adapter = TrinoCliSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self) -> None:
        """Should return correct framework type."""
        adapter = TrinoCliSurfaceAdapter()
        assert adapter.framework_type == FRAMEWORK_TYPE

    def test_list_operations(self) -> None:
        """Should declare mutation operations."""
        adapter = TrinoCliSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        for op in operations:
            assert op["action"] in COMMAND_ACTION_MAP.values()
            assert op["resource_type"] in COMMAND_RESOURCE_MAP.values()

    def test_is_active(self) -> None:
        """Should always return True."""
        adapter = TrinoCliSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_read_commands_allow_without_enforcement(self) -> None:
        """Read commands allow without touching the enforcement path."""
        with reset_surface_adapter_singleton(TrinoCliSurfaceAdapter):
            assert_read_commands_allow_without_enforcement(TrinoCliSurfaceAdapter())

    def test_unknown_command_denied_closed(self) -> None:
        """Unclassified commands deny closed so new commands cannot skip auth."""
        with reset_surface_adapter_singleton(TrinoCliSurfaceAdapter):
            assert_unknown_command_denied(TrinoCliSurfaceAdapter(), "trino.unknown")

    def test_trino_query_mutation_honors_policy_decision(self) -> None:
        """trino.query flows a policy allow through and blocks a policy deny."""
        with reset_surface_adapter_singleton(TrinoCliSurfaceAdapter):
            assert_mutation_enforcement_allows_and_denies(TrinoCliSurfaceAdapter(), "trino.query")


class TestCommandClassification:
    """Tests for command classification."""

    def test_trino_query_is_mutation(self) -> None:
        """trino query should be classified as mutation."""
        assert "trino.query" in MUTATION_COMMANDS

    def test_trino_shell_is_mutation(self) -> None:
        """trino (shell) should be classified as mutation."""
        assert "trino" in MUTATION_COMMANDS

    def test_resource_mapping(self) -> None:
        """Commands should map to correct resources."""
        assert COMMAND_RESOURCE_MAP["trino.query"] == "dataset"
        assert COMMAND_RESOURCE_MAP["trino"] == "dataset"

    def test_action_mapping(self) -> None:
        """Commands should map to correct actions."""
        assert COMMAND_ACTION_MAP["trino.query"] == "dataset.query"
        assert COMMAND_ACTION_MAP["trino"] == "dataset.query"
