"""Tests for the PostgreSQL CLI authorization surface.

Package-specific adapter metadata and command-table classification; the
shared adapter contract (read allow-through, unknown deny-closed,
mutation policy allow/deny) and the CliPrincipalResolver environment
fallbacks delegate to ``phlo_testing.authorization_surface``.
"""

from __future__ import annotations

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    run_cli_principal_resolver_contract,
)
from phlo_postgres.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    SURFACE_NAME,
    FRAMEWORK_TYPE,
    PostgresCliSurfaceAdapter,
)


def test_cli_principal_resolver_environment_fallbacks() -> None:
    """Shared CliPrincipalResolver environment fallback contract."""
    run_cli_principal_resolver_contract()


class TestPostgresCliSurfaceAdapter:
    """Tests for PostgreSQL CLI surface adapter."""

    def test_singleton_instance(self) -> None:
        """Should return singleton instance."""
        adapter1 = PostgresCliSurfaceAdapter.get_instance()
        adapter2 = PostgresCliSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_surface_name(self) -> None:
        """Should return correct surface name."""
        adapter = PostgresCliSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self) -> None:
        """Should return correct framework type."""
        adapter = PostgresCliSurfaceAdapter()
        assert adapter.framework_type == FRAMEWORK_TYPE

    def test_list_operations(self) -> None:
        """Should declare mutation operations."""
        adapter = PostgresCliSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        for op in operations:
            assert op["action"] in COMMAND_ACTION_MAP.values()
            assert op["resource_type"] in COMMAND_RESOURCE_MAP.values()

    def test_is_active(self) -> None:
        """Should always return True."""
        adapter = PostgresCliSurfaceAdapter()
        assert adapter.is_active(None) is True

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
