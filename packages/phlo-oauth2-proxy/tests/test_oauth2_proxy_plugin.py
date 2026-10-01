"""Tests for the oauth2-proxy service plugin.

Pins the auth-category, proxy-profile service definition with a
digest-pinned upstream image and an oauth2 callback-only Traefik route; the
distroless image must not claim an in-container health check.
"""

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
        "Host(`api.${TRAEFIK_DOMAIN:-phlo.localhost}`) && PathPrefix(`/oauth2/`)"
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
    assert environment["OAUTH2_PROXY_WHITELIST_DOMAINS"] == (
        "${OAUTH2_PROXY_WHITELIST_DOMAINS:-api.${TRAEFIK_DOMAIN:-phlo.localhost}}"
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
