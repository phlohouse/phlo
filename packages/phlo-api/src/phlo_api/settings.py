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

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from phlo.config.base import BaseConfig
from phlo.config.process import ProcessOverrides

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


class ApiDeploymentSettings(BaseSettings):
    """Process-only deployment assertions, not project dotenv configuration.

    These gates attest to running infrastructure. Keep exact, case-sensitive
    environment keys and values rather than accepting general boolean syntax.
    """

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

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


class ApiProcessSettings(ProcessOverrides):
    """Read process overrides for decoding and validation by the selected API operation."""

    phlo_api_tokens: str | None = Field(
        None,
        validation_alias="PHLO_API_TOKENS",
        description="JSON API token map. Missing/blank disables configured tokens; validation stays in the authentication request boundary.",
    )
    phlo_maintenance_read_model: str | None = Field(
        None,
        validation_alias="PHLO_MAINTENANCE_READ_MODEL",
        description="Maintenance read-model capability name; absent uses capability selection.",
    )
    phlo_observability_backend: str | None = Field(
        None,
        validation_alias="PHLO_OBSERVABILITY_BACKEND",
        description="Observability backend name after explicit argument; absent uses capability selection.",
    )
    phlo_lineage_sink: str | None = Field(
        None,
        validation_alias="PHLO_LINEAGE_SINK",
        description="Lineage sink capability name; absent uses capability selection.",
    )
    phlo_v1_branch_check_jobs: str | None = Field(
        None,
        validation_alias="PHLO_V1_BRANCH_CHECK_JOBS",
        description="JSON branch-check job configuration, validated at use.",
    )
    phlo_v1_git_review_repository: str | None = Field(
        None,
        validation_alias="PHLO_V1_GIT_REVIEW_REPOSITORY",
        description="Required GitHub review repository; absent fails closed in review configuration.",
    )
    phlo_v1_git_review_base_branch: str | None = Field(
        None,
        validation_alias="PHLO_V1_GIT_REVIEW_BASE_BRANCH",
        description="Required Git review base branch; no inferred default.",
    )
    phlo_v1_git_review_token: str | None = Field(
        None,
        validation_alias="PHLO_V1_GIT_REVIEW_TOKEN",
        description="Required Git review credential; no development default.",
    )
    phlo_v1_maintenance_windows: str | None = Field(
        None,
        validation_alias="PHLO_V1_MAINTENANCE_WINDOWS",
        description="JSON maintenance-window configuration, validated at the request boundary.",
    )
    phlo_promotion_dagster_check_jobs: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="PHLO_PROMOTION_DAGSTER_CHECK_JOBS",
        description="Comma-separated promotion check jobs, stripped while preserving blank positions for status validation. Default unconfigured. Launch validation filters blanks and requires three distinct job names in tests/contracts/audits order.",
    )
    phlo_promotion_prod_worktree: str | None = Field(
        None,
        validation_alias="PHLO_PROMOTION_PROD_WORKTREE",
        description="Required production worktree for staging promotion.",
    )
    phlo_promotion_staging_worktree: str | None = Field(
        None,
        validation_alias="PHLO_PROMOTION_STAGING_WORKTREE",
        description="Required staging worktree for promotion.",
    )
    phlo_promotion_prod_ref: str | None = Field(
        None,
        validation_alias="PHLO_PROMOTION_PROD_REF",
        description="Required production promotion ref; must agree with the environment contract.",
    )
    phlo_promotion_staging_ref: str | None = Field(
        None,
        validation_alias="PHLO_PROMOTION_STAGING_REF",
        description="Required staging promotion ref; must agree with the environment contract.",
    )
    phlo_promotion_dagster_location: str | None = Field(
        None,
        validation_alias="PHLO_PROMOTION_DAGSTER_LOCATION",
        description="Required promotion Dagster location; must agree with the environment contract.",
    )
    phlo_compose_project: str | None = Field(
        None,
        validation_alias="PHLO_COMPOSE_PROJECT",
        description="Compose project override before COMPOSE_PROJECT_NAME and project discovery.",
    )
    phlo_workflow_wizard_secret: str | None = Field(
        None,
        validation_alias="PHLO_WORKFLOW_WIZARD_SECRET",
        description="Workflow wizard signing secret; absent uses persisted project-local secret.",
    )
    phlo_uv_project: str | None = Field(
        None,
        validation_alias="PHLO_UV_PROJECT",
        description="Package-install UV project override before UV_PROJECT and project discovery.",
    )
    phlo_v1_preview_catalogs: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_CATALOGS",
        description="Required JSON environment-to-preview catalog map; validated at use.",
    )
    phlo_v1_preview_trino_password_prod: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD",
        description="Required production preview Trino credential; absent/blank fails preview configuration.",
    )
    phlo_v1_preview_trino_password_staging: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING",
        description="Required staging preview Trino credential; absent/blank fails preview configuration.",
    )
    phlo_v1_usage_trino_sources: str | None = Field(
        None,
        validation_alias="PHLO_V1_USAGE_TRINO_SOURCES",
        description="Required JSON usage-source configuration; validated at use.",
    )

    @field_validator("phlo_promotion_dagster_check_jobs", mode="before")
    @classmethod
    def _check_jobs(cls, value: object) -> object:
        return [part.strip() for part in value.split(",")] if isinstance(value, str) else value


def get_process_settings() -> ApiProcessSettings:
    """Read API process overrides afresh without loading project dotenv files."""
    return ApiProcessSettings()
