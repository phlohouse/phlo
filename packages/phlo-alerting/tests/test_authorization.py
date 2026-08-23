"""Tests for the phlo-alerting CLI authorization surface adapter.

Verifies mutation-command classification and principal resolution against the
shared CLI authorization contract.
"""

from __future__ import annotations

import pytest

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

    def test_read_commands_allowed(self):
        adapter = AlertingSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_unknown_command_denied(self):
        adapter = AlertingSurfaceAdapter()
        result = adapter.check_command_authorization("alerts.unknown")
        assert not result.allowed
        assert result.reason_code == "unknown_command"

    def test_all_alerting_commands_covered(self):
        """All alerting subcommands are classified."""
        all_alerting = {
            "alerts.test",
            "alerts.list",
            "alerts.status",
        }
        assert READ_COMMANDS == all_alerting
