"""Typed settings for the phlo-api service.

Declares the ``PHLO_API_*`` environment variables the service reads, so each
one has a single type, default and description and appears in the generated
settings reference. Values come from the process environment and the
project's generated ``.phlo`` env files, as for every other ``BaseConfig``.

``get_settings`` builds a fresh model on each call rather than caching it:
the call sites previously read the environment per request, and keeping that
behaviour means a changed limit applies without a restart.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from phlo.config.base import BaseConfig

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3005,http://127.0.0.1:3005,"
    "http://localhost:4000,http://127.0.0.1:4000"
)


class ApiSettings(BaseConfig):
    """CORS, audit journal and mutation rate-limit settings for phlo-api."""

    phlo_api_cors_origins: str = Field(
        default=DEFAULT_CORS_ORIGINS,
        description=(
            "Comma-separated browser origins allowed by CORS. Use '*' to allow any "
            "origin without credentials."
        ),
    )
    phlo_api_audit_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Size in bytes at which the operation audit journal rotates (0 disables rotation)",
    )
    phlo_api_audit_max_files: int = Field(
        default=5,
        description=(
            "Rotated operation audit segments to retain (0 disables rotation; reads require 1-20)"
        ),
    )
    phlo_api_rate_limit_materialize: int = Field(
        default=10,
        description="Materialize and backfill requests allowed per principal per minute",
    )
    phlo_api_rate_limit_retry: int = Field(
        default=30, description="Failed-run retry requests allowed per principal per minute"
    )
    phlo_api_rate_limit_cancel: int = Field(
        default=60, description="Run cancel requests allowed per principal per minute"
    )
    phlo_api_rate_limit_mutation: int = Field(
        default=60, description="Other mutation requests allowed per principal per minute"
    )

    @field_validator(
        "phlo_api_audit_max_bytes",
        "phlo_api_audit_max_files",
        "phlo_api_rate_limit_materialize",
        "phlo_api_rate_limit_retry",
        "phlo_api_rate_limit_cancel",
        "phlo_api_rate_limit_mutation",
        mode="before",
    )
    @classmethod
    def _parse_integer_string(cls, value: object) -> object:
        # Preserve int(env_value): Pydantic also accepts decimal strings such as "0.0".
        return int(value) if isinstance(value, str) else value

    def cors_origins(self) -> list[str]:
        """Return the configured CORS origins with blanks removed."""
        return [
            origin.strip() for origin in self.phlo_api_cors_origins.split(",") if origin.strip()
        ]


def get_settings() -> ApiSettings:
    """Return the current phlo-api settings, read fresh from the environment."""
    return ApiSettings()
