"""Typed settings for the phlo-api service.

Declares the ``PHLO_API_*`` environment variables the service reads, so each
one has a single type, default and description and appears in the generated
settings reference. Values come from the process environment and the
project's generated ``.phlo`` env files, as for every other ``BaseConfig``.
Deployment assertions use a separate process-only model to preserve their
original precedence and exact enabling values.

``get_settings`` builds a fresh model on each call rather than caching it:
the call sites previously read the environment per request, and keeping that
behaviour means a changed limit applies without a restart.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from phlo.config.base import BaseConfig

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3005,http://127.0.0.1:3005,"
    "http://localhost:4000,http://127.0.0.1:4000"
)


class ApiSettings(BaseConfig):
    """CORS, audit journal and mutation rate-limit settings for phlo-api."""

    phlo_api_operation_controls_db_url: str | None = Field(
        default=None,
        description="PostgreSQL DSN for shared API idempotency, exclusion and rate limits",
    )
    phlo_api_operation_controls_namespace: str = Field(
        default="",
        description="Stable project identity shared by every API replica; required with the controls DSN",
    )
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


class ApiDeploymentSettings(BaseSettings):
    """Process-only deployment assertions, not project dotenv configuration.

    These gates attest to running infrastructure. Keep exact, case-sensitive
    environment keys and values rather than accepting general boolean syntax.
    """

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    workers: int = Field(
        1,
        ge=1,
        validation_alias="WEB_CONCURRENCY",
        description="Configured API worker count; local operation controls require one worker",
    )
    actions_single_replica: bool = Field(
        False,
        validation_alias="PHLO_V1_ACTIONS_SINGLE_REPLICA",
        description="Process-only single API replica assertion for v1 actions. Only '1' enables.",
    )
    actions_single_process: bool = Field(
        False,
        validation_alias="PHLO_V1_ACTIONS_SINGLE_PROCESS",
        description="Process-only single API process assertion for v1 actions. Only '1' enables.",
    )
    actions_ref_tag_contract: bool = Field(
        False,
        validation_alias="PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
        description="Process-only environment-pinned ref/tag contract assertion. Only '1' enables.",
    )
    query_single_replica: bool = Field(
        False,
        validation_alias="PHLO_V1_QUERY_SINGLE_REPLICA",
        description="Process-only single API replica assertion for query workspace. Only '1' enables.",
    )
    preview_server_limits_configured: bool = Field(
        False,
        validation_alias="PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED",
        description="Process-only assertion of configured Trino preview server limits. Only '1' enables.",
    )
    staging_single_replica: bool = Field(
        False,
        validation_alias="PHLO_STAGING_SINGLE_REPLICA",
        description="Process-only single API replica assertion for staging promotion. Only 'true' enables.",
    )

    @field_validator(
        "actions_single_replica",
        "actions_single_process",
        "actions_ref_tag_contract",
        "query_single_replica",
        "preview_server_limits_configured",
        mode="before",
    )
    @classmethod
    def _parse_one(cls, value: object) -> bool:
        return value is True or value == "1"

    @field_validator("staging_single_replica", mode="before")
    @classmethod
    def _parse_true(cls, value: object) -> bool:
        return value is True or value == "true"


def get_deployment_settings() -> ApiDeploymentSettings:
    """Read deployment assertions afresh for each request evaluation."""
    return ApiDeploymentSettings()
