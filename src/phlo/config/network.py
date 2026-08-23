"""Host resolution helpers for Docker ↔ host portability.

When phlo CLI commands run on the host machine, Docker-internal hostnames
(``postgres``, ``minio``, ``trino``, etc.) are unreachable.  These helpers
detect that situation and transparently fall back to ``localhost`` with the
appropriate exposed port.
"""

from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit

import structlog

from phlo.config.env import project_env_value

logger = structlog.get_logger(__name__)

_LOCALHOST = {"localhost", "127.0.0.1", "::1"}


def _port_from_env(port_env_var: str | None, default: int) -> int:
    """Resolve a port override, falling back when the value is invalid."""
    if not port_env_var:
        return default
    env_value = project_env_value(port_env_var, str(default))
    try:
        return int(env_value) if env_value is not None else default
    except ValueError:
        logger.warning(
            "invalid_port_env_value",
            env_var=port_env_var,
            value=env_value,
            fallback_port=default,
        )
        return default


def resolve_host(host: str, port: int, *, port_env_var: str | None = None) -> tuple[str, int]:
    """Resolve a service hostname, falling back to localhost if DNS fails.

    Returns an ``(host, port)`` tuple resolved to ``localhost`` when the original
    hostname cannot be reached from the current environment. ``port_env_var``
    optionally supplies the host-exposed port.
    """
    if host in _LOCALHOST:
        return host, port

    try:
        # DNS resolution doubles as the container-detection probe: a
        # hostname that only exists inside the Docker network fails to
        # resolve on the host, which is the signal to rewrite to localhost
        # with the exposed port.
        socket.gethostbyname(host)
        return host, port
    except socket.gaierror:
        resolved_port = _port_from_env(port_env_var, port)
        logger.debug(
            "host_resolved_to_localhost",
            original_host=host,
            original_port=port,
            resolved_port=resolved_port,
        )
        return "localhost", resolved_port


def resolve_url(url: str, *, port_env_var: str | None = None) -> str:
    """Resolve a service URL, falling back to localhost if the host is unreachable.

    ``port_env_var`` optionally supplies the host-exposed port used in the
    substituted URL.
    """
    if not url:
        return url

    parsed = urlsplit(url)
    host = parsed.hostname
    if not host or host in _LOCALHOST:
        return url

    try:
        socket.gethostbyname(host)
        return url
    except socket.gaierror:
        original_port = parsed.port
        default_port = original_port or 80
        resolved_port = _port_from_env(port_env_var, default_port)
        netloc = f"localhost:{resolved_port}" if resolved_port else "localhost"
        resolved = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        logger.debug(
            "url_resolved_to_localhost",
            original_url=url,
            resolved_url=resolved,
        )
        return resolved
