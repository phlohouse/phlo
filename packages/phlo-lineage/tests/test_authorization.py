"""Tests for lineage authorization module.

Package-specific command classification and mapping; the shared adapter
contract (read allow-through, unknown deny-closed, mutation policy allow/deny)
delegates to ``phlo_testing.authorization_surface``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from phlo_testing.authorization_surface import (
    assert_mutation_enforcement_allows_and_denies,
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
)
from phlo_lineage.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    LineageSurfaceAdapter,
    MUTATION_COMMANDS,
    READ_COMMANDS,
)

pytestmark = pytest.mark.core_regression


class TestLineageSurfaceAdapter:
    """Tests for LineageSurfaceAdapter."""

    def test_framework_type(self):
        adapter = LineageSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = LineageSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_read_commands_allowed(self):
        assert_read_commands_allow_without_enforcement(LineageSurfaceAdapter())

    def test_unknown_command_denied(self):
        assert_unknown_command_denied(LineageSurfaceAdapter(), "lineage.unknown")


class TestMutationCommandMaps:
    """Tests for command classification."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_lineage_commands_covered(self):
        """All lineage subcommands are classified."""
        all_lineage = {
            "lineage.show",
            "lineage.export",
            "lineage.impact",
            "lineage.status",
            "lineage.column.upstream",
            "lineage.column.downstream",
            "lineage.column.import-dbt",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        assert classified == all_lineage

    def test_mutation_commands_have_resource_mapping(self):
        """All mutation commands have resource mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for lineage mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        with reset_surface_adapter_singleton(LineageSurfaceAdapter):
            yield

    def test_import_dbt_mutation_honors_policy_decision(self):
        """lineage.column.import-dbt honors both policy outcomes through enforcement."""
        assert_mutation_enforcement_allows_and_denies(
            LineageSurfaceAdapter(), "lineage.column.import-dbt"
        )

    def test_read_commands_skip_enforcement(self):
        """Read commands do not go through enforcement."""
        from phlo.security.adapters import EnforcementResult

        adapter = LineageSurfaceAdapter()
        with patch("phlo.cli.authorization.enforce") as mock_enforce:
            mock_enforce.return_value = EnforcementResult.deny("policy_denied")
            for cmd in READ_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert result.allowed is True
            mock_enforce.assert_not_called()

            denied = adapter.check_command_authorization("lineage.column.import-dbt")
            assert denied.allowed is False
            assert denied.reason_code == "policy_denied"
            mock_enforce.assert_called_once()
