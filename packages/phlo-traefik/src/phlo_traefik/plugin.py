"""Traefik service plugin registration.

TraefikServicePlugin is built via service_plugin_class so discovery offers
traefik as a managed reverse-proxy service.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the traefik reverse-proxy service through the phlo.plugins factory.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


TraefikServicePlugin = service_plugin_class(
    "TraefikServicePlugin",
    name="traefik",
    version="0.1.0",
    description="Local reverse proxy for named service URLs",
    author="Phlo Team",
    tags=["networking", "proxy", "traefik"],
)
