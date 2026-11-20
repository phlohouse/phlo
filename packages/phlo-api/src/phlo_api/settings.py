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

from pathlib import Path

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
    query_single_process: bool = Field(
        False,
        validation_alias="PHLO_V1_QUERY_SINGLE_PROCESS",
        description="Explicit single API process assertion; active query sessions are process-local.",
    )
    web_concurrency: int = Field(1, validation_alias="WEB_CONCURRENCY", ge=1)
    notebook_kernels: bool = Field(
        False,
        validation_alias="PHLO_NOTEBOOK_KERNELS",
        description=(
            "Process-only opt-in for Observatory notebook kernels, which run Python in the "
            "phlo-notebook-kernels service. Only '1' enables."
        ),
    )
    notebook_kernel_dir: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_KERNEL_DIR",
        description=(
            "Private folder for kernel records and sockets. "
            "Absent uses a per-user folder in the system temp directory."
        ),
    )
    notebook_packages: bool = Field(
        False,
        validation_alias="PHLO_NOTEBOOK_PACKAGES",
        description=(
            "Process-only opt-in letting notebooks list Python packages, installed into "
            "per-package-set environments in the kernel host. Only '1' enables."
        ),
    )
    notebook_gateway_url: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_GATEWAY_URL",
        description=(
            "Notebook kernel host URL, such as http://phlo-notebook-kernels:8890 inside the stack "
            "or http://127.0.0.1:10090 from the host. Absent leaves kernels unavailable."
        ),
    )
    notebook_gateway_token_value: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_GATEWAY_TOKEN",
        description="Bearer token the notebook kernel host requires; generated by phlo services init.",
    )
    notebook_gateway_token_file: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_GATEWAY_TOKEN_FILE",
        description="File holding the notebook kernel host token; wins over the variable.",
    )
    notebook_schedule_key_value: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_SCHEDULE_KEY",
        description=(
            "Key the API signs notebook schedules with; generated by phlo services init. "
            "Absent leaves schedules unavailable."
        ),
    )
    notebook_schedule_key_file: str | None = Field(
        None,
        validation_alias="PHLO_NOTEBOOK_SCHEDULE_KEY_FILE",
        description="File holding the notebook schedule key; wins over the variable.",
    )
    notebook_max_rows: int = Field(
        10_000,
        ge=1,
        le=100_000,
        validation_alias="PHLO_NOTEBOOK_MAX_ROWS",
        description="Most rows a notebook Trino cell loads into its kernel.",
    )
    notebook_cell_timeout: int = Field(
        1800,
        ge=10,
        le=6 * 3600,
        validation_alias="PHLO_NOTEBOOK_CELL_TIMEOUT",
        description="Seconds a notebook cell may run before it is interrupted.",
    )
    notebook_output_days: int = Field(
        14,
        ge=1,
        le=365,
        validation_alias="PHLO_NOTEBOOK_OUTPUT_DAYS",
        description="Days a notebook cell's last output is kept for reopening.",
    )
    notebook_schedules: bool = Field(
        False,
        validation_alias="PHLO_NOTEBOOK_SCHEDULES",
        description=(
            "Process-only opt-in for running notebooks on their saved cron schedules. "
            "Only '1' enables."
        ),
    )
    notebook_git_commit: bool = Field(
        False,
        validation_alias="PHLO_NOTEBOOK_GIT_COMMIT",
        description="Process-only opt-in for committing notebooks to git from Observatory. Only '1' enables.",
    )
    notebook_git_protected_branches: str = Field(
        "main,master",
        validation_alias="PHLO_NOTEBOOK_GIT_PROTECTED_BRANCHES",
        description="Comma-separated branches notebooks are never committed to from Observatory.",
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

    def notebook_gateway_token(self) -> str | None:
        """The kernel host token, reread from its file on each use so it can rotate."""
        if self.notebook_gateway_token_file:
            try:
                return Path(self.notebook_gateway_token_file).read_text(encoding="utf-8").strip()
            except OSError:
                return None
        return self.notebook_gateway_token_value or None

    def notebook_schedule_key(self) -> str | None:
        """The schedule signing key, at least 32 characters, or ``None``."""
        key = self.notebook_schedule_key_value or None
        if self.notebook_schedule_key_file:
            try:
                key = Path(self.notebook_schedule_key_file).read_text(encoding="utf-8").strip()
            except OSError:
                return None
        return key if key and len(key) >= 32 else None

    @field_validator(
        "actions_single_replica",
        "actions_single_process",
        "actions_ref_tag_contract",
        "query_single_replica",
        "query_single_process",
        "preview_server_limits_configured",
        "notebook_kernels",
        "notebook_packages",
        "notebook_schedules",
        "notebook_git_commit",
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
    phlo_v1_preview_trino_url: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_TRINO_URL",
        description="Dedicated authenticated HTTPS preview coordinator, never the ingestion URL fallback.",
    )
    phlo_v1_preview_trino_ca_file: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_TRINO_CA_FILE",
        description="Preview trusted PEM CA bundle; absent uses platform trust, never disables verification.",
    )
    phlo_v1_preview_trino_password_files: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_TRINO_PASSWORD_FILES",
        description="JSON configured-environment to private password file map, reread for rotation.",
    )
    phlo_v1_preview_state_dir: str | None = Field(
        None,
        validation_alias="PHLO_V1_PREVIEW_STATE_DIR",
        description="Private folder where the API makes the generated local preview Trino's TLS and passwords.",
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
