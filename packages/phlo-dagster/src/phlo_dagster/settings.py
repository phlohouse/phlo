"""Configuration settings for Dagster orchestration.

This module defines the DagsterSettings class for configuring the
Dagster adapter behavior. Settings control executor selection,
workflow discovery paths, and service port configuration.

Configuration Sources:
    - Environment variables (PHLO_* prefix)
    - .phlo/.env and .phlo/.env.local files
    - Default values defined in DagsterSettings

Key Settings:
    - dagster_port: Webserver port (default: 10006)
    - workflows_path: User workflow discovery path (default: workflows)
    - phlo_force_in_process_executor: Force single-process execution
    - phlo_force_multiprocess_executor: Force multiprocess execution
    - phlo_host_platform: Override platform detection

Executor Selection:
    The module implements platform-aware executor selection to handle
    Docker Desktop/Colima on macOS where multiprocessing can cause
    DuckDB crashes. Priority:
    1. PHLO_FORCE_IN_PROCESS_EXECUTOR
    2. PHLO_FORCE_MULTIPROCESS_EXECUTOR
    3. PHLO_HOST_PLATFORM detection
    4. platform.system() fallback

Example:
    Accessing settings::

        from phlo_dagster.settings import get_settings

        settings = get_settings()
        port = settings.dagster_port

    Environment configuration::

        PHLO_DAGSTER_PORT=3000
        PHLO_WORKFLOWS_PATH=./custom_workflows


        Settings for the phlo_dagster workflows package, built on the shared phlo.config base/cache helpers.
        Loaded within phlo_dagster by framework and CLI-log code through get_settings().
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.process import ProcessOverrides


class DagsterSettings(BaseConfig):
    """Dagster orchestration configuration."""

    dagster_port: int = Field(default=10006, description="Dagster webserver port")
    workflows_path: str = Field(
        default="workflows",
        validation_alias=AliasChoices("PHLO_WORKFLOWS_PATH", "WORKFLOWS_PATH", "workflows_path"),
        description="Path to user workflows directory (for external projects)",
    )
    phlo_force_in_process_executor: bool = Field(
        default=False, description="Force use of in-process executor"
    )
    phlo_force_multiprocess_executor: bool = Field(
        default=False, description="Force use of multiprocess executor"
    )
    phlo_host_platform: str | None = Field(
        default=None,
        description="Host platform for executor selection (Darwin/Linux/Windows). "
        "Auto-detected in CLI; set explicitly for daemon/webserver on macOS.",
    )

    @model_validator(mode="after")
    def validate_executor_flags(self) -> "DagsterSettings":
        """Reject settings where both executor force flags are set."""
        if self.phlo_force_in_process_executor and self.phlo_force_multiprocess_executor:
            raise ValueError(
                "phlo_force_in_process_executor and phlo_force_multiprocess_executor "
                "cannot both be True"
            )
        return self


@project_root_cached
def get_settings(project_root: Path) -> DagsterSettings:
    """Return cached Dagster settings."""
    return DagsterSettings()


class DagsterProcessSettings(ProcessOverrides):
    """Read process overrides while retaining operation-local parsing and errors."""

    phlo_dagster_allowed_service_ids: Annotated[list[str], NoDecode] = Field(
        ["phlo-api"],
        validation_alias="PHLO_DAGSTER_ALLOWED_SERVICE_IDS",
        description="Comma-separated allowed service callers, stripped with blanks removed; default phlo-api. Explicit blank allows no callers.",
    )
    phlo_dagster_access_token: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_ACCESS_TOKEN",
        description="Dagster access token for CLI operations; no default.",
    )
    phlo_auto_refresh_contracts: bool = Field(
        False,
        validation_alias="PHLO_AUTO_REFRESH_CONTRACTS",
        description="Contract refresh opt-in. Stripped lowercase 1/true/yes enable (not on); default disabled.",
    )
    phlo_contract_refresh_selection: str | None = Field(
        None,
        validation_alias="PHLO_CONTRACT_REFRESH_SELECTION",
        description="Optional selection for automatic contract refresh.",
    )
    phlo_dagster_incident_location_env_map: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_INCIDENT_LOCATION_ENV_MAP",
        description="Incident location-to-environment map, validated by the incident sensor.",
    )
    phlo_incident_api_url: str | None = Field(
        None,
        validation_alias="PHLO_INCIDENT_API_URL",
        description="Incident ingestion API URL; sensor handles absent configuration.",
    )
    phlo_observatory_maintenance_execute: bool = Field(
        False,
        validation_alias="PHLO_OBSERVATORY_MAINTENANCE_EXECUTE",
        description="Maintenance execution opt-in; only exact 1 enables, otherwise dry-run.",
    )
    phlo_dagster_oidc_issuer: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_OIDC_ISSUER",
        description="OIDC issuer; stripped, default blank.",
    )
    phlo_dagster_oidc_audience: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_OIDC_AUDIENCE",
        description="OIDC audience; stripped, default blank.",
    )
    phlo_dagster_oidc_jwks_url: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_OIDC_JWKS_URL",
        description="OIDC JWKS URL; stripped, default blank.",
    )
    phlo_dagster_oidc_ca_file: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_OIDC_CA_FILE",
        description="OIDC CA file; stripped, blank means absent.",
    )
    phlo_dagster_oidc_groups_claim: str | None = Field(
        None,
        validation_alias="PHLO_DAGSTER_OIDC_GROUPS_CLAIM",
        description="OIDC groups claim; default groups, explicit blank preserved.",
    )
    phlo_dagster_oidc_allow_insecure_http: bool = Field(
        False,
        validation_alias="PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP",
        description="OIDC loopback HTTP opt-in; lowercase true enables, without trimming.",
    )
    phlo_dagster_oidc_required: bool = Field(
        False,
        validation_alias="PHLO_DAGSTER_OIDC_REQUIRED",
        description="OIDC-required override; stripped lowercase true enables. Production authorization policy remains independent.",
    )

    @field_validator("phlo_auto_refresh_contracts", mode="before")
    @classmethod
    def _refresh(cls, value: object) -> bool:
        return (
            value is True
            or isinstance(value, str)
            and value.strip().lower() in {"1", "true", "yes"}
        )

    @field_validator("phlo_dagster_allowed_service_ids", mode="before")
    @classmethod
    def _services(cls, value: object) -> object:
        return (
            [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(value, str)
            else value
        )

    @field_validator("phlo_observatory_maintenance_execute", mode="before")
    @classmethod
    def _execute(cls, value: object) -> bool:
        return value is True or value == "1"

    @field_validator(
        "phlo_dagster_oidc_allow_insecure_http", "phlo_dagster_oidc_required", mode="before"
    )
    @classmethod
    def _true(cls, value: object, info: ValidationInfo) -> bool:
        if isinstance(value, str):
            return (
                value.strip() if info.field_name == "phlo_dagster_oidc_required" else value
            ).lower() == "true"
        return value is True


def get_process_settings() -> DagsterProcessSettings:
    """Read current Dagster process overrides without dotenv or caching."""
    return DagsterProcessSettings()


class WapSensorSettings(BaseSettings):
    """Read sensor intervals at sensor module import as before."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    cleanup_interval_seconds: int = Field(
        3600,
        validation_alias="PHLO_WAP_CLEANUP_INTERVAL_SECONDS",
        description="Cleanup sensor interval, int syntax; read at sensor module import.",
    )
    promotion_interval_seconds: int = Field(
        60,
        validation_alias="PHLO_WAP_PROMOTION_INTERVAL_SECONDS",
        description="Promotion sensor interval, int syntax; read at sensor module import.",
    )

    @field_validator("cleanup_interval_seconds", "promotion_interval_seconds", mode="before")
    @classmethod
    def _integer(cls, value: object) -> object:
        return int(value) if isinstance(value, str) else value


class WapBackfillSettings(BaseSettings):
    """Read timeouts only when waiting for a WAP backfill lifecycle."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    timeout_seconds: float = Field(
        3600,
        validation_alias="PHLO_WAP_BACKFILL_TIMEOUT_SECONDS",
        description="WAP backfill wait timeout; float syntax, no additional range restriction.",
    )
    poll_seconds: float = Field(
        2,
        validation_alias="PHLO_WAP_BACKFILL_POLL_SECONDS",
        description="WAP backfill polling interval; float syntax, no additional range restriction.",
    )


class DagsterOidcTimingSettings(BaseSettings):
    """Validate OIDC timing only after the identity configuration gate."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    leeway_seconds: int = Field(
        30,
        validation_alias="PHLO_DAGSTER_OIDC_LEEWAY_SECONDS",
        description="OIDC leeway, integer seconds from 0 through 300; ignored while identity is unconfigured.",
    )
    jwks_cache_ttl_seconds: int = Field(
        300,
        validation_alias="PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS",
        description="JWKS cache TTL, integer seconds from 1 through 86400; ignored while identity is unconfigured.",
    )
    refresh_min_interval_seconds: int = Field(
        5,
        validation_alias="PHLO_DAGSTER_OIDC_REFRESH_MIN_INTERVAL_SECONDS",
        description="JWKS refresh interval, integer seconds from 1 through 300; ignored while identity is unconfigured.",
    )

    @field_validator(
        "leeway_seconds", "jwks_cache_ttl_seconds", "refresh_min_interval_seconds", mode="before"
    )
    @classmethod
    def _integer(cls, value: object, info: ValidationInfo) -> object:
        assert info.field_name is not None
        name = cls.model_fields[info.field_name].validation_alias
        try:
            parsed = int(value) if isinstance(value, str) else value
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        minimum, maximum = {
            "leeway_seconds": (0, 300),
            "jwks_cache_ttl_seconds": (1, 86400),
            "refresh_min_interval_seconds": (1, 300),
        }[info.field_name]
        if isinstance(parsed, int) and not minimum <= parsed <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return parsed


class DagsterWebsocketSettings(BaseSettings):
    """Validate websocket timeout at middleware construction, not other settings reads."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    init_timeout_seconds: float = Field(
        10.0,
        validation_alias="PHLO_DAGSTER_GRAPHQL_WS_INIT_TIMEOUT_SECONDS",
        description="Websocket connection_init timeout, float seconds greater than 0 and at most 60.",
    )

    @field_validator("init_timeout_seconds", mode="before")
    @classmethod
    def _timeout(cls, value: object) -> object:
        name = "PHLO_DAGSTER_GRAPHQL_WS_INIT_TIMEOUT_SECONDS"
        try:
            parsed = float(value) if isinstance(value, str) else value
        except ValueError as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if isinstance(parsed, float | int) and not 0 < parsed <= 60:
            raise ValueError(f"{name} must be greater than 0 and at most 60")
        return parsed
