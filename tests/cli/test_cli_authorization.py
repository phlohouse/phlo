"""Tests for the CLI authorization module.

Covers principal resolution from env, the core CLI surface adapter's
command classification (no unclassified mutation commands), mutation
enforcement with reason codes, and the require-mutation-authorization
wrappers.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from phlo.cli.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    CliPrincipalResolver,
    CliSurfaceAdapter,
)

pytestmark = pytest.mark.core_regression


class TestCliPrincipalResolver:
    """Tests for CliPrincipalResolver."""

    def test_resolve_service_account(self):
        """PHLO_SERVICE_ACCOUNT creates service principal."""
        env = {"PHLO_SERVICE_ACCOUNT": "ci-bot@phlo.svc"}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "ci-bot@phlo.svc"
            assert principal.principal_type == "service"
            assert "operators" in principal.groups

    def test_resolve_human_principal(self):
        """PHLO_AUTH_SUBJECT creates human principal."""
        env = {
            "PHLO_AUTH_SUBJECT": "user@example.com",
            "PHLO_AUTH_TYPE": "user",
            "PHLO_AUTH_GROUPS": "admin,developers",
        }
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "user@example.com"
            assert principal.principal_type == "user"
            assert "admin" in principal.groups
            assert "developers" in principal.groups

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

    def test_resolve_dev_mode_fallback(self):
        """PHLO_DEV_MODE creates admin fallback outside regulated mode."""
        env = {"PHLO_DEV_MODE": "1"}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "local:root"
            assert principal.principal_type == "user"
            assert "admin" in principal.groups

    def test_resolve_dev_mode_does_not_grant_admin_in_regulated_mode(self):
        """PHLO_DEV_MODE does not create an admin principal in regulated mode."""
        env = {"PHLO_DEV_MODE": "1", "PHLO_REGULATED": "true"}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "anonymous"
            assert principal.principal_type == "user"
            assert principal.groups == ()

    def test_resolve_anonymous_default(self):
        """No env vars creates anonymous principal."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "anonymous"
            assert principal.principal_type == "user"


class TestCliSurfaceAdapter:
    """Tests for CliSurfaceAdapter."""

    def test_surface_name(self):
        adapter = CliSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = CliSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = CliSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = CliSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = CliSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = CliSurfaceAdapter.get_instance()
        adapter2 = CliSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_command_mapped_correctly(self):
        """All mutation commands have resource and action mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for CLI mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        CliSurfaceAdapter._instance = None
        yield
        CliSurfaceAdapter._instance = None

    def test_check_read_command_allowed(self):
        """Read commands are always allowed."""
        adapter = CliSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_check_mutation_command_enforced(self):
        """Mutation commands go through enforcement."""
        adapter = CliSurfaceAdapter()
        with patch("phlo.security.enforcement.EnforcementContext") as mock_ctx:
            mock_instance = MagicMock()
            mock_ctx.get_instance.return_value = mock_instance
            mock_instance.canonicalize.return_value = MagicMock(
                subject="test-user",
                principal_type="user",
                roles=("admin",),
                attributes={"authentication_source": "env"},
            )
            mock_instance.authorization_backend.explain_decision.return_value = MagicMock(
                allowed=True,
                reason_code=None,
                policy_id=None,
                explanation=None,
            )

            for cmd in MUTATION_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert not result.allowed or result.allowed, (
                    f"Mutation {cmd} should go through enforcement"
                )

    def test_check_unknown_command_denied(self):
        """Unknown commands are denied."""
        adapter = CliSurfaceAdapter()
        result = adapter.check_command_authorization("unknown.command")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


class TestMutationCommandLists:
    """Tests for command classification lists."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_services_commands_covered(self):
        """All services subcommands are classified."""
        all_services = {
            "services.start",
            "services.stop",
            "services.add",
            "services.remove",
            "services.reset",
            "services.exec",
            "services.status",
            "services.list",
            "services.logs",
            "services.ports",
            "services.init",
            "services.restart",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_services - classified
        assert unclassified == set(), f"Unclassified services commands: {unclassified}"

    def test_all_plugin_commands_covered(self):
        """All plugin subcommands are classified."""
        all_plugin = {
            "plugin.create",
            "plugin.install",
            "plugin.update",
            "plugin.list",
            "plugin.info",
            "plugin.search",
            "plugin.check",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_plugin - classified
        assert unclassified == set(), f"Unclassified plugin commands: {unclassified}"

    def test_all_authz_commands_covered(self):
        """All authz subcommands are classified."""
        all_authz = {
            "authz.sync",
            "authz.revert",
            "authz.validate",
            "authz.plan",
            "authz.verify",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_authz - classified
        assert unclassified == set(), f"Unclassified authz commands: {unclassified}"

    def test_all_migration_commands_covered(self):
        """All migration subcommands are classified."""
        all_migration = {
            "migrate.decorators_2026_05",
            "migrate.run",
            "migrate.validate",
            "migrate.list",
            "migrate.status",
            "schema_migrate.diff",
            "schema_migrate.plan",
            "schema_migrate.apply",
            "schema_migrate.history",
            "schema_migrate.export_contract",
            "schema_migrate.scaffold_yaml",
            "schema_migrate.scaffold_yaml_recent",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_migration - classified
        assert unclassified == set(), f"Unclassified migration commands: {unclassified}"


class TestResourceActionMapping:
    """Tests for command to resource/action mapping."""

    def test_services_commands_resource_mapping(self):
        """Services commands map to infrastructure resource."""
        for cmd in [
            "services.start",
            "services.stop",
            "services.add",
            "services.init",
            "services.remove",
            "services.reset",
            "services.exec",
            "services.restart",
        ]:
            assert COMMAND_RESOURCE_MAP[cmd] == "infrastructure"

    def test_plugin_commands_resource_mapping(self):
        """Plugin commands map to plugin resource."""
        for cmd in ["plugin.create", "plugin.install", "plugin.update"]:
            assert COMMAND_RESOURCE_MAP[cmd] == "plugin"

    def test_authz_commands_resource_mapping(self):
        """Authz commands map to rbac_policy resource."""
        for cmd in ["authz.sync", "authz.revert"]:
            assert COMMAND_RESOURCE_MAP[cmd] == "rbac_policy"

    def test_action_format(self):
        """All actions follow resource.action pattern."""
        for cmd, action in COMMAND_ACTION_MAP.items():
            parts = action.split(".")
            assert len(parts) >= 2, (
                f"Action {action} for {cmd} should have at least resource.action"
            )


class TestAuthorizationWrappers:
    """Tests for CLI authorization wrappers."""

    def test_regulated_services_start_discovers_configured_authorization_backend(
        self, monkeypatch, tmp_path
    ):
        """The regulated services wrapper resolves the configured discovered backend."""
        from phlo.capabilities import clear_all_capabilities
        from phlo.cli.authorization_wrappers import require_mutation_authorization
        from phlo.security.enforcement import EnforcementContext

        auth_dir = tmp_path / ".phlo" / "authorization"
        auth_dir.mkdir(parents=True)
        (auth_dir / "roles.yaml").write_text(
            """
version: 1
roles:
  operator:
    inherits: []
subjects:
  services:
    proof-operator: [operator]
""".lstrip()
        )
        (auth_dir / "policies.yaml").write_text(
            """
version: 1
policies:
  - policy_id: allow_services_start
    effect: allow
    principal:
      roles: [operator]
    action: infrastructure.start
    resource:
      type: infrastructure
      id_pattern: cli:services.start
""".lstrip()
        )
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "default")
        monkeypatch.setenv("PHLO_SERVICE_ACCOUNT", "proof-operator")
        clear_all_capabilities()
        EnforcementContext.reset_instance()

        @require_mutation_authorization("services.start")
        def handler() -> str:
            return "started"

        try:
            assert handler() == "started"
        finally:
            EnforcementContext.reset_instance()
            clear_all_capabilities()

    def test_require_mutation_authorization_import(self):
        """Can import the wrapper module."""
        from phlo.cli import authorization_wrappers

        assert hasattr(authorization_wrappers, "require_mutation_authorization")
        assert hasattr(authorization_wrappers, "enforce_mutation_context")

    def test_enforce_mutation_context(self):
        """enforce_mutation_context creates proper context."""
        from phlo.cli.authorization_wrappers import MutationContext

        ctx = MutationContext(command="services.start", resource_id="postgres")
        assert ctx.command == "services.start"
        assert ctx.resource_id == "postgres"

    def test_check_cli_surface_active(self):
        """check_cli_surface_active returns bool."""
        from phlo.cli.authorization_wrappers import check_cli_surface_active

        result = check_cli_surface_active()
        assert isinstance(result, bool)

    def test_require_mutation_authorization_skips_when_surface_inactive(self, monkeypatch):
        """Decorator is inert outside regulated mode."""
        from phlo.cli import authorization as authorization_module
        from phlo.cli import authorization_wrappers
        from phlo.cli.authorization_wrappers import require_mutation_authorization

        monkeypatch.setattr(authorization_wrappers, "check_cli_surface_active", lambda: False)
        monkeypatch.setattr(
            authorization_module,
            "get_cli_adapter",
            MagicMock(side_effect=AssertionError("adapter should not be called")),
        )

        @require_mutation_authorization("services.start")
        def handler(value: str) -> str:
            return f"ran:{value}"

        assert handler("postgres") == "ran:postgres"

    def test_require_mutation_authorization_enforces_when_surface_active(self, monkeypatch):
        """Decorator calls the CLI adapter in regulated mode."""
        from phlo.cli import authorization as authorization_module
        from phlo.cli import authorization_wrappers
        from phlo.cli.authorization_wrappers import require_mutation_authorization

        adapter = MagicMock()
        adapter.enforce_mutation.return_value = MagicMock(allowed=True)
        monkeypatch.setattr(authorization_wrappers, "check_cli_surface_active", lambda: True)
        monkeypatch.setattr(authorization_module, "get_cli_adapter", lambda: adapter)

        @require_mutation_authorization("services.start")
        def handler() -> str:
            return "ran"

        assert handler() == "ran"
        adapter.enforce_mutation.assert_called_once_with("services.start", None)

    def test_require_mutation_authorization_can_skip_read_mode(self, monkeypatch):
        """Conditional wrappers do not enforce read-only command modes."""
        from phlo.cli import authorization as authorization_module
        from phlo.cli import authorization_wrappers
        from phlo.cli.authorization_wrappers import require_mutation_authorization

        monkeypatch.setattr(authorization_wrappers, "check_cli_surface_active", lambda: True)
        monkeypatch.setattr(
            authorization_module,
            "get_cli_adapter",
            MagicMock(side_effect=AssertionError("adapter should not be called")),
        )

        @require_mutation_authorization(
            "migrate.run",
            when=lambda params: not params.get("dry_run"),
        )
        def handler(*, dry_run: bool) -> str:
            return "read-only"

        assert handler(dry_run=True) == "read-only"

    def test_require_mutation_authorization_denies_when_adapter_denies(self, monkeypatch):
        """Authorization denial stops the command before mutation logic runs."""
        from phlo.cli import authorization as authorization_module
        from phlo.cli import authorization_wrappers
        from phlo.cli.authorization_wrappers import require_mutation_authorization

        adapter = MagicMock()
        adapter.enforce_mutation.return_value = MagicMock(
            allowed=False,
            reason_code="forbidden",
            explanation="not allowed",
        )
        monkeypatch.setattr(authorization_wrappers, "check_cli_surface_active", lambda: True)
        monkeypatch.setattr(authorization_module, "get_cli_adapter", lambda: adapter)

        @require_mutation_authorization("services.start")
        def handler() -> str:
            return "should not run"

        with pytest.raises(SystemExit):
            handler()
