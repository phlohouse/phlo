"""Tests for phlo-api authorization helpers.

Covers request principal resolution (forwarded identity headers are ignored),
backend selection when multiple backends register, authorization mode
resolution (env over phlo.yaml, service-specific config over top-level), and
fail-closed enforcement in required and regulated modes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException, Request
from phlo.capabilities import AuthorizationPolicyBackendSpec, clear_all_capabilities
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.capabilities.interfaces import AuthPrincipal, Principal, ResourceRef
from phlo.capabilities.registry import register_capability
from phlo.infrastructure.config import clear_config_cache
from phlo.security.adapters import EnforcementResult

from phlo_api.api.authorization import (
    check_admin_read,
    get_authorization_backend,
    get_authorization_mode,
    resolve_request_principal,
)


def teardown_function() -> None:
    clear_all_capabilities()
    clear_config_cache()


def _make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": headers or [],
        }
    )


def _write_phlo_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "phlo.yaml").write_text(content)


def test_resolve_request_principal_ignores_forwarded_identity_headers() -> None:
    request = _make_request(
        [
            (b"x-forwarded-user", b"admin"),
            (b"x-forwarded-roles", b"admin,operator"),
        ]
    )

    principal = resolve_request_principal(request)

    assert principal == Principal(
        subject="anonymous",
        principal_type="user",
        roles=(),
    )


def test_get_authorization_backend_requires_explicit_selection_for_multiple_backends(
    monkeypatch,
) -> None:
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="default",
            provider=DefaultAuthorizationPolicyBackend(
                policies=[
                    {
                        "policy_id": "allow-default",
                        "effect": "allow",
                        "action": "*",
                        "resource": {"type": "*", "id_pattern": "*"},
                    }
                ]
            ),
        ),
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="opa",
            provider=DefaultAuthorizationPolicyBackend(
                policies=[
                    {
                        "policy_id": "allow-opa",
                        "effect": "allow",
                        "action": "*",
                        "resource": {"type": "*", "id_pattern": "*"},
                    }
                ]
            ),
        ),
    )

    monkeypatch.delenv("PHLO_AUTHORIZATION_BACKEND", raising=False)

    try:
        get_authorization_backend()
    except RuntimeError as exc:
        assert "Multiple authorization backends are registered" in str(exc)
    else:
        raise AssertionError("Expected explicit backend selection error")


def test_get_authorization_backend_resolves_named_backend(monkeypatch) -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "allow-opa",
                "effect": "allow",
                "action": "*",
                "resource": {"type": "*", "id_pattern": "*"},
            }
        ]
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="default", provider=DefaultAuthorizationPolicyBackend()
        ),
    )
    register_capability(
        "authorization_policy_backend", AuthorizationPolicyBackendSpec(name="opa", provider=backend)
    )

    monkeypatch.setenv("PHLO_AUTHORIZATION_BACKEND", "opa")

    assert get_authorization_backend() is backend


def test_get_authorization_mode_defaults_to_optional_in_development(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_AUTHORIZATION_MODE", raising=False)
    monkeypatch.delenv("PHLO_ENVIRONMENT", raising=False)

    assert get_authorization_mode() == "optional"


def test_get_authorization_mode_defaults_to_required_in_production(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_AUTHORIZATION_MODE", raising=False)
    monkeypatch.setenv("PHLO_ENVIRONMENT", "production")

    assert get_authorization_mode() == "required"


def test_get_authorization_mode_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "fail-closed")

    try:
        get_authorization_mode()
    except RuntimeError as exc:
        assert "Invalid PHLO_AUTHORIZATION_MODE value" in str(exc)
    else:
        raise AssertionError("Expected invalid authorization mode error")


def test_route_guard_allows_access_without_backend_in_optional_mode(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_AUTHORIZATION_BACKEND", raising=False)
    monkeypatch.delenv("PHLO_AUTHORIZATION_MODE", raising=False)

    check_admin_read(_make_request(), "observatory_settings")


def test_route_guard_fails_closed_without_backend_in_required_mode(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_AUTHORIZATION_BACKEND", raising=False)
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "required")

    try:
        check_admin_read(_make_request(), "observatory_settings")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == {
            "error": "service_unavailable",
            "reason": "authorization_backend_not_configured",
        }
    else:
        raise AssertionError("Expected route guard to fail closed")


def test_enforce_or_raise_returns_503_for_regulated_enforcement_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_api.api.authorization.get_request_principal",
        lambda _request: AuthPrincipal(subject="user-1", principal_type="user"),
    )
    monkeypatch.setattr(
        "phlo_api.api.authorization.enforce",
        lambda **_kwargs: EnforcementResult.error("backend_unavailable"),
    )

    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(request_id="req-123"),
        client=SimpleNamespace(host="127.0.0.1"),
        method="GET",
        url=SimpleNamespace(path="/api/datasets/raw.orders"),
    )

    try:
        from phlo_api.api.authorization import _enforce_or_raise

        _enforce_or_raise(
            request,
            "dataset.read",
            ResourceRef(resource_type="dataset", resource_id="raw.orders"),
        )
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == {
            "error": "service_unavailable",
            "reason": "backend_unavailable",
        }
    else:
        raise AssertionError("Expected regulated enforcement error to surface as 503")


def test_get_authorization_mode_uses_top_level_phlo_yaml(monkeypatch, tmp_path: Path) -> None:
    _write_phlo_config(
        tmp_path,
        """
api:
  authorization:
    mode: required
""".lstrip(),
    )
    monkeypatch.delenv("PHLO_AUTHORIZATION_MODE", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authorization_mode() == "required"


def test_get_authorization_mode_prefers_service_specific_phlo_yaml(
    monkeypatch, tmp_path: Path
) -> None:
    _write_phlo_config(
        tmp_path,
        """
api:
  authorization:
    mode: optional
services:
  phlo-api:
    authorization:
      mode: required
""".lstrip(),
    )
    monkeypatch.delenv("PHLO_AUTHORIZATION_MODE", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authorization_mode() == "required"


def test_env_authorization_mode_overrides_phlo_yaml(monkeypatch, tmp_path: Path) -> None:
    _write_phlo_config(
        tmp_path,
        """
api:
  authorization:
    mode: optional
""".lstrip(),
    )
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "required")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authorization_mode() == "required"


def test_empty_env_authorization_mode_falls_back_to_phlo_yaml(monkeypatch, tmp_path: Path) -> None:
    _write_phlo_config(
        tmp_path,
        """
api:
  authorization:
    mode: required
""".lstrip(),
    )
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authorization_mode() == "required"


def test_get_authorization_backend_uses_service_specific_phlo_yaml(
    monkeypatch, tmp_path: Path
) -> None:
    backend = DefaultAuthorizationPolicyBackend()
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="default", provider=DefaultAuthorizationPolicyBackend()
        ),
    )
    register_capability(
        "authorization_policy_backend", AuthorizationPolicyBackendSpec(name="opa", provider=backend)
    )
    _write_phlo_config(
        tmp_path,
        """
services:
  phlo-api:
    authorization:
      backend: opa
""".lstrip(),
    )
    monkeypatch.delenv("PHLO_AUTHORIZATION_BACKEND", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authorization_backend() is backend
