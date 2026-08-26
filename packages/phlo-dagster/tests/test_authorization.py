"""Tests for Dagster regulated surface adapter and authorization middleware.

Covers adapter metadata and operation listing, install-time surface
registration in the capability registry (None runtimes rejected), the
get_adapter() singleton, GraphQL-to-canonical-action mapping, and
service gating for the dagster-daemon regulated surface.
"""

import pytest

from phlo.capabilities import get_capability_registry
from phlo_dagster.authorization import (
    DagsterRegulatedSurfaceAdapter,
    get_adapter,
    SURFACE_NAME,
)


class TestDagsterRegulatedSurfaceAdapter:
    """Tests for the DagsterRegulatedSurfaceAdapter class."""

    def test_adapter_surface_name(self):
        """Verify adapter reports correct surface name."""
        adapter = DagsterRegulatedSurfaceAdapter()
        assert adapter.surface_name == SURFACE_NAME

    def test_adapter_framework_type(self):
        """Verify adapter reports correct framework type."""
        adapter = DagsterRegulatedSurfaceAdapter()
        assert adapter.framework_type == "dagster-graphql"

    def test_adapter_list_operations_returns_list(self):
        """Verify list_operations returns a list of SurfaceOperations."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        assert isinstance(operations, list)
        assert len(operations) > 0

    def test_adapter_list_operations_contains_required_actions(self):
        """Verify list_operations includes required canonical actions."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        actions = {op["action"] for op in operations}
        assert "asset.read" in actions
        assert "asset.execute" in actions
        assert "asset.manage" in actions
        assert "run.read" in actions
        assert "run.execute" in actions

    def test_adapter_is_active_false_without_install(self):
        """Verify is_active returns False when no runtime installed."""
        adapter = DagsterRegulatedSurfaceAdapter()
        assert adapter.is_active(None) is False
        assert adapter.is_active("some_runtime") is False

    def test_adapter_is_active_true_when_matching_runtime(self):
        """Verify is_active returns True when matching runtime is installed."""
        adapter = DagsterRegulatedSurfaceAdapter()
        runtime = object()
        adapter.install(runtime)
        assert adapter.is_active(runtime) is True

    def test_adapter_install_registers_surface(self):
        """Verify install registers the surface with capability registry."""
        adapter = DagsterRegulatedSurfaceAdapter()
        runtime = object()

        adapter.install(runtime)

        registered = get_capability_registry().list("regulated_surface")
        dagster_spec = next((s for s in registered if s.name == SURFACE_NAME), None)
        assert dagster_spec is not None
        assert dagster_spec.provider is adapter

    def test_adapter_install_requires_non_none_runtime(self):
        """Verify install raises ValueError when runtime is None."""
        adapter = DagsterRegulatedSurfaceAdapter()
        with pytest.raises(ValueError, match="non-None"):
            adapter.install(None)


class TestGetAdapterSingleton:
    """Tests for the get_adapter singleton accessor."""

    def test_get_adapter_returns_same_instance(self):
        """Verify get_adapter returns the same instance on repeated calls."""
        adapter1 = get_adapter()
        adapter2 = get_adapter()
        assert adapter1 is adapter2

    def test_get_adapter_returns_dagster_adapter(self):
        """Verify get_adapter returns a DagsterRegulatedSurfaceAdapter."""
        adapter = get_adapter()
        assert isinstance(adapter, DagsterRegulatedSurfaceAdapter)
        assert adapter.surface_name == SURFACE_NAME


class TestSurfaceOperationMapping:
    """Tests for GraphQL operation to canonical action mapping."""

    def test_operation_names_are_unique(self):
        """Verify all operation names in list_operations are unique."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        operation_names = [op["operation_name"] for op in operations]
        assert len(operation_names) == len(set(operation_names))

    def test_all_operations_have_resource_type(self):
        """Verify the operation table uses exactly the five regulated resources."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        assert {op["resource_type"] for op in operations} == {
            "asset",
            "run",
            "service",
            "catalog",
            "admin",
        }

    def test_all_operations_have_action(self):
        """Verify the operation table uses exactly the eleven canonical actions."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        assert {op["action"] for op in operations} == {
            "asset.read",
            "asset.execute",
            "asset.manage",
            "run.read",
            "run.execute",
            "run.manage",
            "service.read",
            "catalog.read",
            "catalog.manage",
            "admin.read",
            "admin.manage",
        }

    def test_graphql_operations_in_framework_metadata(self):
        """Verify mapped operations include GraphQL operation names in framework_metadata."""
        adapter = DagsterRegulatedSurfaceAdapter()
        operations = adapter.list_operations()
        mapped_ops = [
            op
            for op in operations
            if op["operation_name"].startswith("dagster.")
            and not op["operation_name"].endswith((".query", ".mutate", ".launch", ".manage"))
        ]
        assert len(mapped_ops) > 0
        for op in mapped_ops:
            assert "framework_metadata" in op
            assert "graphql_operations" in op["framework_metadata"]


class TestGatingIntegration:
    """Tests verifying dagster is properly integrated with gating."""

    def test_dagster_is_not_blocked_in_regulated_mode(self, monkeypatch):
        """Verify dagster-webserver is allowed in regulated mode."""
        from phlo.security.gating import is_service_allowed

        monkeypatch.setenv("PHLO_REGULATED", "true")

        assert is_service_allowed("dagster-webserver", regulated=True) is True

    def test_dagster_daemon_is_not_blocked_in_regulated_mode(self, monkeypatch):
        """Verify dagster-daemon is allowed in regulated mode."""
        from phlo.security.gating import is_service_allowed

        monkeypatch.setenv("PHLO_REGULATED", "true")

        assert is_service_allowed("dagster-daemon", regulated=True) is True
