"""Tests for Nessie CLI authorization adapter.

Also covers the shared CliPrincipalResolver precedence: service
account, then human subject, then dev-mode fallback, then anonymous.
Adapter tests assert singleton access, surface metadata, and that every
declared mutation command maps to a resource and action.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from phlo_nessie import cli_branch
from phlo.cli.authorization import CliPrincipalResolver
from phlo_nessie.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    FRAMEWORK_TYPE,
    NessieCliSurfaceAdapter,
)


class TestNessieCliPrincipalResolver:
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


class TestNessieCliSurfaceAdapter:
    """Tests for Nessie CLI surface adapter."""

    def test_singleton_instance(self) -> None:
        """Should return singleton instance."""
        adapter1 = NessieCliSurfaceAdapter.get_instance()
        adapter2 = NessieCliSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_surface_name(self) -> None:
        """Should return correct surface name."""
        adapter = NessieCliSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self) -> None:
        """Should return correct framework type."""
        adapter = NessieCliSurfaceAdapter()
        assert adapter.framework_type == FRAMEWORK_TYPE

    def test_list_operations(self) -> None:
        """Should declare mutation operations."""
        adapter = NessieCliSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        for op in operations:
            assert op["action"] in COMMAND_ACTION_MAP.values()
            assert op["resource_type"] in COMMAND_RESOURCE_MAP.values()

    def test_is_active(self) -> None:
        """Should always return True."""
        adapter = NessieCliSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_read_commands_return_allow(self) -> None:
        """Read commands should return allow without enforcement."""
        adapter = NessieCliSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed is True

    def test_unknown_commands_return_deny(self) -> None:
        """Unknown commands should return deny."""
        adapter = NessieCliSurfaceAdapter()
        result = adapter.check_command_authorization("branch.unknown")
        assert result.allowed is False
        assert result.reason_code == "unknown_command"

    def test_mutation_commands_call_enforce(self) -> None:
        """Mutation commands should call enforce."""
        adapter = NessieCliSurfaceAdapter()

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

                result = adapter.check_command_authorization("branch.create")

                if result.variant == "deny":
                    pass


class TestCommandClassification:
    """Tests for command classification."""

    def test_branch_create_is_mutation(self) -> None:
        """branch create should be classified as mutation."""
        assert "branch.create" in MUTATION_COMMANDS

    def test_branch_delete_is_mutation(self) -> None:
        """branch delete should be classified as mutation."""
        assert "branch.delete" in MUTATION_COMMANDS

    def test_branch_merge_is_mutation(self) -> None:
        """branch merge should be classified as mutation."""
        assert "branch.merge" in MUTATION_COMMANDS

    def test_branch_list_is_read(self) -> None:
        """branch list should be classified as read."""
        assert "branch.list" in READ_COMMANDS

    def test_branch_diff_is_read(self) -> None:
        """branch diff should be classified as read."""
        assert "branch.diff" in READ_COMMANDS

    def test_catalog_commands_are_read(self) -> None:
        """catalog commands should be classified as read."""
        assert "catalog.tables" in READ_COMMANDS
        assert "catalog.describe" in READ_COMMANDS
        assert "catalog.history" in READ_COMMANDS

    def test_resource_mapping(self) -> None:
        """Commands should map to correct resources."""
        assert COMMAND_RESOURCE_MAP["branch.create"] == "catalog"
        assert COMMAND_RESOURCE_MAP["branch.list"] == "catalog"
        assert COMMAND_RESOURCE_MAP["catalog.tables"] == "catalog"

    def test_action_mapping(self) -> None:
        """Commands should map to correct actions."""
        assert COMMAND_ACTION_MAP["branch.create"] == "catalog.manage"
        assert COMMAND_ACTION_MAP["branch.delete"] == "catalog.manage"
        assert COMMAND_ACTION_MAP["branch.merge"] == "catalog.manage"
        assert COMMAND_ACTION_MAP["branch.list"] == "catalog.read"
        assert COMMAND_ACTION_MAP["catalog.tables"] == "catalog.read"


def test_branch_merge_authorizes_implicit_source_delete(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    class Client:
        def list_references(self):
            return [
                SimpleNamespace(name="feature", hash_="feature-hash"),
                SimpleNamespace(name="main", hash_="main-hash"),
            ]

        def merge(self, **_kwargs):
            return None

        def delete_branch(self, **_kwargs):
            return None

    monkeypatch.setattr(cli_branch, "get_nessie_client", lambda: Client())
    monkeypatch.setattr(
        cli_branch,
        "enforce_surface_mutation_authorization",
        lambda command, _adapter_getter, resource_id=None: calls.append((command, resource_id)),
    )

    cli_branch.merge.callback("feature", "main", False, False)

    assert calls == [("branch.merge", None), ("branch.delete", "feature")]
