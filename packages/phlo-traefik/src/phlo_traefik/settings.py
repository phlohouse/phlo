"""Traefik service configuration constants.

This module defines default configuration values for the Traefik reverse
proxy service integration with the Phlo platform.

All values defined here can be overridden through environment variables
or configuration files in the Phlo settings system.

Example:
    >>> from phlo_traefik.settings import TRAEFIK_HTTP_PORT_DEFAULT
    >>> print(TRAEFIK_HTTP_PORT_DEFAULT)
    80

Leaf constants module: no repository module imports it directly, so callers take
these defaults unless overridden by PHLO_TRAEFIK_* environment variables.
"""

from __future__ import annotations

#: Default HTTP port for Traefik web entrypoint.
#: This is the port Traefik listens on for incoming HTTP traffic.
#: Can be overridden via PHLO_TRAEFIK_HTTP_PORT environment variable.
TRAEFIK_HTTP_PORT_DEFAULT: int = 80

#: Default domain suffix for local service routing.
#: Services are exposed as <service-name>.phlo.localhost by default.
#: Can be overridden via PHLO_TRAEFIK_DOMAIN environment variable.
TRAEFIK_DOMAIN_DEFAULT: str = "phlo.localhost"
