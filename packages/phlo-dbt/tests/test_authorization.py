"""Tests for the dbt authorization module.

Package-specific command-table coverage plus the shared surface-adapter
contract from ``phlo_testing.authorization_surface``: reads skip enforcement,
unknown commands deny closed, and mutations honor policy allow/deny.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
)
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
            assert "operation_name" in op
            assert "action" in op
            assert "resource_type" in op

    def test_is_active(self):
        adapter = DbtSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = DbtSurfaceAdapter.get_instance()
        adapter2 = DbtSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allow_without_enforcement(self):
        """Read commands allow without touching the enforcement path."""
        with reset_surface_adapter_singleton(DbtSurfaceAdapter):
            assert_read_commands_allow_without_enforcement(DbtSurfaceAdapter())

    def test_unknown_command_denied_closed(self):
        with reset_surface_adapter_singleton(DbtSurfaceAdapter):
            assert_unknown_command_denied(DbtSurfaceAdapter(), "dbt.unknown")


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

    def test_mutation_commands_have_action_mapping(self):
        """All mutation commands have action mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for dbt mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        with reset_surface_adapter_singleton(DbtSurfaceAdapter):
            yield

    def test_dbt_run_mutation_honors_policy_decision(self):
        """dbt.run flows a policy allow through and blocks a policy deny."""
        assert_mutation_enforcement_allows_and_denies(DbtSurfaceAdapter(), "dbt.run")

    def test_read_commands_skip_enforcement(self):
        """Read commands do not go through enforcement."""
        adapter = DbtSurfaceAdapter()
        with patch("phlo.security.enforcement.enforce") as mock_enforce:
            mock_enforce.return_value = MagicMock(variant="deny")
            for cmd in READ_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert result.allowed
                mock_enforce.assert_not_called()
