"""Tests for the Trino CLI authorization surface.

Package-specific command classification and mapping plus the shared
surface-adapter contract from ``phlo_testing.authorization_surface``: reads
skip enforcement, unknown commands deny closed, and mutation commands honor
policy allow/deny.
"""

from __future__ import annotations

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
)
from phlo_trino.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    TrinoCliSurfaceAdapter,
)


class TestTrinoCliSurfaceAdapter:
    """Tests for Trino CLI surface adapter."""

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
