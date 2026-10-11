"""Integration tests for regulated surface discovery and enforcement.

Tests the full pipeline:
    - phlo-api adapter registers on startup
    - validation discovers surfaces via capability registry
    - enforcement works with EnforcementContext
    - canonical audit events are emitted per enforce() call
    - module-level globals are gone from phlo-api authorization path
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

import phlo.application.discovery as discovery
from phlo.capabilities import (
    RegulatedSurfaceSpec,
    clear_capabilities,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.interfaces import (
    AuthPrincipal,
    Principal,
    ResourceRef,
)
from phlo.capabilities.registry import CAPABILITY_FAMILIES
from phlo.identity.bridge import create_regulated_bridge
from phlo.security.adapters import SurfaceOperation
from phlo.security.enforcement import EnforcementContext
from phlo.security.validation import (
    run_regulated_validation,
)

pytestmark = pytest.mark.integration


class MockRegulatedSurfaceAdapter:
    """Mock adapter for testing surface registration."""

    surface_name: str = "mock-surface"
    framework_type: str = "test"

    def list_operations(self) -> list[SurfaceOperation]:
        return [
            SurfaceOperation(
                action="dataset.read",
                resource_type="dataset",
                operation_name="dataset.read",
            ),
        ]

    def is_active(self) -> bool:
        return True

    def install(self) -> None:
        pass


@contextmanager
def isolated_authorization_policy_backend() -> Iterator[None]:
    """Temporarily remove only the authorization backend capability family."""
    registry = get_capability_registry()
    previous_specs = registry.list("authorization_policy_backend")
    clear_capabilities("authorization_policy_backend")
    EnforcementContext.reset_instance()

    isolated_context = EnforcementContext()
    isolated_context._identity_bridge = create_regulated_bridge()
    EnforcementContext._instance = isolated_context

    try:
        with patch.object(discovery, "discover_capabilities", lambda: None):
            yield
    finally:
        clear_capabilities("authorization_policy_backend")
        EnforcementContext.reset_instance()
        for spec in previous_specs:
            register_capability("authorization_policy_backend", spec)
        EnforcementContext.reset_instance()


class TestRegulatedSurfaceDiscovery:
    """Test surface discovery via capability registry."""

    def test_phlo_api_adapter_registered(self) -> None:
        """phlo-api adapter should be discoverable via capability registry."""
        from phlo_api.regulated_surface_adapter import get_adapter

        adapter = get_adapter()
        assert adapter.surface_name == "phlo-api"
        assert adapter.framework_type == "fastapi"

    def test_register_and_list_regulated_surfaces(self) -> None:
        """Surfaces can register and be listed via capability registry."""
        spec = RegulatedSurfaceSpec(
            name="test-surface",
            provider=MockRegulatedSurfaceAdapter(),
            metadata={},
        )
        register_capability("regulated_surface", spec)

        surfaces = get_capability_registry().list("regulated_surface")
        assert len(surfaces) >= 1
        assert any(s.name == "test-surface" for s in surfaces)

    def test_mock_surface_can_register_without_core_changes(self) -> None:
        """A mock surface adapter can register without any core code changes."""
        spec = RegulatedSurfaceSpec(
            name="integration-test-surface",
            provider=MockRegulatedSurfaceAdapter(),
            metadata={"test": True},
        )
        register_capability("regulated_surface", spec)

        surfaces = get_capability_registry().list("regulated_surface")
        test_surface = next((s for s in surfaces if s.name == "integration-test-surface"), None)
        assert test_surface is not None
        assert test_surface.metadata.get("test") is True


class TestEnforcementContext:
    """Test EnforcementContext singleton behavior."""

    def test_singleton_returns_same_instance(self) -> None:
        """get_instance() returns the same instance on repeated calls."""
        EnforcementContext.reset_instance()
        EnforcementContext._instance = None

        ctx1 = EnforcementContext.get_instance()
        ctx2 = EnforcementContext.get_instance()
        assert ctx1 is ctx2

    def test_singleton_thread_safety(self) -> None:
        """Multiple threads calling get_instance() concurrently get same instance."""
        import threading

        EnforcementContext.reset_instance()
        EnforcementContext._instance = None

        results: list[EnforcementContext] = []
        errors: list[Exception] = []

        def get_ctx() -> None:
            try:
                results.append(EnforcementContext.get_instance())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_ctx) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len({id(r) for r in results}) == 1


class TestEnforceFunction:
    """Test the core enforce() function."""

    def test_enforce_rejects_injected_canonical_principal(self) -> None:
        """An externally constructed Principal cannot bypass group canonicalization."""
        context = EnforcementContext()
        canonical = Principal(
            subject="test-user",
            principal_type="user",
            roles=("viewer",),
        )
        bridge = MagicMock()
        context._identity_bridge = bridge

        with pytest.raises(TypeError, match="AuthPrincipal"):
            context.canonicalize(canonical)
        bridge.canonicalize.assert_not_called()

    def test_enforce_returns_error_when_no_backend(self) -> None:
        """Discovery and no-backend enforcement remain isolated and reversible."""
        EnforcementContext.reset_instance()

        from phlo.application.discovery import discover_capabilities
        from phlo.security.enforcement import enforce

        auth_principal = AuthPrincipal(
            subject="test-user",
            principal_type="user",
            groups=("developers",),
            attributes={},
        )
        resource = ResourceRef(resource_type="dataset", resource_id="test-ds")

        clear_capabilities("authorization_policy_backend")
        discover_capabilities()

        normal_result = enforce(
            principal=auth_principal,
            action="dataset.read",
            resource=resource,
            surface="test",
        )
        assert normal_result.variant == "deny"
        assert normal_result.reason_code == "default_deny"
        registry = get_capability_registry()
        discovered_specs = registry.list("authorization_policy_backend")
        assert [spec.name for spec in discovered_specs] == ["default"]
        other_family_specs = {
            family: registry.list(family)
            for family in CAPABILITY_FAMILIES
            if family != "authorization_policy_backend"
        }

        with isolated_authorization_policy_backend():
            assert registry.list("authorization_policy_backend") == []
            assert {
                family: registry.list(family)
                for family in CAPABILITY_FAMILIES
                if family != "authorization_policy_backend"
            } == other_family_specs
            isolated_result = enforce(
                principal=auth_principal,
                action="dataset.read",
                resource=resource,
                surface="test",
            )
            assert isolated_result.variant == "error"
            assert isolated_result.reason_code == "backend_unavailable"

        restored_specs = registry.list("authorization_policy_backend")
        assert restored_specs == discovered_specs
        assert {
            family: registry.list(family)
            for family in CAPABILITY_FAMILIES
            if family != "authorization_policy_backend"
        } == other_family_specs
        restored_result = enforce(
            principal=auth_principal,
            action="dataset.read",
            resource=resource,
            surface="test",
        )
        assert restored_result.variant == "deny"
        assert restored_result.reason_code == "default_deny"


class TestValidation:
    """Test startup validation."""

    def test_validation_report_structure(self) -> None:
        """Validation report has correct structure."""
        report = run_regulated_validation()
        assert hasattr(report, "regulated_enabled")
        assert hasattr(report, "passed")
        assert hasattr(report, "checks")
        assert hasattr(report, "errors")

    def test_validation_identifies_not_yet_integrated(self) -> None:
        """Validation report includes registered surfaces check (informational).

        In v1, only phlo-api is required. Dagster and CLI are not checked
        by regulated validation — they are outside v1 scope.
        """
        report = run_regulated_validation(config_regulated=True)
        registered_check = next(
            (c for c in report.checks if c.name == "regulated_surfaces_registered"), None
        )
        assert registered_check is not None
        assert registered_check.passed is True

    def test_validation_reads_regulated_from_phlo_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validation should honor root-level regulated from phlo.yaml."""
        config_path = tmp_path / "phlo.yaml"
        config_path.write_text(yaml.safe_dump({"regulated": True}))

        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        monkeypatch.delenv("PHLO_REGULATED", raising=False)
        monkeypatch.delenv("PHLO_REGULATED_MODE", raising=False)

        report = run_regulated_validation()

        assert report.regulated_enabled is True

    def test_validation_reads_regulated_mode_fallback_from_phlo_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validation should fallback to regulated_mode from phlo.yaml."""
        config_path = tmp_path / "phlo.yaml"
        config_path.write_text(yaml.safe_dump({"regulated_mode": True}))

        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        monkeypatch.delenv("PHLO_REGULATED", raising=False)
        monkeypatch.delenv("PHLO_REGULATED_MODE", raising=False)

        with pytest.warns(DeprecationWarning):
            report = run_regulated_validation()

        assert report.regulated_enabled is True


class TestModuleLevelGlobalsGone:
    """Test that module-level globals are removed from phlo-api authorization."""

    def test_phlo_api_no_cached_regulated_mode_global(self) -> None:
        """phlo-api authorization module should not have _regulated_mode global."""
        from phlo_api.api import authorization

        assert (
            not hasattr(authorization, "_regulated_mode") or authorization._regulated_mode is None
        )

    def test_phlo_api_no_cached_identity_bridge_global(self) -> None:
        """phlo-api authorization module should not have _identity_bridge global."""
        from phlo_api.api import authorization

        assert (
            not hasattr(authorization, "_identity_bridge") or authorization._identity_bridge is None
        )

    def test_phlo_api_no_cached_audit_emitter_global(self) -> None:
        """phlo-api authorization module should not have _audit_emitter global."""
        from phlo_api.api import authorization

        assert not hasattr(authorization, "_audit_emitter") or authorization._audit_emitter is None


class TestCanonicalAuditEvents:
    """Test that canonical audit events are emitted per enforcement call."""

    def test_audit_event_emitted_per_enforce_call(self) -> None:
        """Each enforce() call emits exactly one audit event."""
        EnforcementContext.reset_instance()
        EnforcementContext._instance = None

        mock_emitter = MagicMock()
        mock_emitter.emit_authorization = MagicMock()

        with patch.object(EnforcementContext, "audit_emitter", mock_emitter):
            mock_backend = MagicMock()
            mock_backend.explain_decision = MagicMock(
                return_value=MagicMock(
                    allowed=True, reason_code=None, policy_id=None, explanation=None
                )
            )

            mock_bridge = MagicMock()
            mock_bridge.canonicalize = MagicMock(
                return_value=Principal(
                    subject="test-user",
                    principal_type="user",
                    roles=("developer",),
                    attributes={"authentication_source": "test"},
                )
            )

            with (
                patch.object(EnforcementContext, "authorization_backend", mock_backend),
                patch.object(EnforcementContext, "identity_bridge", mock_bridge),
            ):
                from phlo.security.enforcement import enforce

                auth_principal = AuthPrincipal(
                    subject="test-user",
                    principal_type="user",
                    groups=("developers",),
                    attributes={},
                )
                resource = ResourceRef(resource_type="dataset", resource_id="test-ds")

                enforce(
                    principal=auth_principal,
                    action="dataset.read",
                    resource=resource,
                    surface="test-surface",
                )

                assert mock_emitter.emit_authorization.call_count == 1


class TestPhloAPIIntegration:
    """Regression tests for migrated phlo-api regulated enforcement."""

    def test_regulated_phlo_api_passes_auth_principal_to_core_enforce(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """phlo-api should hand the raw auth principal to core enforce()."""
        from phlo_api.api import authorization

        auth_principal = AuthPrincipal(
            subject="test-user",
            principal_type="user",
            groups=("developers",),
            attributes={},
        )
        request = SimpleNamespace(
            state=SimpleNamespace(request_id="req-123"),
            client=SimpleNamespace(host="127.0.0.1"),
            method="GET",
            url=SimpleNamespace(path="/api/datasets/raw.orders"),
        )
        captured: dict[str, object] = {}

        monkeypatch.setattr(authorization, "get_request_principal", lambda _request: auth_principal)

        def fake_enforce(**kwargs):
            captured.update(kwargs)
            from phlo.security.adapters import EnforcementResult

            return EnforcementResult.allow()

        monkeypatch.setattr(authorization, "enforce", fake_enforce)

        authorization._enforce_or_raise(
            request,
            "dataset.read",
            ResourceRef(resource_type="dataset", resource_id="raw.orders"),
        )

        assert captured["principal"] is auth_principal
        assert captured["request_id"] == "req-123"

    @pytest.mark.anyio
    async def test_request_logging_middleware_persists_request_id(self) -> None:
        """Request middleware should persist request_id for audit/context consumers."""
        from phlo_api.main import bind_request_logging_context

        request = SimpleNamespace(
            headers={"x-request-id": "req-456"},
            url=SimpleNamespace(path="/api/maintenance/status"),
            method="GET",
            state=SimpleNamespace(),
        )

        async def call_next(_request):
            assert _request.state.request_id == "req-456"
            return SimpleNamespace(headers={})

        response = await bind_request_logging_context(request, call_next)

        assert request.state.request_id == "req-456"
        assert response.headers["x-request-id"] == "req-456"


class TestSurfaceOperation:
    """Test SurfaceOperation type."""

    def test_surface_operation_dict_keys(self) -> None:
        """SurfaceOperation contains expected dict keys."""
        op = SurfaceOperation(
            action="dataset.read",
            resource_type="dataset",
            operation_name="dataset.read",
            resource_id_strategy=None,
            framework_metadata={},
        )
        assert op["action"] == "dataset.read"
        assert op["resource_type"] == "dataset"
        assert op["operation_name"] == "dataset.read"
        assert op["resource_id_strategy"] is None
        assert op["framework_metadata"] == {}

    def test_surface_operation_with_optional_fields(self) -> None:
        """SurfaceOperation with optional fields set."""
        op = SurfaceOperation(
            action="dataset.write",
            resource_type="dataset",
            operation_name="dataset.write",
            resource_id_strategy="path_param",
            framework_metadata={"route": "/datasets/{id}"},
        )
        assert op["resource_id_strategy"] == "path_param"
        assert op["framework_metadata"]["route"] == "/datasets/{id}"


class TestPhloAPIAdapterOperations:
    """Test phlo-api adapter operation declarations."""

    def test_phlo_api_adapter_declares_all_operations(self) -> None:
        """phlo-api adapter declares every manifest route exactly once."""
        from phlo_api.regulated_surface_adapter import get_adapter
        from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST

        adapter = get_adapter()
        ops = adapter.list_operations()

        assert len({op["operation_name"] for op in ops}) == len(HTTP_ROUTE_MANIFEST)
        assert {op["operation_name"] for op in ops} == {
            f"http.{name}" for name in HTTP_ROUTE_MANIFEST
        }
        actions = {op["action"] for op in ops}
        assert "dataset.read" in actions
        assert "asset.read" in actions
        assert "asset.execute" in actions
        assert "service.read" in actions
        assert "service.manage" in actions
        assert "admin.read" in actions
        assert "admin.manage" in actions
        assert "settings.read" in actions
        assert "settings.manage" in actions
        assert "catalog.read" in actions
        assert "catalog.manage" in actions
        assert "platform_metadata.read" in actions
        assert "observability.read" in actions
        assert "maintenance.read" in actions
        assert "run.read" in actions
        assert "run.manage" in actions
        assert "audit.read" in actions
        assert "dataset.query" in actions

    def test_phlo_api_adapter_is_active_requires_exact_runtime(self) -> None:
        """phlo-api adapter is_active(runtime) only True when runtime matches installed app."""
        from phlo_api.regulated_surface_adapter import PhloAPIRegulatedSurfaceAdapter

        adapter = PhloAPIRegulatedSurfaceAdapter()
        real_app = object()
        other_app = object()

        assert adapter.is_active(real_app) is False
        assert adapter.is_active(other_app) is False
        assert adapter.is_active(None) is False

        adapter._installed_runtime = real_app

        assert adapter.is_active(real_app) is True
        assert adapter.is_active(other_app) is False
        assert adapter.is_active(None) is False
