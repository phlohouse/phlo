"""Tests for the oauth2-proxy service plugin.

Pins the auth-category, proxy-profile service definition with a
digest-pinned upstream image and an oauth2 callback-only Traefik route; the
distroless image must not claim an in-container health check.
"""

import base64

from phlo.plugins.compose.env import generate_env, generate_env_local
from phlo.plugins.discovery.services import ServiceDefinition
from phlo_oauth2_proxy.plugin import Oauth2ProxyServicePlugin


def test_oauth2_proxy_service_definition():
    """Validate oauth2-proxy service metadata in compose definition."""
    plugin = Oauth2ProxyServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "oauth2-proxy"
    assert defn["category"] == "auth"
    assert defn["default"] is False
    assert defn["profile"] == "proxy"


def test_oauth2_proxy_plugin_metadata():
    """Validate oauth2-proxy plugin metadata tags and name."""
    plugin = Oauth2ProxyServicePlugin()
    meta = plugin.metadata

    assert meta.name == "oauth2-proxy"
    assert "auth" in meta.tags
    assert "oidc" in meta.tags


def test_oauth2_proxy_exposes_only_its_callback_path_through_traefik():
    """Verify oauth2-proxy is reachable only at the shared auth callback path."""
    plugin = Oauth2ProxyServicePlugin()
    defn = plugin.service_definition
    labels = defn.get("compose", {}).get("labels", {})

    assert labels["traefik.enable"] == "true"
    assert labels["traefik.http.routers.oauth2-proxy.rule"] == (
        "(Host(`${PHLO_API_HOST:-api.${TRAEFIK_DOMAIN:-phlo.localhost}}`) || Host(`${OBSERVATORY_HOST:-observatory.${TRAEFIK_DOMAIN:-phlo.localhost}}`)) && PathPrefix(`/oauth2/`)"
    )
    assert labels["traefik.http.routers.oauth2-proxy.entrypoints"] == "web"
    assert labels["traefik.http.routers.oauth2-proxy.priority"] == "100"
    assert labels["traefik.http.services.oauth2-proxy.loadbalancer.server.port"] == "4180"
    environment = defn["compose"]["environment"]
    assert environment["OAUTH2_PROXY_SET_XAUTHREQUEST"] == "true"
    assert environment["OAUTH2_PROXY_PASS_ACCESS_TOKEN"] == "true"
    assert environment["OAUTH2_PROXY_COOKIE_REFRESH"] == "${OAUTH2_PROXY_COOKIE_REFRESH:-5m}"
    assert environment["OAUTH2_PROXY_SCOPE"] == (
        "${OAUTH2_PROXY_SCOPE:-openid email profile offline_access}"
    )
    assert environment["OAUTH2_PROXY_REDIRECT_URL"] == (
        "${OAUTH2_PROXY_REDIRECT_URL:-http://${PHLO_API_HOST:-api.${TRAEFIK_DOMAIN:-phlo.localhost}}/oauth2/callback}"
    )
    assert environment["OAUTH2_PROXY_WHITELIST_DOMAINS"] == (
        "${OAUTH2_PROXY_WHITELIST_DOMAINS:-${PHLO_API_HOST:-api.${TRAEFIK_DOMAIN:-phlo.localhost}},${OBSERVATORY_HOST:-observatory.${TRAEFIK_DOMAIN:-phlo.localhost}}}"
    )


def test_oauth2_proxy_image_pinned():
    """Verify oauth2-proxy uses a pinned image version."""
    plugin = Oauth2ProxyServicePlugin()
    defn = plugin.service_definition

    assert defn["image"] == (
        "quay.io/oauth2-proxy/oauth2-proxy:v7.15.3@"
        "sha256:10a1165743a192e1940b4708fb9647027185ce11a681a1c5519b442ff7f1f561"
    )
    assert "build" not in defn


def test_oauth2_proxy_distroless_image_does_not_claim_an_in_container_probe():
    definition = Oauth2ProxyServicePlugin().service_definition

    assert "healthcheck" not in definition["compose"]
    assert not definition.get("files")


def _env_values(content: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in content.splitlines() if line and not line.startswith("#")
    )


def test_oauth2_proxy_credentials_survive_secret_file_regeneration():
    """Verify regeneration keeps operator-supplied credentials in the secrets file."""
    definition = Oauth2ProxyServicePlugin().service_definition
    service = ServiceDefinition(
        name="oauth2-proxy",
        description="oauth2-proxy",
        category="auth",
        default=False,
        env_vars=definition["env_vars"],
    )

    generated = _env_values(generate_env_local([service]))
    cookie_secret = generated["OAUTH2_PROXY_COOKIE_SECRET"]
    assert len(base64.urlsafe_b64decode(cookie_secret)) == 32

    regenerated = _env_values(
        generate_env_local(
            [service],
            existing_values={
                "OAUTH2_PROXY_CLIENT_SECRET": "issued-by-identity-provider",
                "OAUTH2_PROXY_COOKIE_SECRET": cookie_secret,
            },
        )
    )
    assert regenerated["OAUTH2_PROXY_CLIENT_SECRET"] == "issued-by-identity-provider"
    assert regenerated["OAUTH2_PROXY_COOKIE_SECRET"] == cookie_secret
    assert "OAUTH2_PROXY_CLIENT_SECRET" not in _env_values(generate_env([service]))
