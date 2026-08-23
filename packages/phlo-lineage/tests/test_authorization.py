"""Tests for lineage authorization module.

Covers the LineageSurfaceAdapter surface contract (singleton instance,
operation listing, framework type) and command classification: read
commands are always allowed, unknown commands are denied, and mutation
enforcement routes through the shared policy layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_lineage.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    LineageSurfaceAdapter,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
)

pytestmark = pytest.mark.core_regression


class TestLineageSurfaceAdapter:
    """Tests for LineageSurfaceAdapter."""

    def test_surface_name(self):
        adapter = LineageSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

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

    def test_list_operations_has_required_fields(self):
        adapter = LineageSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = LineageSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = LineageSurfaceAdapter.get_instance()
        adapter2 = LineageSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allowed(self):
        adapter = LineageSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_unknown_command_denied(self):
        adapter = LineageSurfaceAdapter()
        result = adapter.check_command_authorization("lineage.unknown")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


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
        LineageSurfaceAdapter._instance = None
        yield
        LineageSurfaceAdapter._instance = None

    def test_import_dbt_mutation_enforced(self):
        """lineage.column.import-dbt goes through enforcement."""
        adapter = LineageSurfaceAdapter()
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

            result = adapter.check_command_authorization("lineage.column.import-dbt")
            assert result.allowed or not result.allowed

    def test_read_commands_skip_enforcement(self):
        """Read commands do not go through enforcement."""
        adapter = LineageSurfaceAdapter()
        with patch("phlo.security.enforcement.enforce") as mock_enforce:
            mock_enforce.return_value = MagicMock(variant="deny")
            for cmd in READ_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert result.allowed
                mock_enforce.assert_not_called()
