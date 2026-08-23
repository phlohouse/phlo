"""
Infrastructure Configuration Schema

Pydantic models for phlo.yaml infrastructure section.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiAuthorizationConfig(BaseModel):
    """Authorization configuration for phlo-api."""

    backend: str | None = Field(
        default=None,
        description="Authorization backend capability name.",
    )
    mode: str | None = Field(
        default=None,
        description="Guard behavior when no authorization backend exists.",
    )

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str | None) -> str | None:
        """Validate backend names when provided."""
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("backend cannot be empty")
        return normalized

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        """Validate supported authorization modes."""
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"optional", "required"}:
            raise ValueError("mode must be 'optional' or 'required'")
        return normalized


class ApiConfig(BaseModel):
    """Top-level phlo-api configuration."""

    authorization: ApiAuthorizationConfig | None = Field(
        default=None,
        description="Authorization settings for phlo-api.",
    )


class WapConfig(BaseModel):
    """Project-level Write-Audit-Publish launch configuration.

    WAP is deliberately a project policy rather than a per-invocation switch:
    enabling it makes every Phlo materialize or backfill launch branch-first.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Launch materialize and backfill runs through the WAP GraphQL lifecycle.",
    )
    job_name: str = Field(
        default="__ASSET_JOB",
        description="Dagster asset job used for WAP launches.",
    )
    repository_location_name: str | None = Field(
        default="phlo_dagster.framework.definitions",
        description="Dagster code-location selector for generated WAP launches.",
    )
    repository_name: str | None = Field(
        default="__repository__",
        description="Dagster repository selector for generated WAP launches.",
    )
    dagster_url: str | None = Field(
        default=None,
        description="Optional remote Dagster GraphQL endpoint used for WAP launches.",
    )

    @field_validator("job_name", "repository_location_name", "repository_name")
    @classmethod
    def validate_nonblank(cls, value: str | None) -> str | None:
        """Normalize optional selectors and reject empty required settings."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("WAP string settings cannot be empty")
        return normalized

    @field_validator("dagster_url")
    @classmethod
    def validate_dagster_url(cls, value: str | None) -> str | None:
        """Allow plaintext GraphQL only for local development endpoints."""
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/graphql"
        ):
            raise ValueError("dagster_url must be an absolute HTTP(S) GraphQL endpoint")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("dagster_url must use HTTPS unless it targets localhost")
        return normalized

    @property
    def requires_access_token(self) -> bool:
        """Whether this endpoint is remote and must use explicit bearer authentication."""
        return self.dagster_url is not None and urlparse(self.dagster_url).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }


class ServiceOverride(BaseModel):
    """User overrides for a service in phlo.yaml.

    Allows customizing installed service configurations without
    modifying the package's bundled service.yaml.

    Example in phlo.yaml:
        services:
          observatory:
            enabled: true
            ports:
              - "8080:3000"
            environment:
              DEBUG: "true"
          superset:
            enabled: false
    """

    enabled: bool = Field(
        default=True,
        description="Whether to include this service. Set to false to disable.",
    )
    ports: list[str] | None = Field(
        default=None,
        description="Port mappings to override (replaces package defaults).",
    )
    environment: dict[str, str] | None = Field(
        default=None,
        description="Environment variables to add/override (merged with package defaults).",
    )
    volumes: list[str] | None = Field(
        default=None,
        description="Volume mounts to add (appended to package defaults).",
    )
    extra_hosts: list[str] | None = Field(
        default=None,
        description="Compose host mappings to add or replace for this service.",
    )
    depends_on: list[str] | None = Field(
        default=None,
        description="Service dependencies to override (replaces package defaults).",
    )
    command: str | list[str] | None = Field(
        default=None,
        description="Container command override.",
    )
    authorization: ApiAuthorizationConfig | None = Field(
        default=None,
        description="Service-scoped authorization settings for phlo-api.",
    )

    # For inline custom services (type: inline)
    type: str | None = Field(
        default=None,
        description="Service type. Set to 'inline' for custom services defined in phlo.yaml.",
    )
    image: str | None = Field(
        default=None,
        description="Docker image for inline services.",
    )
    build: dict[str, Any] | None = Field(
        default=None,
        description="Build configuration for inline services.",
    )
    healthcheck: dict[str, Any] | None = Field(
        default=None,
        description="Healthcheck configuration for inline services.",
    )

    @field_validator("extra_hosts")
    @classmethod
    def validate_extra_hosts(cls, value: list[str] | None) -> list[str] | None:
        """Require non-empty Docker Compose host-to-address mappings."""
        if value is None:
            return value
        for mapping in value:
            if not isinstance(mapping, str) or not mapping.strip():
                raise ValueError("extra_hosts mappings must be non-empty")
            host, separator, address = mapping.strip().partition("=")
            if not separator:
                host, separator, address = mapping.strip().partition(":")
            if not separator or not host.strip() or not address.strip():
                raise ValueError("extra_hosts entries must be Compose host mappings")
        return [mapping.strip() for mapping in value]


class ServiceConfig(BaseModel):
    """Configuration for a single service."""

    container_name: str | None = Field(
        default=None,
        description="Explicit container name override. If None, uses container_naming_pattern.",
    )
    service_name: str = Field(
        description="Docker compose service name (e.g., 'dagster-webserver', 'postgres')"
    )
    host: str | None = Field(
        default="localhost",
        description="External hostname for accessing the service",
    )
    internal_host: str | None = Field(
        default=None,
        description="Internal Docker network hostname. If None, uses service_name.",
    )

    @field_validator("container_name")
    @classmethod
    def validate_container_name(cls, v: str | None) -> str | None:
        """Validate `container_name` characters and format.

        Raises: ValueError when the name is empty or contains invalid characters.
        """
        if v is None:
            return v

        if not v:
            raise ValueError("container_name cannot be empty string")

        valid_chars = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
        if not all(c in valid_chars for c in v.lower()):
            raise ValueError(
                "container_name must contain only alphanumeric characters, hyphens, underscores, and dots"
            )

        if v.startswith(("-", ".")):
            raise ValueError("container_name cannot start with hyphen or dot")

        return v

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        """Validate and normalize a service name, returning the trimmed value.

        Raises: ValueError when the service name is empty.
        """
        if not v or not v.strip():
            raise ValueError("service_name cannot be empty")
        return v.strip()

    def get_container_name(self, project_name: str, pattern: str) -> str:
        """Get effective container name, applying pattern if needed."""
        if self.container_name:
            return self.container_name
        return pattern.format(project=project_name, service=self.service_name)

    def get_internal_host(self) -> str:
        """Get effective internal hostname."""
        return self.internal_host or self.service_name


class NetworkConfig(BaseModel):
    """Docker network configuration."""

    name: str | None = Field(
        default=None,
        description="Network name. If None, uses docker compose default.",
    )
    driver: str = Field(
        default="bridge",
        description="Network driver (e.g., 'bridge', 'overlay')",
    )


class InfrastructureConfig(BaseModel):
    """Infrastructure configuration section from phlo.yaml."""

    container_backend: Literal["docker", "podman", "auto"] = Field(
        default="docker",
        description="Container backend used by service lifecycle commands.",
    )

    container_naming_pattern: str = Field(
        default="{project}-{service}-1",
        description="Pattern for generating container names. Available variables: {project}, {service}",
    )

    services: dict[str, ServiceConfig] = Field(
        default_factory=dict,
        description="Service definitions keyed by service identifier",
    )

    network: NetworkConfig = Field(
        default_factory=NetworkConfig,
        description="Docker network configuration",
    )

    @field_validator("container_naming_pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        """Validate a container naming pattern.

        Raises: ValueError when the pattern includes neither `{project}` nor `{service}`.
        """
        if "{project}" not in v and "{service}" not in v:
            raise ValueError(
                "container_naming_pattern must contain at least {project} or {service}"
            )
        return v

    def get_service(self, service_key: str) -> ServiceConfig | None:
        """Get service configuration by key."""
        return self.services.get(service_key)

    def get_container_name(self, service_key: str, project_name: str) -> str | None:
        """Get container name for a service."""
        service = self.get_service(service_key)
        if not service:
            return None
        return service.get_container_name(project_name, self.container_naming_pattern)
