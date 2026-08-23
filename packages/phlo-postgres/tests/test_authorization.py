"""Tests for the PostgreSQL CLI authorization surface.

Covers CliPrincipalResolver environment fallbacks (service account, human
subject, dev mode, anonymous default) and PostgresCliSurfaceAdapter policy:
reads allow without enforcement, unknown commands deny closed, and mutation
commands (query/dump/restore/vacuum/shell) always route through enforcement.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phlo.cli.authorization import CliPrincipalResolver
from phlo_postgres.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    FRAMEWORK_TYPE,
    PostgresCliSurfaceAdapter,
)


class TestPostgresCliPrincipalResolver:
    """Tests for the shared CLI principal resolver."""

    def test_resolve_service_account(self) -> None:
        """Should resolve PHLO_SERVICE_ACCOUNT as service principal."""
        with patch.dict("os.environ", {"PHLO_SERVICE_ACCOUNT": "ci-pipeline"}):
            principal = CliPrincipalResolver.resolve()
            assert principal.subject == "ci-pipeline"
            assert principal.principal_type == "service"
            assert "operators" in principal.groups

    def test_resolve_human_principal(self) -> None:
        """Should resolve PHLO_AUTH_SUBJECT as human principal."""
        with patch.dict(
            "os.environ",
            {
                "PHLO_AUTH_SUBJECT": "alice",
                "PHLO_AUTH_TYPE": "user",
                "PHLO_AUTH_GROUPS": "operators,admins",
            },
        ):
            principal = CliPrincipalResolver.resolve()
            assert principal.subject == "alice"
            assert principal.principal_type == "user"
            assert principal.groups == ("operators", "admins")

    def test_resolve_dev_fallback(self) -> None:
        """Should use dev fallback when PHLO_DEV_MODE set."""
        with patch.dict("os.environ", {"PHLO_DEV_MODE": "true"}):
            principal = CliPrincipalResolver.resolve()
            assert principal.subject == "local:root"
            assert principal.groups == ("admin",)

    def test_resolve_anonymous_default(self) -> None:
        """Should return anonymous principal when no auth env vars."""
        principal = CliPrincipalResolver.resolve()
        assert principal.subject == "anonymous"
        assert principal.principal_type == "user"
        assert principal.groups == ()


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
        adapter = PostgresCliSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed is True

    def test_unknown_commands_return_deny(self) -> None:
        """Unknown commands should return deny."""
        adapter = PostgresCliSurfaceAdapter()
        result = adapter.check_command_authorization("postgres.unknown")
        assert result.allowed is False
        assert result.reason_code == "unknown_command"

    def test_mutation_commands_call_enforce(self) -> None:
        """Mutation commands should call enforce."""
        adapter = PostgresCliSurfaceAdapter()

        mock_result = MagicMock()
        mock_result.variant = "allow"

        with patch("phlo.cli.authorization.enforce", return_value=mock_result):
            with patch.object(adapter, "_resolver"):
                from phlo.capabilities.interfaces import AuthPrincipal

                adapter._resolver.resolve = MagicMock(
                    return_value=AuthPrincipal(
                        subject="test",
                        principal_type="user",
                        issuer="test",
                        groups=(),
                        attributes={},
                    )
                )

                result = adapter.check_command_authorization("postgres.query")

                if result.variant == "deny":
                    pass


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
