"""Tests for OpenMetadata authorization module.

Package-specific command classification and mapping; the shared adapter
contract (read allow-through, unknown deny-closed, mutation policy allow/deny)
delegates to ``phlo_testing.authorization_surface``.
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
from phlo_openmetadata.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    OpenMetadataSurfaceAdapter,
)

pytestmark = pytest.mark.core_regression


class TestOpenMetadataSurfaceAdapter:
    """Tests for OpenMetadataSurfaceAdapter."""

    def test_framework_type(self):
        adapter = OpenMetadataSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = OpenMetadataSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_read_commands_allowed(self):
        assert_read_commands_allow_without_enforcement(OpenMetadataSurfaceAdapter())

    def test_unknown_command_denied(self):
        assert_unknown_command_denied(OpenMetadataSurfaceAdapter(), "openmetadata.unknown")


class TestMutationCommandMaps:
    """Tests for command classification."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_openmetadata_commands_covered(self):
        """All OpenMetadata subcommands are classified."""
        all_openmetadata = {
            "openmetadata.health",
            "openmetadata.sync",
        }
        classified = READ_COMMANDS | MUTATION_COMMANDS
        assert classified == all_openmetadata

    def test_mutation_commands_have_resource_mapping(self):
        """All mutation commands have resource mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for OpenMetadata mutation enforcement."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        with reset_surface_adapter_singleton(OpenMetadataSurfaceAdapter):
            yield

    def test_openmetadata_sync_mutation_honors_policy_decision(self):
        """openmetadata.sync honors both policy outcomes through enforcement."""
        assert_mutation_enforcement_allows_and_denies(
            OpenMetadataSurfaceAdapter(), "openmetadata.sync"
        )

    def test_read_commands_skip_enforcement(self):
        """Read commands do not go through enforcement."""
        adapter = OpenMetadataSurfaceAdapter()
        with patch("phlo.cli.authorization.enforce") as mock_enforce:
            mock_enforce.return_value = MagicMock(variant="deny")
            for cmd in READ_COMMANDS:
                result = adapter.check_command_authorization(cmd)
                assert result.allowed
            mock_enforce.assert_not_called()
            # Control: the same patch intercepts a mutation command.
            adapter.enforce_mutation("openmetadata.sync")
            assert mock_enforce.call_count == 1
