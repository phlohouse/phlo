"""Tests for the Pandera authorization surface adapter.

Pandera is a passive CLI surface: it declares no operations, is always active,
and every pandera command is classified as read-only, so READ_COMMANDS must
cover all commands and mutations stay empty. Shared surface-adapter contract
checks come from ``phlo_testing.authorization_surface``.
"""

from __future__ import annotations

import pytest

from phlo_testing.authorization_surface import (
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
)
from phlo_pandera.authorization import (
    PanderaSurfaceAdapter,
    READ_COMMANDS,
    SURFACE_NAME,
)

pytestmark = pytest.mark.core_regression


class TestPanderaSurfaceAdapter:
    """Tests for PanderaSurfaceAdapter."""

    def test_surface_name(self):
        adapter = PanderaSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = PanderaSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations_returns_empty(self):
        adapter = PanderaSurfaceAdapter()
        assert adapter.list_operations() == []

    def test_is_active(self):
        adapter = PanderaSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = PanderaSurfaceAdapter.get_instance()
        adapter2 = PanderaSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allow_without_enforcement(self):
        """Read commands allow without touching the enforcement path."""
        with reset_surface_adapter_singleton(PanderaSurfaceAdapter):
            assert_read_commands_allow_without_enforcement(PanderaSurfaceAdapter())

    def test_unknown_command_denied_closed(self):
        with reset_surface_adapter_singleton(PanderaSurfaceAdapter):
            assert_unknown_command_denied(PanderaSurfaceAdapter(), "schema.unknown")

    def test_all_pandera_commands_covered(self):
        """All Pandera subcommands are classified."""
        all_pandera = {
            "schema.list",
            "schema.show",
            "schema.diff",
            "schema.validate",
            "schema.generate",
            "validate-schema",
            "validate-workflow",
        }
        assert READ_COMMANDS == all_pandera
