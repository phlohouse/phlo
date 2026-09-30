"""Tests for phlo-api authentication helpers and service definition.

Covers provider resolution precedence: a provider registered via phlo.yaml
capabilities wins unless an env-configured provider is set, conflicting
registrations are rejected, and the declared authentication method must
match the provider. Also asserts contract properties of the packaged API
service (forward-auth middleware, no docker socket mount, portable build
context, no unauthenticated Traefik route).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.requests import Request

from phlo.capabilities import AuthenticationProviderSpec, clear_all_capabilities
from phlo.capabilities.registry import register_capability
from phlo.infrastructure.config import clear_config_cache
from phlo_api.api.authentication import (
    create_request_context,
    get_authentication_provider,
    get_request_principal,
)


def teardown_function() -> None:
    clear_all_capabilities()
    clear_config_cache()


def _write_phlo_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "phlo.yaml").write_text(content)


def _load_packaged_definition(name: str) -> dict[str, Any]:
    definition_path = files("phlo_api") / name
    with open(definition_path) as f:
        return yaml.safe_load(f)


class _DummyAuthenticationProvider:
    pass


def test_get_authentication_provider_uses_phlo_yaml(monkeypatch, tmp_path: Path) -> None:
    provider = _DummyAuthenticationProvider()
    register_capability(
        "authentication_provider", AuthenticationProviderSpec(name="proxy", provider=provider)
    )

    _write_phlo_config(
        tmp_path,
        """
authentication:
  provider: proxy
""".lstrip(),
    )
    monkeypatch.delenv("PHLO_AUTHENTICATION_PROVIDER", raising=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authentication_provider() is provider


def test_conflicting_env_authentication_provider_is_rejected(monkeypatch, tmp_path: Path) -> None:
    proxy_provider = _DummyAuthenticationProvider()
    static_provider = _DummyAuthenticationProvider()
    register_capability(
        "authentication_provider", AuthenticationProviderSpec(name="proxy", provider=proxy_provider)
    )
    register_capability(
        "authentication_provider",
        AuthenticationProviderSpec(name="static", provider=static_provider),
    )

    _write_phlo_config(
        tmp_path,
        """
authentication:
  provider: proxy
""".lstrip(),
    )
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "static")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    with pytest.raises(RuntimeError, match="Conflicting authentication settings"):
        get_authentication_provider()


def test_empty_env_authentication_provider_falls_back_to_phlo_yaml(
    monkeypatch, tmp_path: Path
) -> None:
    provider = _DummyAuthenticationProvider()
    register_capability(
        "authentication_provider", AuthenticationProviderSpec(name="proxy", provider=provider)
    )

    _write_phlo_config(
        tmp_path,
        """
authentication:
  provider: proxy
""".lstrip(),
    )
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    clear_config_cache()

    assert get_authentication_provider() is provider


def test_authentication_method_and_provider_must_match(monkeypatch) -> None:
    from phlo_api.api.authentication import get_authentication_provider

    monkeypatch.setenv("PHLO_AUTHENTICATION_METHOD", "proxy")
    monkeypatch.setenv("PHLO_AUTHENTICATION_PROVIDER", "jwt")

    with pytest.raises(RuntimeError, match="Conflicting authentication settings"):
        get_authentication_provider()


def test_phlo_api_has_forward_auth_middleware() -> None:
    """Verify the generated API service owns a routed oauth2-proxy chain."""
    auth_labels = _load_packaged_definition("service.yaml")["compose"]["labels"]

    assert auth_labels["traefik.enable"] == "true"
    assert auth_labels["traefik.http.routers.phlo-api.rule"] == (
        "Host(`api.${TRAEFIK_DOMAIN:-phlo.localhost}`) && !PathPrefix(`/oauth2/`)"
    )
    assert auth_labels["traefik.http.routers.phlo-api.middlewares"] == (
        "phlo-api-auth,phlo-api-login"
    )
    assert (
        auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.address"]
        == "http://oauth2-proxy:4180/oauth2/auth"
    )
    assert (
        auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.trustForwardHeader"]
        == "false"
    )
    response_headers = auth_labels[
        "traefik.http.middlewares.phlo-api-auth.forwardauth.authResponseHeaders"
    ].split(",")
    assert response_headers == ["X-Auth-Request-Access-Token"]
    assert auth_labels["traefik.http.middlewares.phlo-api-login.errors.status"] == "401"
    assert auth_labels["traefik.http.middlewares.phlo-api-login.errors.service"] == (
        "oauth2-proxy@docker"
    )
    assert auth_labels["traefik.http.middlewares.phlo-api-login.errors.query"] == (
        "/oauth2/sign_in?rd={url}"
    )


def test_oauth2_proxy_access_token_is_forwarded_as_bearer_without_trusting_identity_headers() -> (
    None
):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/me",
            "query_string": b"",
            "headers": [
                (b"x-auth-request-access-token", b"signed-token"),
                (b"x-forwarded-user", b"attacker"),
                (b"x-forwarded-groups", b"admin"),
            ],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )

    context = create_request_context(request)

    assert context.headers["authorization"] == "Bearer signed-token"
    assert "x-forwarded-user" in context.headers
    assert "x-forwarded-groups" in context.headers


def test_direct_bearer_authorization_takes_precedence_over_forwarded_token() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/me",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer direct-token"),
                (b"x-auth-request-access-token", b"proxy-token"),
            ],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )

    assert create_request_context(request).headers["authorization"] == "Bearer direct-token"


def test_oidc_jwks_outage_returns_service_unavailable(monkeypatch) -> None:
    from fastapi import HTTPException

    from phlo.security.oidc_identity import OIDCVerificationUnavailable

    class UnavailableProvider:
        def current_principal(self, _context):
            raise OIDCVerificationUnavailable

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/me",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )
    monkeypatch.setenv("PHLO_IDENTITY_AUTHORITY_ENABLED", "0")
    monkeypatch.setattr(
        "phlo_api.api.authentication.get_authentication_provider", lambda: UnavailableProvider()
    )

    with pytest.raises(HTTPException) as error:
        get_request_principal(request)

    assert error.value.status_code == 503


def test_phlo_api_service_passes_clickstack_query_env() -> None:
    service_defn = _load_packaged_definition("service.yaml")

    compose_env = service_defn["compose"]["environment"]
    dev_env = service_defn["dev"]["environment"]

    for env in (compose_env, dev_env):
        assert env["CLICKSTACK_QUERY_URL"] == "${CLICKSTACK_QUERY_URL:-}"
        assert env["CLICKSTACK_QUERY_USER"] == "${CLICKSTACK_QUERY_USER:-}"
        assert env["CLICKSTACK_QUERY_PASSWORD"] == "${CLICKSTACK_QUERY_PASSWORD:-}"


def test_phlo_api_passes_oidc_settings_to_compose_and_dev_process() -> None:
    service_defn = _load_packaged_definition("service.yaml")
    oidc_settings = (
        "PHLO_AUTHENTICATION_PROVIDER",
        "PHLO_AUTH_JWT_SECRET",
        "PHLO_AUTH_JWT_ISSUER",
        "PHLO_AUTH_JWT_AUDIENCE",
        "PHLO_AUTH_JWT_JWKS_URL",
        "PHLO_AUTH_JWT_GROUPS_CLAIM",
        "PHLO_AUTH_JWT_CA_FILE",
        "PHLO_AUTH_JWT_LEEWAY",
        "PHLO_AUTH_JWT_JWKS_CACHE_TTL_SECONDS",
        "PHLO_AUTH_JWT_REFRESH_MIN_INTERVAL_SECONDS",
    )

    for environment in (
        service_defn["compose"]["environment"],
        service_defn["dev"]["environment"],
    ):
        for setting in oidc_settings:
            assert setting in environment


def test_phlo_api_service_does_not_mount_docker_socket_by_default() -> None:
    service_defn = _load_packaged_definition("service.yaml")
    compose_config = service_defn["compose"]

    mounts = [*compose_config.get("volumes", []), *compose_config.get("devices", [])]
    assert not any("/var/run/docker.sock" in str(mount) for mount in mounts)


def test_phlo_api_service_persists_operation_audit_beside_idempotency_state() -> None:
    volumes = _load_packaged_definition("service.yaml")["compose"]["volumes"]

    assert "../:/app:ro" in volumes
    assert "../.phlo/state:/app/.phlo/state" in volumes
    assert "../.phlo/audit:/app/.phlo/audit" in volumes


def test_phlo_api_service_build_context_is_package_portable() -> None:
    service_defn = _load_packaged_definition("service.yaml")

    build_args = service_defn["build"]["args"]
    assert service_defn["build"]["context"] == "."
    assert service_defn["build"]["dockerfile"] == "phlo-api/Dockerfile"
    assert build_args["PHLO_VERSION"] == "${PHLO_VERSION:-}"
    assert build_args["PHLO_API_VERSION"] == "${PHLO_API_VERSION:-}"
    assert build_args["PHLO_WHEELHOUSE"] == "${PHLO_WHEELHOUSE:-}"

    assert service_defn["env_vars"]["PHLO_VERSION"]["package"] == "phlo"
    assert service_defn["env_vars"]["PHLO_API_VERSION"]["package"] == "phlo-api"


def test_phlo_api_traefik_route_uses_oauth2_proxy_authentication() -> None:
    labels = _load_packaged_definition("service.yaml")["compose"].get("labels", {})

    assert labels["traefik.http.routers.phlo-api.middlewares"] == ("phlo-api-auth,phlo-api-login")
