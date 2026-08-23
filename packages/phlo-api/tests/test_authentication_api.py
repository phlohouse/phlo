"""Tests for phlo-api authentication helpers and service definition.

Covers provider resolution precedence: a provider registered via phlo.yaml
capabilities wins unless an env-configured provider is set, conflicting
registrations are rejected, and the declared authentication method must
match the provider. Also asserts contract properties of the packaged API
service (forward-auth middleware, no docker socket mount, portable build
context, no unauthenticated Traefik route).
"""

from __future__ import annotations

import pytest
from pathlib import Path

from phlo.capabilities import AuthenticationProviderSpec, clear_all_capabilities
from phlo.capabilities.registry import register_capability
from phlo.infrastructure.config import clear_config_cache
from phlo_api.api.authentication import get_authentication_provider


def teardown_function() -> None:
    clear_all_capabilities()
    clear_config_cache()


def _write_phlo_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "phlo.yaml").write_text(content)


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
    """Verify phlo-api declares forwardAuth middleware for oauth2-proxy.

    The middleware is defined in service-auth.yaml which is conditionally
    included when the proxy profile is active.
    """
    import yaml
    from importlib.resources import files

    package_root = files("phlo_api")
    auth_defn_path = package_root / "service-auth.yaml"

    if not auth_defn_path.exists():
        pytest.skip("service-auth.yaml not found - oauth2-proxy integration not configured")

    with open(auth_defn_path) as f:
        auth_defn = yaml.safe_load(f)

    auth_labels = auth_defn.get("compose", {}).get("labels", {})

    assert "traefik.http.routers.api.middlewares" in auth_labels
    assert auth_labels["traefik.http.routers.api.middlewares"] == "phlo-api-auth@docker"
    assert "traefik.http.middlewares.phlo-api-auth.forwardauth.address" in auth_labels
    assert (
        "/oauth2/auth" in auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.address"]
    )
    assert (
        "oauth2-proxy" in auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.address"]
    )
    assert (
        auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.trustForwardHeader"]
        == "true"
    )
    assert (
        "X-Forwarded-User"
        in auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.authResponseHeaders"]
    )
    assert (
        "X-Forwarded-Email"
        in auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.authResponseHeaders"]
    )
    assert (
        "X-Forwarded-Groups"
        in auth_labels["traefik.http.middlewares.phlo-api-auth.forwardauth.authResponseHeaders"]
    )


def test_phlo_api_service_passes_clickstack_query_env() -> None:
    import yaml
    from importlib.resources import files

    service_defn_path = files("phlo_api") / "service.yaml"

    with open(service_defn_path) as f:
        service_defn = yaml.safe_load(f)

    compose_env = service_defn["compose"]["environment"]
    dev_env = service_defn["dev"]["environment"]

    for env in (compose_env, dev_env):
        assert env["CLICKSTACK_QUERY_URL"] == "${CLICKSTACK_QUERY_URL:-}"
        assert env["CLICKSTACK_QUERY_USER"] == "${CLICKSTACK_QUERY_USER:-}"
        assert env["CLICKSTACK_QUERY_PASSWORD"] == "${CLICKSTACK_QUERY_PASSWORD:-}"


def test_phlo_api_service_does_not_mount_docker_socket_by_default() -> None:
    import yaml
    from importlib.resources import files

    service_defn_path = files("phlo_api") / "service.yaml"

    with open(service_defn_path) as f:
        service_defn = yaml.safe_load(f)

    volumes = service_defn["compose"].get("volumes", [])
    assert not any("/var/run/docker.sock" in volume for volume in volumes)


def test_phlo_api_service_build_context_is_package_portable() -> None:
    import yaml
    from importlib.resources import files

    service_defn_path = files("phlo_api") / "service.yaml"

    with open(service_defn_path) as f:
        service_defn = yaml.safe_load(f)

    assert service_defn["build"] == {
        "context": ".",
        "dockerfile": "phlo-api/Dockerfile",
        "args": {
            "PHLO_VERSION": "${PHLO_VERSION:-}",
            "PHLO_API_VERSION": "${PHLO_API_VERSION:-}",
            "PHLO_WHEELHOUSE": "${PHLO_WHEELHOUSE:-}",
        },
    }
    assert service_defn["env_vars"]["PHLO_VERSION"]["package"] == "phlo"
    assert service_defn["env_vars"]["PHLO_API_VERSION"]["package"] == "phlo-api"


def test_phlo_api_service_does_not_publish_unauthenticated_traefik_route() -> None:
    import yaml
    from importlib.resources import files

    service_defn_path = files("phlo_api") / "service.yaml"

    with open(service_defn_path) as f:
        service_defn = yaml.safe_load(f)

    labels = service_defn["compose"].get("labels", {})
    assert "traefik.http.routers.api.rule" not in labels
    assert "traefik.http.routers.api.entrypoints" not in labels
