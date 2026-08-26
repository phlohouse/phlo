"""Tests for the phlo-alerting CLI authorization surface adapter.

Package-specific adapter basics plus the shared surface-adapter contract from
``phlo_testing.authorization_surface``: reads allow without enforcement and
unknown commands deny closed.
"""

from __future__ import annotations

import pytest

from phlo_testing.authorization_surface import (
    assert_read_commands_allow_without_enforcement,
    assert_unknown_command_denied,
    reset_surface_adapter_singleton,
)
from phlo_alerting.authorization import (
    AlertingSurfaceAdapter,
    READ_COMMANDS,
    SURFACE_NAME,
)

pytestmark = pytest.mark.core_regression


class TestAlertingSurfaceAdapter:
    """Tests for AlertingSurfaceAdapter."""

    def test_surface_name(self):
        adapter = AlertingSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = AlertingSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations_returns_empty(self):
        adapter = AlertingSurfaceAdapter()
        assert adapter.list_operations() == []

    def test_is_active(self):
        adapter = AlertingSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = AlertingSurfaceAdapter.get_instance()
        adapter2 = AlertingSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_read_commands_allow_without_enforcement(self):
        """Read commands allow without touching the enforcement path."""
        with reset_surface_adapter_singleton(AlertingSurfaceAdapter):
            assert_read_commands_allow_without_enforcement(AlertingSurfaceAdapter())

    def test_unknown_command_denied_closed(self):
        with reset_surface_adapter_singleton(AlertingSurfaceAdapter):
            assert_unknown_command_denied(AlertingSurfaceAdapter(), "alerts.unknown")

    def test_all_alerting_commands_covered(self):
        """All alerting subcommands are classified."""
        all_alerting = {
            "alerts.test",
            "alerts.list",
            "alerts.status",
        }
        assert READ_COMMANDS == all_alerting
