"""Typed, fresh process overrides for configuration resolved by runtime consumers.

These values do not load dotenv files. Safe scalar/list parsing belongs here;
consumers combine overrides with YAML, explicit arguments and deployment defaults.
JSON wire contracts retain their operation-local validators and error boundaries.
An empty override is not the same as an absent override. Do not cache these models.
"""

from __future__ import annotations

from copy import copy
from typing import Annotated, cast, overload

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, NoDecode, SettingsConfigDict


def process_override(
    model: type[BaseSettings], name: str, *, alias: str | None = None
) -> str | None:
    """Read an exact process key from its owning declaration, without defaults or dotenv.

    Resolved logging models also accept secondary aliases and have defaults.
    A raw projection must not apply either, or blank/missing security overrides
    would acquire logging's precedence. No second setting is declared here.
    """
    field = copy(model.model_fields[name])
    declared_alias = field.validation_alias
    primary = (
        declared_alias.choices[0] if isinstance(declared_alias, AliasChoices) else declared_alias
    )
    field.validation_alias = alias or (primary if isinstance(primary, str) else name.upper())
    return cast(
        str | None, EnvSettingsSource(model, case_sensitive=True).get_field_value(field, name)[0]
    )


class ProcessOverrides(BaseSettings):
    """Read case-sensitive process overrides while preserving missing and empty values."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)

    def __getitem__(self, name: str) -> str:
        field = type(self).model_fields.get(name.lower())
        if field is not None and field.validation_alias != name:
            raise KeyError(name)
        value: str | None = getattr(self, name.lower(), None)
        if value is None:
            raise KeyError(name)
        if not isinstance(value, str):
            raise TypeError(f"Use the typed {name.lower()} field directly")
        return value

    def __contains__(self, name: str) -> bool:
        try:
            self[name]
        except KeyError:
            return False
        return True

    @overload
    def get(self, name: str) -> str | None: ...

    @overload
    def get[T](self, name: str, default: T) -> str | T: ...

    def get[T](self, name: str, default: T | None = None) -> str | T | None:
        """Resolve an override without replacing an explicitly empty string."""
        try:
            return self[name]
        except KeyError:
            return default


class CoreProcessSettings(ProcessOverrides):
    """Read core process overrides without replacing contextual consumer fallbacks."""

    @property
    def phlo_environment(self) -> str | None:
        """Project the core Settings declaration without its logging fallbacks."""
        from phlo.config.settings import Settings

        return process_override(Settings, "phlo_environment")

    @property
    def phlo_project(self) -> str | None:
        """Project the core Settings declaration without loading dotenv."""
        from phlo.config.settings import Settings

        return process_override(Settings, "phlo_project")

    @property
    def phlo_lineage_db_url(self) -> str | None:
        """Read only the namespaced shared database alias for the fallback chain."""
        return process_override(
            LineageDatabaseSettings, "lineage_db_url", alias="PHLO_LINEAGE_DB_URL"
        )

    phlo_observatory_environment: str | None = Field(
        None,
        validation_alias="PHLO_OBSERVATORY_ENVIRONMENT",
        description="Explicit prod/staging operational target for sensors; no inferred default.",
    )
    phlo_maintenance_policy_path: str | None = Field(
        None,
        validation_alias="PHLO_MAINTENANCE_POLICY_PATH",
        description="Maintenance policy file override; default maintenance_policy.yaml.",
    )
    phlo_service_nonce_db_url: str | None = Field(
        None,
        validation_alias="PHLO_SERVICE_NONCE_DB_URL",
        description="Service replay-prevention database DSN, before run-evidence DSN fallback.",
    )
    phlo_project_path: str | None = Field(
        None,
        validation_alias="PHLO_PROJECT_PATH",
        description="Process project-root override. Otherwise use the caller's root or working directory; API deployment defaults may use /app/project.",
    )
    phlo_authentication_method: str | None = Field(
        None,
        validation_alias="PHLO_AUTHENTICATION_METHOD",
        description="Authentication method override, stripped before YAML fallback.",
    )
    phlo_authentication_provider: str | None = Field(
        None,
        validation_alias="PHLO_AUTHENTICATION_PROVIDER",
        description="Authentication provider override, stripped before YAML fallback.",
    )
    phlo_authorization_backend: str | None = Field(
        None,
        validation_alias="PHLO_AUTHORIZATION_BACKEND",
        description="Authorization backend override, stripped before YAML fallback.",
    )
    phlo_authorization_mode: str | None = Field(
        None,
        validation_alias="PHLO_AUTHORIZATION_MODE",
        description="HTTP authorization mode override. Consumers preserve production fail-closed validation.",
    )
    phlo_auth_dev_mode: bool | None = Field(
        None,
        validation_alias="PHLO_AUTH_DEV_MODE",
        description="Static development authentication override; lowercase 1/true/yes enable. Blank falls back to YAML; blocked in production and regulated mode.",
    )
    phlo_auth_static_enabled: bool = Field(
        False,
        validation_alias="PHLO_AUTH_STATIC_ENABLED",
        description="Static-provider enabling override; any nonempty value, including false, selects the provider before YAML.",
    )
    phlo_auth_static_users: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_STATIC_USERS",
        description="JSON static-user override; invalid JSON retains the YAML user map.",
    )
    phlo_auth_proxy_enabled: bool = Field(
        False,
        validation_alias="PHLO_AUTH_PROXY_ENABLED",
        description="Proxy-provider enabling override; any nonempty value selects the provider before YAML.",
    )
    phlo_auth_proxy_trusted_proxies: Annotated[list[str] | None, NoDecode] = Field(
        None,
        validation_alias="PHLO_AUTH_PROXY_TRUSTED_PROXIES",
        description="Comma-separated trusted proxies, stripped individually without dropping empty entries; absent/blank falls back to YAML, then loopback defaults.",
    )
    phlo_auth_proxy_header_subject: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_PROXY_HEADER_SUBJECT",
        description="Proxy subject header override; blank falls back to YAML.",
    )
    phlo_auth_proxy_header_email: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_PROXY_HEADER_EMAIL",
        description="Proxy email header override; blank falls back to YAML.",
    )
    phlo_auth_proxy_header_groups: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_PROXY_HEADER_GROUPS",
        description="Proxy groups header override; blank falls back to YAML.",
    )
    phlo_auth_proxy_shared_secret: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_PROXY_SHARED_SECRET",
        description="Proxy shared-secret override; blank falls back to YAML.",
    )
    phlo_auth_service_enabled: bool = Field(
        False,
        validation_alias="PHLO_AUTH_SERVICE_ENABLED",
        description="Service-token provider override; any nonempty value selects the provider before YAML.",
    )
    phlo_auth_service_tokens: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_SERVICE_TOKENS",
        description="JSON service-token map override. Invalid JSON or missing explicit subjects fail validation.",
    )
    phlo_auth_jwt_enabled: bool = Field(
        False,
        validation_alias="PHLO_AUTH_JWT_ENABLED",
        description="JWT provider override; any nonempty value selects the provider before YAML.",
    )
    phlo_auth_jwt_secret: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_SECRET",
        description="JWT secret override; presence including blank takes precedence over YAML.",
    )
    phlo_auth_jwt_issuer: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_ISSUER",
        description="JWT issuer override; presence including blank takes precedence over YAML.",
    )
    phlo_auth_jwt_audience: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_AUDIENCE",
        description="JWT audience override; presence including blank takes precedence over YAML.",
    )
    phlo_auth_jwt_jwks_url: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_JWKS_URL",
        description="JWT JWKS URL override; presence including blank takes precedence over YAML.",
    )
    phlo_auth_jwt_groups_claim: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_GROUPS_CLAIM",
        description="JWT groups claim override; final default groups.",
    )
    phlo_auth_jwt_ca_file: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_CA_FILE",
        description="JWT CA-file override; blank clears the YAML value.",
    )
    phlo_auth_jwt_allow_insecure_http: bool | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_ALLOW_INSECURE_HTTP",
        description="JWT loopback HTTP override; stripped lowercase 1/true/yes/on enable; YAML then disabled.",
    )
    phlo_auth_subject: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_SUBJECT",
        description="CLI principal subject; absent uses service-account or development identity resolution.",
    )
    phlo_auth_type: str | None = Field(
        None,
        validation_alias="PHLO_AUTH_TYPE",
        description="CLI principal type; default user for resolution, unknown for audit attribution.",
    )
    phlo_auth_groups: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="PHLO_AUTH_GROUPS",
        description="CLI principal groups, comma-separated, stripped with blanks removed; default empty.",
    )
    phlo_service_account: str | None = Field(
        None,
        validation_alias="PHLO_SERVICE_ACCOUNT",
        description="CLI service-account identity override.",
    )
    phlo_dev_mode: bool = Field(
        False,
        validation_alias="PHLO_DEV_MODE",
        description="CLI local development identity fallback; any nonempty value opts in, including false; production checks still apply.",
    )
    phlo_request_id: str | None = Field(
        None,
        validation_alias="PHLO_REQUEST_ID",
        description="CLI request correlation identifier override.",
    )
    phlo_container_backend: str | None = Field(
        None,
        validation_alias="PHLO_CONTAINER_BACKEND",
        description="Container backend override after explicit CLI argument, before project config; final default docker.",
    )
    phlo_operations_journal_dir: str | None = Field(
        None,
        validation_alias="PHLO_OPERATIONS_JOURNAL_DIR",
        description="Operation journal directory override; absent uses each consumer's project-local journal.",
    )
    phlo_trivy_cache_dir: str | None = Field(
        None,
        validation_alias="PHLO_TRIVY_CACHE_DIR",
        description="Trivy cache directory override for plugin checks.",
    )
    phlo_dev_source: str | None = Field(
        None,
        validation_alias="PHLO_DEV_SOURCE",
        description="Development source checkout override for generated services.",
    )
    phlo_audit_hmac_key: str | None = Field(
        None,
        validation_alias="PHLO_AUDIT_HMAC_KEY",
        description="Audit HMAC key, also fallback for evidence and signature keys. No deployed default.",
    )
    phlo_evidence_hmac_key: str | None = Field(
        None,
        validation_alias="PHLO_EVIDENCE_HMAC_KEY",
        description="Evidence HMAC key; blank falls back to audit key.",
    )
    phlo_signature_hmac_key: str | None = Field(
        None,
        validation_alias="PHLO_SIGNATURE_HMAC_KEY",
        description="Signature HMAC key; blank falls back to audit key.",
    )
    phlo_dataset_state_store: str | None = Field(
        None,
        validation_alias="PHLO_DATASET_STATE_STORE",
        description="Dataset-state storage override after explicit argument; final default durable.",
    )
    phlo_identity_authority_enabled: bool = Field(
        False,
        validation_alias="PHLO_IDENTITY_AUTHORITY_ENABLED",
        description="Identity-authority deployment opt-in; only exact 1 enables.",
    )
    phlo_deployment_id: str | None = Field(
        None,
        validation_alias="PHLO_DEPLOYMENT_ID",
        description="Deployment identity override, stripped; blank falls back to project and hostname.",
    )
    phlo_project_name: str | None = Field(
        None,
        validation_alias="PHLO_PROJECT_NAME",
        description="Project name override. Operation identity strips it; API resource fallback is project.",
    )
    phlo_v1_environments: str | None = Field(
        None,
        validation_alias="PHLO_V1_ENVIRONMENTS",
        description="Required JSON map of operational environment targets; validated by the environment contract at use.",
    )
    phlo_run_evidence_db_url: str | None = Field(
        None,
        validation_alias="PHLO_RUN_EVIDENCE_DB_URL",
        description="Run-evidence PostgreSQL DSN; required in production, staging and regulated environments.",
    )
    phlo_run_evidence_sqlite_path: str | None = Field(
        None,
        validation_alias="PHLO_RUN_EVIDENCE_SQLITE_PATH",
        description="Local run-evidence SQLite path; default .phlo/run-evidence.sqlite.",
    )
    phlo_regulated: bool | None = Field(
        None,
        validation_alias="PHLO_REGULATED",
        description="Regulated-mode override. Stripped lowercase 1/true/yes/on or 0/false/no/off; unknown falls through to configuration.",
    )
    phlo_regulated_mode: str | None = Field(
        None,
        validation_alias="PHLO_REGULATED_MODE",
        description="Deprecated regulated-mode override, removed in 0.19.0; explicit config and PHLO_REGULATED take precedence.",
    )
    phlo_service_credentials_file: str | None = Field(
        None,
        validation_alias="PHLO_SERVICE_CREDENTIALS_FILE",
        description="Scoped service-identity JSON credentials file; must be an owner-controlled regular file.",
    )
    phlo_service_secret: str | None = Field(
        None,
        validation_alias="PHLO_SERVICE_SECRET",
        description="Legacy shared service-token secret, allowed only in local development; no scheduled removal release is declared.",
    )
    phlo_enabled_services: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="PHLO_ENABLED_SERVICES",
        description="Comma-separated enabled service names used by security validation, stripped with blanks removed; default empty, combined with project enabled/disabled selections.",
    )
    phlo_observability_public_host: str | None = Field(
        None,
        validation_alias="PHLO_OBSERVABILITY_PUBLIC_HOST",
        description="Public observability host; default localhost.",
    )
    phlo_observability_public_scheme: str | None = Field(
        None,
        validation_alias="PHLO_OBSERVABILITY_PUBLIC_SCHEME",
        description="Public observability URL scheme; default http.",
    )
    phlo_telemetry_path: str | None = Field(
        None,
        validation_alias="PHLO_TELEMETRY_PATH",
        description="Telemetry JSONL path override; blank leaves the capability's default path.",
    )
    phlo_registry_db_url: str | None = Field(
        None,
        validation_alias="PHLO_REGISTRY_DB_URL",
        description="Schema registry database override, before lineage and Dagster database fallback.",
    )

    @field_validator("phlo_auth_proxy_trusted_proxies", mode="before")
    @classmethod
    def _proxies(cls, value: object) -> object:
        return (
            [part.strip() for part in value.split(",")]
            if isinstance(value, str) and value
            else value or None
        )

    @field_validator("phlo_auth_groups", "phlo_enabled_services", mode="before")
    @classmethod
    def _groups(cls, value: object) -> object:
        return (
            [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(value, str)
            else value
        )

    @field_validator("phlo_auth_jwt_allow_insecure_http", mode="before")
    @classmethod
    def _insecure_http(cls, value: object) -> object:
        return (
            value.strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else value
        )

    @field_validator(
        "phlo_auth_static_enabled",
        "phlo_auth_proxy_enabled",
        "phlo_auth_service_enabled",
        "phlo_auth_jwt_enabled",
        "phlo_dev_mode",
        mode="before",
    )
    @classmethod
    def _nonempty(cls, value: object) -> bool:
        return bool(value)

    @field_validator("phlo_identity_authority_enabled", mode="before")
    @classmethod
    def _one(cls, value: object) -> bool:
        return value is True or value == "1"

    @field_validator("phlo_auth_dev_mode", mode="before")
    @classmethod
    def _dev_mode(cls, value: object) -> bool | None:
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes"} if value else None
        return value if isinstance(value, bool) else None

    @field_validator("phlo_regulated", mode="before")
    @classmethod
    def _regulated(cls, value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return None


def get_process_settings() -> CoreProcessSettings:
    """Read core process overrides afresh without adding dotenv precedence."""
    return CoreProcessSettings()


class LineageDatabaseSettings(BaseSettings):
    """Declare the database setting shared by lineage config and registry projections."""

    lineage_db_url: str | None = Field(
        None,
        validation_alias=AliasChoices(
            "LINEAGE_DB_URL", "PHLO_LINEAGE_DB_URL", "DAGSTER_PG_DB_CONNECTION_STRING"
        ),
        description="Lineage database DSN. Resolved project config selects the first present alias including blank; fresh process resolvers skip blank in this order. Registry uses PHLO_REGISTRY_DB_URL, then only PHLO_LINEAGE_DB_URL, then DAGSTER_PG_DB_CONNECTION_STRING. Default absent; PostgreSQL connection fallback is consumer-specific.",
    )


class RunEvidencePoolSettings(BaseSettings):
    """Read pool capacity on the first non-injected PostgreSQL connection."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    max_connections: int = Field(
        10,
        validation_alias="PHLO_RUN_EVIDENCE_POOL_MAX",
        description="PostgreSQL pool capacity, int syntax; clamped to at least 1. Invalid overrides fail only at first pool connection, not for injected factories or SQLite.",
    )

    @field_validator("max_connections", mode="before")
    @classmethod
    def _capacity(cls, value: object) -> object:
        return max(1, int(value)) if isinstance(value, str) else value


class JwtTimingSettings(BaseSettings):
    """Read optional typed timings at authentication-provider configuration."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    phlo_auth_jwt_leeway: int | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_LEEWAY",
        description="JWT integer leeway override; absent uses YAML then 60 seconds. Invalid strings fail at authentication-provider configuration, not unrelated core settings reads.",
    )
    phlo_auth_jwt_jwks_cache_ttl_seconds: int | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_JWKS_CACHE_TTL_SECONDS",
        description="JWT integer cache TTL override; absent uses YAML then 300 seconds. Read at authentication-provider configuration.",
    )
    phlo_auth_jwt_refresh_min_interval_seconds: int | None = Field(
        None,
        validation_alias="PHLO_AUTH_JWT_REFRESH_MIN_INTERVAL_SECONDS",
        description="JWT integer refresh interval override; absent uses YAML then 5 seconds. Read at authentication-provider configuration.",
    )

    @field_validator(
        "phlo_auth_jwt_leeway",
        "phlo_auth_jwt_jwks_cache_ttl_seconds",
        "phlo_auth_jwt_refresh_min_interval_seconds",
        mode="before",
    )
    @classmethod
    def _integer(cls, value: object, info: ValidationInfo) -> object:
        assert info.field_name is not None
        try:
            return int(value) if isinstance(value, str) else value
        except ValueError as exc:
            name = cls.model_fields[info.field_name].validation_alias
            raise ValueError(f"{name} must be an integer") from exc


class PluginDiscoverySettings(BaseSettings):
    """Evaluate bootstrap controls at the plugin discovery decision boundary."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    no_auto_discover: bool = Field(
        False,
        validation_alias="PHLO_NO_AUTO_DISCOVER",
        description="Bootstrap auto-discovery override (also set by phlo init). Stripped lowercase 0/false/no/off/blank permit discovery; any other value disables, with a warning for unknown tokens. Default false; plugins_enabled remains independent.",
    )

    @field_validator("no_auto_discover", mode="before")
    @classmethod
    def _disabled(cls, value: object) -> bool:
        if not isinstance(value, str):
            return bool(value)
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        if normalized not in {"1", "true", "yes", "on"}:
            from phlo.logging import get_logger

            get_logger("phlo.plugins.discovery._plugin_auto_discovery").warning(
                "plugin_auto_discover_env_invalid",
                env_var="PHLO_NO_AUTO_DISCOVER",
                value=value,
                hint="Use PHLO_NO_AUTO_DISCOVER=1 to disable auto-discovery",
            )
        return True
