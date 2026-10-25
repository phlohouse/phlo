"""Tests for the PostgreSQL CLI authorization surface.

Package-specific command classification and mapping; the shared adapter
contract (read allow-through, unknown deny-closed, mutation policy allow/deny)
delegates to ``phlo_testing.authorization_surface``.
"""

from __future__ import annotations

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
)
from phlo_postgres.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    PostgresCliSurfaceAdapter,
)


class TestPostgresCliSurfaceAdapter:
    """Tests for PostgreSQL CLI surface adapter."""

    def test_read_commands_return_allow(self) -> None:
        """Read commands should return allow without enforcement."""
        assert_read_commands_allow_without_enforcement(PostgresCliSurfaceAdapter())

    def test_unknown_commands_return_deny(self) -> None:
        """Unknown commands should return deny."""
        assert_unknown_command_denied(PostgresCliSurfaceAdapter(), "postgres.unknown")

    def test_postgres_query_mutation_honors_policy_decision(self) -> None:
        """postgres.query honors both policy outcomes through enforcement."""
        assert_mutation_enforcement_allows_and_denies(PostgresCliSurfaceAdapter(), "postgres.query")


class TestCommandClassification:
    """Tests for command classification."""

    def test_postgres_query_is_mutation(self) -> None:
        """postgres query should be classified as mutation."""
        assert "postgres.query" in MUTATION_COMMANDS

    def test_postgres_dump_is_mutation(self) -> None:
        """postgres dump should be classified as mutation."""
        assert "postgres.dump" in MUTATION_COMMANDS

    def test_postgres_restore_is_mutation(self) -> None:
        """postgres restore should be classified as mutation."""
        assert "postgres.restore" in MUTATION_COMMANDS

    def test_postgres_vacuum_is_mutation(self) -> None:
        """postgres vacuum should be classified as mutation."""
        assert "postgres.vacuum" in MUTATION_COMMANDS

    def test_postgres_shell_is_mutation(self) -> None:
        """postgres (raw psql) should be classified as mutation."""
        assert "postgres" in MUTATION_COMMANDS

    def test_resource_mapping(self) -> None:
        """Commands should map to correct resources."""
        for cmd in MUTATION_COMMANDS:
            assert COMMAND_RESOURCE_MAP[cmd] == "dataset"

    def test_action_mapping(self) -> None:
        """Commands should map to correct actions."""
        assert COMMAND_ACTION_MAP["postgres.query"] == "dataset.query"
        assert COMMAND_ACTION_MAP["postgres.dump"] == "dataset.manage"
        assert COMMAND_ACTION_MAP["postgres.restore"] == "dataset.manage"
        assert COMMAND_ACTION_MAP["postgres.vacuum"] == "dataset.manage"
        assert COMMAND_ACTION_MAP["postgres"] == "dataset.query"
