"""Tests for ClickHouse authorization module.

Covers principal resolution from environment variables (service account,
human subject, dev-mode fallback) and command classification into read and
mutation sets with their resource/action mappings.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from phlo.cli.authorization import CliPrincipalResolver
from phlo_clickhouse.authorization import (
    COMMAND_ACTION_MAP,
    COMMAND_RESOURCE_MAP,
    MUTATION_COMMANDS,
    READ_COMMANDS,
    SURFACE_NAME,
    ClickHouseSurfaceAdapter,
)

pytestmark = pytest.mark.core_regression


class TestCliPrincipalResolver:
    """Tests for CliPrincipalResolver."""

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


class TestClickHouseSurfaceAdapter:
    """Tests for ClickHouseSurfaceAdapter."""

    def setup_method(self):
        ClickHouseSurfaceAdapter._instance = None

    def teardown_method(self):
        ClickHouseSurfaceAdapter._instance = None

    def test_surface_name(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_framework_type(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.framework_type == "cli"

    def test_list_operations(self):
        adapter = ClickHouseSurfaceAdapter()
        operations = adapter.list_operations()
        assert len(operations) == len(MUTATION_COMMANDS)
        operation_names = {op["operation_name"] for op in operations}
        for cmd in MUTATION_COMMANDS:
            assert cmd in operation_names

    def test_list_operations_has_required_fields(self):
        adapter = ClickHouseSurfaceAdapter()
        for op in adapter.list_operations():
            assert "action" in op
            assert "resource_type" in op
            assert "operation_name" in op

    def test_is_active(self):
        adapter = ClickHouseSurfaceAdapter()
        assert adapter.is_active(None) is True

    def test_get_instance_singleton(self):
        adapter1 = ClickHouseSurfaceAdapter.get_instance()
        adapter2 = ClickHouseSurfaceAdapter.get_instance()
        assert adapter1 is adapter2

    def test_command_mapped_correctly(self):
        """All mutation commands have resource and action mappings."""
        for cmd in MUTATION_COMMANDS:
            assert cmd in COMMAND_RESOURCE_MAP, f"Missing resource mapping for {cmd}"
            assert cmd in COMMAND_ACTION_MAP, f"Missing action mapping for {cmd}"


class TestEnforcement:
    """Tests for ClickHouse mutation enforcement."""

    def setup_method(self):
        ClickHouseSurfaceAdapter._instance = None

    def teardown_method(self):
        ClickHouseSurfaceAdapter._instance = None

    def test_check_read_command_allowed(self):
        """Read commands are always allowed."""
        adapter = ClickHouseSurfaceAdapter()
        for cmd in READ_COMMANDS:
            result = adapter.check_command_authorization(cmd)
            assert result.allowed, f"Read command {cmd} should be allowed"

    def test_check_unknown_command_denied(self):
        """Unknown commands are denied."""
        adapter = ClickHouseSurfaceAdapter()
        result = adapter.check_command_authorization("unknown.command")
        assert not result.allowed
        assert result.reason_code == "unknown_command"


class TestMutationCommandLists:
    """Tests for command classification lists."""

    def test_no_overlap_between_read_and_mutation(self):
        """Read and mutation command sets are disjoint."""
        overlap = READ_COMMANDS & MUTATION_COMMANDS
        assert overlap == set(), f"Commands in both sets: {overlap}"

    def test_all_clickhouse_commands_covered(self):
        """All clickhouse subcommands are classified."""
        all_clickhouse = {"clickhouse.query", "clickhouse.status"}
        classified = READ_COMMANDS | MUTATION_COMMANDS
        unclassified = all_clickhouse - classified
        assert unclassified == set(), f"Unclassified clickhouse commands: {unclassified}"


class TestResourceActionMapping:
    """Tests for command to resource/action mapping."""

    def test_query_command_resource_mapping(self):
        """clickhouse.query maps to dataset resource."""
        assert COMMAND_RESOURCE_MAP["clickhouse.query"] == "dataset"

    def test_query_command_action_mapping(self):
        """clickhouse.query maps to dataset.write action."""
        assert COMMAND_ACTION_MAP["clickhouse.query"] == "dataset.write"

    def test_action_format(self):
        """All actions follow resource.action pattern."""
        for cmd, action in COMMAND_ACTION_MAP.items():
            parts = action.split(".")
            assert len(parts) >= 2, (
                f"Action {action} for {cmd} should have at least resource.action"
            )
