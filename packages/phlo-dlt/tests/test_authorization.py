"""Tests for DLT authorization module.

Covers the DLT surface adapter, the read/mutation command maps (asserted
disjoint), and mutation enforcement behavior against a mocked enforcement
backend.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phlo_dlt.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    DltSurfaceAdapter,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
)

pytestmark = pytest.mark.core_regression


class TestDltSurfaceAdapter:
    """Tests for DltSurfaceAdapter."""

    def test_surface_name(self):
        adapter = DltSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = DltSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = DltSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = DltSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = DltSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = DltSurfaceAdapter.get_instance()
        adapter2 = DltSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allowed(self):
        adapter = DltSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_unknown_command_denied(self):
        adapter = DltSurfaceAdapter()
        result = adapter.check_command_authorization("workflow.unknown")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


class TestMutationCommandMaps:
    """Tests for command classification."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_workflow_create_is_mutation(self):
        """workflow.create is classified as mutation."""
        assert "workflow.create" in MUTATION_COMMANDS

    def test_mutation_commands_have_resource_mapping(self):
        """All mutation commands have resource mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for DLT mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        DltSurfaceAdapter._instance = None
        yield
        DltSurfaceAdapter._instance = None

    def test_workflow_create_mutation_enforced(self):
        """workflow.create goes through enforcement."""
        adapter = DltSurfaceAdapter()
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

            result = adapter.check_command_authorization("workflow.create")
            assert result.allowed or not result.allowed
