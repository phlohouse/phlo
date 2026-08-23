"""Tests for ClickStack authorization module.

Covers the shared CLI principal resolver (service account, human subject,
dev-mode fallback) plus command classification into read and mutation sets
with their resource/action mappings.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from phlo.cli.authorization import CliPrincipalResolver
from phlo_clickstack.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    ClickStackSurfaceAdapter,
)

pytestmark = pytest.mark.core_regression


class TestClickStackPrincipalResolver:
    """Tests for the shared CLI principal resolver."""

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
        """PHLO_DEV_MODE creates admin fallback with warning."""
        env = {"PHLO_DEV_MODE": "1"}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "local:root"
            assert principal.principal_type == "user"
            assert "admin" in principal.groups

    def test_resolve_anonymous_default(self):
        """No env vars creates anonymous principal."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            resolver = CliPrincipalResolver()
            principal = resolver.resolve()
            assert principal.subject == "anonymous"
            assert principal.principal_type == "user"


class TestClickStackSurfaceAdapter:
    """Tests for ClickStackSurfaceAdapter."""

    def setup_method(self):
        ClickStackSurfaceAdapter._instance = None

    def teardown_method(self):
        ClickStackSurfaceAdapter._instance = None

    def test_surface_name(self):
        adapter = ClickStackSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = ClickStackSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = ClickStackSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = ClickStackSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = ClickStackSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = ClickStackSurfaceAdapter.get_instance()
        adapter2 = ClickStackSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_command_mapped_correctly(self):
        """All mutation commands have resource and action mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for ClickStack mutation enforcement."""

    def setup_method(self):
        ClickStackSurfaceAdapter._instance = None

    def teardown_method(self):
        ClickStackSurfaceAdapter._instance = None

    def test_check_read_command_allowed(self):
        """Read commands are always allowed."""
        adapter = ClickStackSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_check_unknown_command_denied(self):
        """Unknown commands are denied."""
        adapter = ClickStackSurfaceAdapter()
        result = adapter.check_command_authorization("unknown.command")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


class TestMutationCommandLists:
    """Tests for command classification lists."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_clickstack_commands_covered(self):
        """All clickstack subcommands are classified."""
        all_clickstack = {"clickstack.query"}
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_clickstack - classified
        assert unclassified == set(), f"Unclassified clickstack commands: {unclassified}"


class TestResourceActionMapping:
    """Tests for command to resource/action mapping."""

    def test_query_command_resource_mapping(self):
        """clickstack.query maps to dataset resource."""
        assert COMMAND_RESOURCE_MAP["clickstack.query"] == "dataset"

    def test_query_command_action_mapping(self):
        """clickstack.query maps to dataset.write action."""
        assert COMMAND_ACTION_MAP["clickstack.query"] == "dataset.write"

    def test_action_format(self):
        """All actions follow resource.action pattern."""
        for cmd, action in COMMAND_ACTION_MAP.items():
            parts = action.split(".")
            assert len(parts) >= 2, (
                f"Action {action} for {cmd} should have at least resource.action"
            )
