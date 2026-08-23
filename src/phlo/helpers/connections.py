"""Connection resolution and representation helpers.

Normalizes connections from URLs or capability metadata into immutable
ConnectionConfig values and converts them to the dialects of external
tools (Sling env, SQLAlchemy URL, dbt profile target). Redaction of
secrets happens at every representation boundary.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from phlo.capabilities import resolve_capability
from phlo.exceptions import PhloConfigError
from phlo.helpers._common import redact_mapping


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    """Normalized connection configuration for workflow helpers."""

    name: str
    kind: str
    config: dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """Return a redacted config suitable for logs and previews."""
        return redact_mapping(self.config)

    def as_sling_connection(self) -> dict[str, Any]:
        """Return a Sling-compatible connection mapping."""
        payload = dict(self.config)
        payload.setdefault("type", self.kind)
        return payload

    def as_sqlalchemy_url(self) -> str:
        """Return a SQLAlchemy-style URL when enough parts are available."""
        url = self.config.get("url") or self.config.get("dsn")
        if isinstance(url, str) and url:
            return url
        driver = str(self.config.get("driver") or self.kind)
        user = quote(str(self.config.get("user") or self.config.get("username") or ""))
        password = quote(str(self.config.get("password") or ""))
        host = str(self.config.get("host") or "localhost")
        port = self.config.get("port")
        database = quote(str(self.config.get("database") or self.config.get("dbname") or ""))
        auth = user if user else ""
        if password:
            auth = f"{auth}:{password}"
        if auth:
            auth = f"{auth}@"
        hostport = f"{host}:{port}" if port else host
        path = f"/{database}" if database else ""
        return f"{driver}://{auth}{hostport}{path}"

    def as_dbt_profile_target(self) -> dict[str, Any]:
        """Return a dbt profile target-like mapping."""
        payload = dict(self.config)
        payload.setdefault("type", self.kind)
        if "database" in payload and "dbname" not in payload:
            payload["dbname"] = payload["database"]
        return payload


def redact_connection_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Redact secrets in a connection config."""
    return redact_mapping(config)


def connection_from_url(
    url: str,
    *,
    name: str = "connection",
    kind: str | None = None,
) -> ConnectionConfig:
    """Parse a database URL into a normalized connection config."""
    parsed = urlsplit(url)
    if not parsed.scheme:
        raise PhloConfigError(
            message="Connection URL must include a scheme",
            suggestions=["Use a URL such as postgresql://user:pass@host:5432/db."],
        )
    config: dict[str, Any] = {
        "url": url,
        "driver": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
    }
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query:
        config["query"] = query
    return ConnectionConfig(name=name, kind=kind or parsed.scheme, config=config)


def redacted_url(url: str, *, replacement: str = "<redacted>") -> str:
    """Return a URL with the password component redacted."""
    parsed = urlsplit(url)
    if parsed.password is None:
        return url
    user = quote(unquote(parsed.username or ""))
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{user}:{replacement}@{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def resolve_database(
    name: str | None = None,
    *,
    capability_type: str = "query_engine",
    env_prefix: str | None = None,
) -> ConnectionConfig | None:
    """Resolve a database-like connection from capability metadata or env vars."""
    if env_prefix:
        url = os.environ.get(f"{env_prefix}_URL") or os.environ.get(f"{env_prefix}_DSN")
        if url:
            return connection_from_url(url, name=name or env_prefix.lower())
    resolution = resolve_capability(capability_type, name)
    if resolution is None:
        return None
    provider = resolution.provider
    if hasattr(provider, "to_connection_config"):
        raw = provider.to_connection_config()
    elif hasattr(provider, "to_sling_connection"):
        raw = provider.to_sling_connection()
    else:
        raw = resolution.metadata
    kind = str(raw.get("type") or raw.get("driver") or resolution.name)
    return ConnectionConfig(name=resolution.name, kind=kind, config=dict(raw))


def as_sling_connection(connection: ConnectionConfig | Mapping[str, Any]) -> dict[str, Any]:
    """Convert a connection object or mapping to Sling connection config."""
    if isinstance(connection, ConnectionConfig):
        return connection.as_sling_connection()
    payload = dict(connection)
    if "type" not in payload and "driver" in payload:
        payload["type"] = payload["driver"]
    return payload


def export_sling_env(connections: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Serialize Sling connections as environment variables."""
    return {name: json.dumps(dict(config), sort_keys=True) for name, config in connections.items()}


def as_sqlalchemy_url(connection: ConnectionConfig | Mapping[str, Any]) -> str:
    """Return a SQLAlchemy URL from a connection config."""
    if isinstance(connection, ConnectionConfig):
        return connection.as_sqlalchemy_url()
    return ConnectionConfig(
        name=str(connection.get("name", "connection")),
        kind=str(connection.get("type") or connection.get("driver") or "database"),
        config=dict(connection),
    ).as_sqlalchemy_url()


def as_dbt_profile(
    connection: ConnectionConfig | Mapping[str, Any],
    *,
    target_name: str = "dev",
) -> dict[str, Any]:
    """Build a minimal dbt profile target mapping."""
    if not isinstance(connection, ConnectionConfig):
        connection = ConnectionConfig(
            name=str(connection.get("name", "connection")),
            kind=str(connection.get("type") or connection.get("driver") or "database"),
            config=dict(connection),
        )
    return {"target": target_name, "outputs": {target_name: connection.as_dbt_profile_target()}}


def build_url(
    *,
    scheme: str,
    host: str,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    query: Mapping[str, Any] | None = None,
) -> str:
    """Build a connection URL from parts."""
    auth = quote(user or "") if user else ""
    if password:
        auth = f"{auth}:{quote(password)}"
    if auth:
        auth = f"{auth}@"
    netloc = f"{auth}{host}"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = f"/{quote(database)}" if database else ""
    return urlunsplit((scheme, netloc, path, urlencode(query or {}), ""))
