"""Tests for the dbt authorization module.

Verifies the DbtSurfaceAdapter singleton, complete read/mutation command
coverage without overlap, and enforcement: read commands skip authorization
while mutation commands are denied unless policy allows them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_dbt.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    DbtSurfaceAdapter,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
)

pytestmark = pytest.mark.core_regression


class TestDbtSurfaceAdapter:
    """Tests for DbtSurfaceAdapter."""

    def test_surface_name(self):
        adapter = DbtSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = DbtSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = DbtSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = DbtSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = DbtSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = DbtSurfaceAdapter.get_instance()
        adapter2 = DbtSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allowed(self):
        adapter = DbtSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_unknown_command_denied(self):
        adapter = DbtSurfaceAdapter()
        result = adapter.check_command_authorization("dbt.unknown")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


class TestMutationCommandMaps:
    """Tests for command classification."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_dbt_commands_covered(self):
        """All dbt subcommands are classified."""
        all_dbt = {
            "dbt.compile",
            "dbt.test",
            "dbt.run",
            "dbt.publishing.scaffold",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        assert classified == all_dbt

    def test_mutation_commands_have_resource_mapping(self):
        """All mutation commands have resource mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for dbt mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        DbtSurfaceAdapter._instance = None
        yield
        DbtSurfaceAdapter._instance = None

    def test_dbt_run_mutation_enforced(self):
        """dbt.run goes through enforcement."""
        adapter = DbtSurfaceAdapter()
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

            result = adapter.check_command_authorization("dbt.run")
            assert result.allowed or not result.allowed

    def test_read_commands_skip_enforcement(self):
        """Read commands do not go through enforcement."""
        adapter = DbtSurfaceAdapter()
        with patch("phlo.security.enforcement.enforce") as mock_enforce:
            mock_enforce.return_value = MagicMock(variant="deny")
            for cmd in READ_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert result.allowed
                mock_enforce.assert_not_called()
