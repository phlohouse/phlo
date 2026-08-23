"""Traefik reverse proxy service package for Phlo.

This package provides a Traefik-based reverse proxy service plugin for the Phlo
platform, enabling local routing with named service URLs.

Example:
    >>> from phlo_traefik import TraefikServicePlugin
    >>> plugin = TraefikServicePlugin()
    >>> metadata = plugin.metadata

    Exposes TraefikServicePlugin for the reverse proxy service.
"""

from phlo_traefik.plugin import TraefikServicePlugin

__all__ = ["TraefikServicePlugin"]
