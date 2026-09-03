"""Production readiness preflight.

Evaluates whether a selected service set satisfies the v1 production trust and
readiness contract (ADR 0047). The report is read-only, stable,
JSON-serializable, deterministic, and secret-free.

State vocabulary (ADR 0047, decision 4):

- ``passed``: configured state is verified safe for this prerequisite.
- ``failed``: configured state is definitively unsafe or contradictory.
- ``unavailable``: required evidence cannot be obtained yet (for example a
  contributor that lands in Plans 004-005). For production-required checks both
  ``failed`` and ``unavailable`` fail the report; a check is never optimistically
  passed.
- ``not_applicable``: the selected stack genuinely does not use this component.

This module implements only facts inspectable from generated files, selected
services, filesystem metadata, and registered neutral declarations. It never
imports provider packages, never mutates files or policy, and never contacts a
container backend or a network service.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

# ---------------------------------------------------------------------------
# Closed vocabulary
# ---------------------------------------------------------------------------


class ProductionReadinessState(StrEnum):
    """Closed readiness state vocabulary."""

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class ProductionReadinessCheckId(StrEnum):
    """Closed set of production readiness check identifiers."""

    ENV_PRODUCTION = "env.production"
    COMPOSE_NON_DEV = "compose.non_dev"
    HTTP_AUTHORIZATION_REQUIRED = "http.authorization_required"
    AUTHN_PROVIDER = "authn.provider"
    AUTHZ_BACKEND = "authz.backend"
    TLS_EXTERNAL_ENDPOINT = "tls.external_endpoint"
    OIDC_ISSUER_AUDIENCE_JWKS = "oidc.issuer_audience_jwks"
    IDENTITY_WORKLOAD_API = "identity.workload.api"
    IDENTITY_WORKLOAD_ORCHESTRATION = "identity.workload.orchestration"
    IDENTITY_WORKLOAD_QUERY = "identity.workload.query"
    IDENTITY_WORKLOAD_CATALOG = "identity.workload.catalog"
    IDENTITY_WORKLOAD_MAINTENANCE = "identity.workload.maintenance"
    AUDIT_KEY_BACKEND = "audit.key_backend"
    POLICY_COMPILED_VERIFICATION = "policy.compiled_verification"
    SECRETS_NO_BUNDLED_SHARED = "secrets.no_bundled_shared"
    SECRETS_ENV_LOCAL_0600 = "secrets.env_local_0600"
    NETWORK_PROTECTED_PORTS = "network.protected_ports"


class ProductionReadinessReasonCode(StrEnum):
    """Closed set of reasons a readiness check did not pass (ADR 0047 §7.3)."""

    OK = "ok"
    NOT_APPLICABLE = "not_applicable"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    NOT_PRODUCTION = "not_production"
    DEV_COMPOSE = "dev_compose"
    AUTH_BYPASS_IN_PRODUCTION = "auth_bypass_in_production"
    AUTHN_MISSING = "authn_missing"
    AUTHN_PARTIAL = "authn_partial"
    AUTHN_DEV_ONLY = "authn_dev_only"
    AUTHZ_MISSING = "authz_missing"
    AUTHZ_NOT_REGISTERED = "authz_not_registered"
    TLS_NOT_REPRESENTED = "tls_not_represented"
    OIDC_MISSING = "oidc_missing"
    OIDC_NO_VERIFICATION_MATERIAL = "oidc_no_verification_material"
    AUDIT_KEY_MISSING = "audit_key_missing"
    POLICY_UNLOADABLE = "policy_unloadable"
    POLICY_MISSING = "policy_missing"
    CREDENTIALS_BUNDLED_OR_SHARED = "credentials_bundled_or_shared"
    SECRET_MODE_PERMISSIVE = "secret_mode_permissive"
    SECRET_MISSING = "secret_missing"
    PROTECTED_PORTS_EXPOSED = "protected_ports_exposed"


# Backends the production profile removes from public host interfaces.
_PROTECTED_SERVICES = frozenset({"postgres", "minio", "nessie", "trino"})

# Credential defaults that are rejected in production (mirrors
# phlo.cli.commands.services.init and phlo.plugins.compose.generator).
_PRODUCTION_USERNAME_DEFAULTS = {
    "POSTGRES_USER": "phlo",
    "MINIO_ROOT_USER": "minio",
}
_PRODUCTION_PASSWORD_DEFAULTS = {
    "POSTGRES_PASSWORD": "phlo",
    "MINIO_ROOT_PASSWORD": "minio123",
}

# Authentication provider environment keys (capabilities/authentication.py).
_PHLO_ENVIRONMENT_ENV = "PHLO_ENVIRONMENT"
_AUTH_DEV_MODE_ENV = "PHLO_AUTH_DEV_MODE"
_AUTH_JWT_SECRET_ENV = "PHLO_AUTH_JWT_SECRET"
_AUTH_JWT_ISSUER_ENV = "PHLO_AUTH_JWT_ISSUER"
_AUTH_JWT_AUDIENCE_ENV = "PHLO_AUTH_JWT_AUDIENCE"
_AUTH_JWT_JWKS_URL_ENV = "PHLO_AUTH_JWT_JWKS_URL"
_AUTH_PROXY_PREFIX = "PHLO_AUTH_PROXY_"
_AUTH_STATIC_PREFIX = "PHLO_AUTH_STATIC_"

# Compliance HMAC key environment keys (phlo.compliance).
_AUDIT_HMAC_KEY_ENV = "PHLO_AUDIT_HMAC_KEY"
_SIGNATURE_HMAC_KEY_ENV = "PHLO_SIGNATURE_HMAC_KEY"

# Workload identities deferred to Plans 004-005. Present so the report is
# total and never optimistically passed.
_DEFERRED_WORKLOAD_CHECKS = (
    (ProductionReadinessCheckId.IDENTITY_WORKLOAD_API, "phlo-api (control plane)"),
    (
        ProductionReadinessCheckId.IDENTITY_WORKLOAD_ORCHESTRATION,
        "Dagster webserver and daemon",
    ),
    (ProductionReadinessCheckId.IDENTITY_WORKLOAD_QUERY, "Trino query engine"),
    (ProductionReadinessCheckId.IDENTITY_WORKLOAD_CATALOG, "Nessie/Iceberg catalog"),
    (ProductionReadinessCheckId.IDENTITY_WORKLOAD_MAINTENANCE, "compaction and snapshot expiry"),
)


# ---------------------------------------------------------------------------
# Report models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProductionReadinessCheck:
    """One readiness check result with a sanitized message."""

    id: ProductionReadinessCheckId
    state: ProductionReadinessState
    message: str
    remediation: str
    source: str
    reason_code: str = ""
    observation_time: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "state": self.state.value,
            "message": self.message,
            "remediation": self.remediation,
            "source": self.source,
            "reason_code": self.reason_code,
            "observation_time": self.observation_time,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ProductionReadinessReport:
    """Stable, deterministic production readiness report (ADR 0047 §7.3)."""

    schema_version: str = "1"
    stage: str = "static_preflight"
    report_id: str = ""
    environment: str = "dev"
    generated_at: str = ""
    passed: bool = False
    services: tuple[str, ...] = ()
    digests: Mapping[str, str] = field(default_factory=dict)
    checks: tuple[ProductionReadinessCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "report_id": self.report_id,
            "environment": self.environment,
            "generated_at": self.generated_at,
            "passed": self.passed,
            "services": list(self.services),
            "digests": dict(self.digests),
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Small local readers (no CLI or provider imports)
# ---------------------------------------------------------------------------


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file without importing CLI internals."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            values[key] = value
    except OSError:
        return {}
    return values


def _project_config(project_root: Path) -> dict[str, Any]:
    """Load phlo.yaml as a plain mapping (never None)."""
    config_file = project_root / "phlo.yaml"
    if not config_file.exists():
        return {}
    try:
        loaded = yaml.safe_load(config_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_effective_environment(phlo_dir: Path, project_root: Path) -> dict[str, str]:
    """Return the effective environment with standard Phlo precedence.

    Precedence, lowest to highest: ``.phlo/.env``, ``.phlo/.env.local``,
    ``phlo.yaml`` ``env:`` overrides, then the process environment.
    """
    env: dict[str, str] = {}
    for file_name in (".env", ".env.local"):
        env.update(_parse_env_file(phlo_dir / file_name))

    config = _project_config(project_root)
    env_overrides = config.get("env")
    if isinstance(env_overrides, dict):
        env.update({str(k): str(v) for k, v in env_overrides.items()})

    env.update(os.environ)
    return env


def _resolve_environment_value(environment: str | None, effective_env: dict[str, str]) -> str:
    """Resolve the effective production/dev environment label."""
    if environment and environment.strip():
        return environment.strip().lower()
    value = effective_env.get(_PHLO_ENVIRONMENT_ENV, "dev").strip().lower()
    return value or "dev"


# ---------------------------------------------------------------------------
# Check implementations (one per closed check ID, uniform context signature)
# ---------------------------------------------------------------------------

# Every builder receives the same keyword context and ignores what it does not
# need, which keeps composition uniform and ordering deterministic.
_CheckContext = Mapping[str, Any]


def _check_env_production(context: _CheckContext) -> ProductionReadinessCheck:
    source = "generated environment"
    environment = context["environment"]
    if environment == "production":
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.ENV_PRODUCTION,
            state=ProductionReadinessState.PASSED,
            message="production environment selected",
            remediation="",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.ENV_PRODUCTION,
        state=ProductionReadinessState.FAILED,
        message=(
            f"not a production environment (effective environment is {environment!r}); "
            "set PHLO_ENVIRONMENT=production or run with --production"
        ),
        remediation="Select the production environment before starting the stack.",
        source=source,
    )


def _check_compose_non_dev(context: _CheckContext) -> ProductionReadinessCheck:
    source = "generated compose"
    compose_file: Path = context["compose_file"]
    try:
        header = compose_file.read_text().splitlines()[:10]
    except OSError as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.COMPOSE_NON_DEV,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"cannot read {compose_file.name}: {exc}",
            remediation="Ensure the compose file is generated and readable.",
            source=source,
        )
    for line in header:
        if line.startswith("# Dev mode:"):
            mode = line.split(":", 1)[1].strip()
            if mode == "true":
                return ProductionReadinessCheck(
                    id=ProductionReadinessCheckId.COMPOSE_NON_DEV,
                    state=ProductionReadinessState.FAILED,
                    message="compose was generated in development mode",
                    remediation="Re-run `phlo services init --production` to regenerate without dev mode.",
                    source=source,
                )
            return ProductionReadinessCheck(
                id=ProductionReadinessCheckId.COMPOSE_NON_DEV,
                state=ProductionReadinessState.PASSED,
                message="compose was generated without dev mode",
                remediation="",
                source=source,
            )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.COMPOSE_NON_DEV,
        state=ProductionReadinessState.UNAVAILABLE,
        message="cannot determine the generated compose dev mode",
        remediation="Regenerate the compose file from `phlo services init` so its dev-mode header is present.",
        source=source,
    )


def _check_http_authorization_required(context: _CheckContext) -> ProductionReadinessCheck:
    source = "effective environment"
    environment = context["environment"]
    effective_env = context["effective_env"]
    if environment != "production":
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED,
            state=ProductionReadinessState.NOT_APPLICABLE,
            message="authorization enforcement is a production requirement only",
            remediation="",
            source=source,
        )
    dev_mode = effective_env.get(_AUTH_DEV_MODE_ENV, "").strip().lower()
    if dev_mode in ("1", "true", "yes", "on"):
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED,
            state=ProductionReadinessState.FAILED,
            message=f"{_AUTH_DEV_MODE_ENV} is enabled while the environment is production",
            remediation="Disable the development authentication bypass for production.",
            source=source,
        )
    has_verified_path = bool(
        effective_env.get(_AUTH_JWT_SECRET_ENV)
        and effective_env.get(_AUTH_JWT_ISSUER_ENV)
        and effective_env.get(_AUTH_JWT_AUDIENCE_ENV)
    )
    if has_verified_path:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED,
            state=ProductionReadinessState.PASSED,
            message="a verified authentication path is configured and dev bypass is disabled",
            remediation="",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED,
        state=ProductionReadinessState.UNAVAILABLE,
        message="HTTP authorization enforcement posture cannot be fully verified from local configuration",
        remediation="Enforce authentication and authorization on every non-public production route.",
        source=source,
    )


def _check_authn_provider(context: _CheckContext) -> ProductionReadinessCheck:
    source = "effective environment"
    effective_env = context["effective_env"]
    secret = effective_env.get(_AUTH_JWT_SECRET_ENV, "")
    issuer = effective_env.get(_AUTH_JWT_ISSUER_ENV, "")
    audience = effective_env.get(_AUTH_JWT_AUDIENCE_ENV, "")
    configured = [bool(secret), bool(issuer), bool(audience)]
    if all(configured):
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHN_PROVIDER,
            state=ProductionReadinessState.PASSED,
            message="a verified JWT authentication provider is configured",
            remediation="",
            source=source,
        )
    if any(configured):
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHN_PROVIDER,
            state=ProductionReadinessState.FAILED,
            message=(
                "the JWT authentication provider is partially configured; "
                "secret, issuer, and audience must all be set"
            ),
            remediation=f"Set {_AUTH_JWT_SECRET_ENV}, {_AUTH_JWT_ISSUER_ENV}, and {_AUTH_JWT_AUDIENCE_ENV} together.",
            source=source,
        )
    proxy_only = any(
        key.startswith(_AUTH_PROXY_PREFIX) and bool(value) for key, value in effective_env.items()
    )
    static_only = any(
        key.startswith(_AUTH_STATIC_PREFIX) and bool(value) for key, value in effective_env.items()
    )
    if proxy_only or static_only:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHN_PROVIDER,
            state=ProductionReadinessState.FAILED,
            message="only development proxy/static authentication is configured",
            remediation="Configure the verified JWT provider (issuer, audience, and secret or JWKS).",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.AUTHN_PROVIDER,
        state=ProductionReadinessState.FAILED,
        message="no production authentication provider is configured",
        remediation="Configure the verified JWT provider (issuer, audience, and secret or JWKS).",
        source=source,
    )


def _check_authz_backend(context: _CheckContext) -> ProductionReadinessCheck:
    source = "registered authorization configuration"
    try:
        from phlo.capabilities import resolve_capability
        from phlo.infrastructure.config import get_configured_authorization_backend_name
    except Exception as exc:  # pragma: no cover - defensive
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHZ_BACKEND,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"authorization backend resolution is unavailable: {exc}",
            remediation="Register backend authorization readiness through provider-owned adapters.",
            source=source,
        )
    try:
        backend_name = get_configured_authorization_backend_name() or ""
    except ValueError as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHZ_BACKEND,
            state=ProductionReadinessState.FAILED,
            message=str(exc),
            remediation="Configure an authorization backend for production.",
            source=source,
        )
    if not backend_name:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHZ_BACKEND,
            state=ProductionReadinessState.FAILED,
            message="no authorization backend is configured",
            remediation="Configure an authorization backend for production.",
            source=source,
        )
    if resolve_capability("authorization_policy_backend", backend_name) is None:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUTHZ_BACKEND,
            state=ProductionReadinessState.FAILED,
            message=f"authorization backend {backend_name!r} is not registered",
            remediation="Install or enable the configured authorization backend.",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.AUTHZ_BACKEND,
        state=ProductionReadinessState.PASSED,
        message=f"authorization backend {backend_name!r} is configured and registered",
        remediation="",
        source=source,
    )


def _check_tls_external_endpoint(context: _CheckContext) -> ProductionReadinessCheck:
    source = "generated compose"
    compose_file: Path = context["compose_file"]
    try:
        compose = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"cannot read {compose_file.name}: {exc}",
            remediation="Ensure the compose file is generated and readable.",
            source=source,
        )
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT,
            state=ProductionReadinessState.UNAVAILABLE,
            message="compose declares no services",
            remediation="Regenerate the compose file before running preflight.",
            source=source,
        )
    tls_evidence: list[str] = []
    for name, config in services.items():
        if not isinstance(config, dict):
            continue
        labels = config.get("labels") or {}
        if isinstance(labels, dict) and any(
            str(key).endswith("tls") or "tls" in str(value) for key, value in labels.items()
        ):
            tls_evidence.append(name)
        ports = config.get("ports") or []
        if isinstance(ports, list) and any("443" in str(port) for port in ports):
            tls_evidence.append(name)
    if tls_evidence:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT,
            state=ProductionReadinessState.PASSED,
            message=f"TLS endpoint represented for: {', '.join(sorted(set(tls_evidence)))}",
            remediation="",
            source=source,
            details={"services": sorted(set(tls_evidence))},
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT,
        state=ProductionReadinessState.UNAVAILABLE,
        message=(
            "TLS termination is not represented in generated compose; "
            "absent and externally terminated TLS cannot be distinguished"
        ),
        remediation="Terminate TLS at the edge and record the termination point in the generated stack.",
        source=source,
    )


def _check_oidc_issuer_audience_jwks(context: _CheckContext) -> ProductionReadinessCheck:
    source = "effective environment"
    effective_env = context["effective_env"]
    issuer = effective_env.get(_AUTH_JWT_ISSUER_ENV, "").strip()
    audience = effective_env.get(_AUTH_JWT_AUDIENCE_ENV, "").strip()
    jwks_url = effective_env.get(_AUTH_JWT_JWKS_URL_ENV, "").strip()
    secret = effective_env.get(_AUTH_JWT_SECRET_ENV, "").strip()
    verification_material = bool(jwks_url) or bool(secret)
    if issuer and audience and verification_material:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.OIDC_ISSUER_AUDIENCE_JWKS,
            state=ProductionReadinessState.PASSED,
            message="OIDC issuer, audience, and verification material are configured",
            remediation="",
            source=source,
        )
    if not issuer or not audience:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.OIDC_ISSUER_AUDIENCE_JWKS,
            state=ProductionReadinessState.FAILED,
            message="OIDC issuer and audience are not both configured",
            remediation=f"Set {_AUTH_JWT_ISSUER_ENV} and {_AUTH_JWT_AUDIENCE_ENV}.",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.OIDC_ISSUER_AUDIENCE_JWKS,
        state=ProductionReadinessState.FAILED,
        message="OIDC verification material (JWKS URL or shared secret) is not configured",
        remediation=f"Set {_AUTH_JWT_JWKS_URL_ENV} or {_AUTH_JWT_SECRET_ENV}.",
        source=source,
    )


def _deferred_workload_check(
    check_id: ProductionReadinessCheckId, workload: str
) -> ProductionReadinessCheck:
    return ProductionReadinessCheck(
        id=check_id,
        state=ProductionReadinessState.UNAVAILABLE,
        message=f"distinct {workload} identity and credential delivery are not yet verified",
        remediation="Plans 004-005 add scoped workload identities and provider-owned credential references.",
        source="deferred workload-identity contributor",
    )


def _check_audit_key_backend(context: _CheckContext) -> ProductionReadinessCheck:
    source = "effective environment"
    effective_env = context["effective_env"]
    audit_key = effective_env.get(_AUDIT_HMAC_KEY_ENV, "").strip()
    signature_key = effective_env.get(_SIGNATURE_HMAC_KEY_ENV, "").strip()
    if audit_key and signature_key:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.AUDIT_KEY_BACKEND,
            state=ProductionReadinessState.PASSED,
            message="audit and signature HMAC keys are configured",
            remediation="",
            source=source,
        )
    missing = [
        name
        for name, value in (
            (_AUDIT_HMAC_KEY_ENV, audit_key),
            (_SIGNATURE_HMAC_KEY_ENV, signature_key),
        )
        if not value
    ]
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.AUDIT_KEY_BACKEND,
        state=ProductionReadinessState.FAILED,
        message=f"audit HMAC keys are not configured: {', '.join(missing)}",
        remediation="Configure PHLO_AUDIT_HMAC_KEY and PHLO_SIGNATURE_HMAC_KEY for durable privileged-mutation audit.",
        source=source,
    )


def _check_policy_compiled_verification(context: _CheckContext) -> ProductionReadinessCheck:
    """Verify compiled RBAC policy is loadable and consistent, read-only.

    Backend drift verification requires provider-owned read-only adapters and
    is delivered by provider-owned read-only adapters; this check never contacts a backend or network.
    """
    source = "local RBAC policy"
    try:
        from phlo.security.validation import _project_rbac_loader
    except Exception as exc:  # pragma: no cover - defensive
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"policy verification is unavailable: {exc}",
            remediation="Add backend drift verification through provider-owned read-only adapters.",
            source=source,
        )
    try:
        rbac = _project_rbac_loader().load()
    except Exception as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION,
            state=ProductionReadinessState.FAILED,
            message=f"compiled RBAC policy cannot be loaded: {exc}",
            remediation="Repair the RBAC configuration so it loads and compiles.",
            source=source,
        )
    if not rbac:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION,
            state=ProductionReadinessState.FAILED,
            message="no RBAC policy is configured for production",
            remediation="Declare the production RBAC policy before starting the stack.",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION,
        state=ProductionReadinessState.PASSED,
        message="compiled RBAC policy loads; backend drift verification pending provider adapters",
        remediation="",
        source=source,
    )


def _check_secrets_no_bundled_shared(context: _CheckContext) -> ProductionReadinessCheck:
    source = "effective environment"
    effective_env = context["effective_env"]
    invalid: list[str] = []

    for variable, default in _PRODUCTION_USERNAME_DEFAULTS.items():
        value = effective_env.get(variable, "")
        if not value.strip() or value.strip() == default:
            invalid.append(variable)

    supplied_passwords: list[str] = []
    for variable, default in _PRODUCTION_PASSWORD_DEFAULTS.items():
        value = effective_env.get(variable, "")
        if value and value.strip() and value.strip() != default:
            supplied_passwords.append(value)
        elif value:
            invalid.append(variable)

    if len(set(supplied_passwords)) != len(supplied_passwords):
        invalid.extend(sorted(_PRODUCTION_PASSWORD_DEFAULTS))

    if invalid:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.SECRETS_NO_BUNDLED_SHARED,
            state=ProductionReadinessState.FAILED,
            message=f"bundled or shared production credentials present: {', '.join(sorted(set(invalid)))}",
            remediation="Set non-default, independent credentials for POSTGRES_USER/MINIO_ROOT_USER and their passwords.",
            source=source,
            details={"variables": sorted(set(invalid))},
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.SECRETS_NO_BUNDLED_SHARED,
        state=ProductionReadinessState.PASSED,
        message="no bundled or shared production credentials present",
        remediation="",
        source=source,
    )


def _check_secrets_env_local_0600(context: _CheckContext) -> ProductionReadinessCheck:
    source = "filesystem metadata"
    phlo_dir: Path = context["phlo_dir"]
    env_local = phlo_dir / ".env.local"
    if os.name != "posix":
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600,
            state=ProductionReadinessState.UNAVAILABLE,
            message="POSIX file-mode inspection is not supported on this platform",
            remediation="Verify restrictive permissions through the platform's native mechanism.",
            source=source,
        )
    try:
        st = env_local.stat()
    except FileNotFoundError:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600,
            state=ProductionReadinessState.UNAVAILABLE,
            message=".env.local is not present; nothing to inspect",
            remediation="Generate the environment file with `phlo services init` before preflight.",
            source=source,
        )
    except OSError as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"cannot stat .env.local: {exc}",
            remediation="Ensure the environment file is inspectable.",
            source=source,
        )
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600,
            state=ProductionReadinessState.FAILED,
            message=f".env.local has mode {oct(mode)}; expected 0600",
            remediation="Re-run `phlo services init` or chmod 0600 .phlo/.env.local.",
            source=source,
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600,
        state=ProductionReadinessState.PASSED,
        message=".env.local owner and mode are 0600",
        remediation="",
        source=source,
    )


def _check_network_protected_ports(context: _CheckContext) -> ProductionReadinessCheck:
    source = "generated compose"
    compose_file: Path = context["compose_file"]
    try:
        compose = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS,
            state=ProductionReadinessState.UNAVAILABLE,
            message=f"cannot read {compose_file.name}: {exc}",
            remediation="Ensure the compose file is generated and readable.",
            source=source,
        )
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS,
            state=ProductionReadinessState.NOT_APPLICABLE,
            message="compose declares no services",
            remediation="",
            source=source,
        )
    selected_protected = [name for name in services if name in _PROTECTED_SERVICES]
    if not selected_protected:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS,
            state=ProductionReadinessState.NOT_APPLICABLE,
            message="no protected backends are selected",
            remediation="",
            source=source,
        )
    exposed: list[str] = []
    for name in selected_protected:
        config = services.get(name)
        ports = config.get("ports") if isinstance(config, dict) else None
        if isinstance(ports, list) and ports:
            exposed.append(name)
    if exposed:
        return ProductionReadinessCheck(
            id=ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS,
            state=ProductionReadinessState.FAILED,
            message=f"protected services publish host ports: {', '.join(sorted(exposed))}",
            remediation="Regenerate with `phlo services init --production` so protected backends are internal-only.",
            source=source,
            details={"services": sorted(exposed)},
        )
    return ProductionReadinessCheck(
        id=ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS,
        state=ProductionReadinessState.PASSED,
        message="protected backends expose no host ports",
        remediation="",
        source=source,
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

# Required checks in deterministic report order. Deferred workload checks are
# appended after these so the closed vocabulary is always total.
_CHECK_BUILDERS: tuple[tuple[ProductionReadinessCheckId, Any], ...] = (
    (ProductionReadinessCheckId.ENV_PRODUCTION, _check_env_production),
    (ProductionReadinessCheckId.COMPOSE_NON_DEV, _check_compose_non_dev),
    (ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED, _check_http_authorization_required),
    (ProductionReadinessCheckId.AUTHN_PROVIDER, _check_authn_provider),
    (ProductionReadinessCheckId.AUTHZ_BACKEND, _check_authz_backend),
    (ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT, _check_tls_external_endpoint),
    (ProductionReadinessCheckId.OIDC_ISSUER_AUDIENCE_JWKS, _check_oidc_issuer_audience_jwks),
    (ProductionReadinessCheckId.AUDIT_KEY_BACKEND, _check_audit_key_backend),
    (ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION, _check_policy_compiled_verification),
    (ProductionReadinessCheckId.SECRETS_NO_BUNDLED_SHARED, _check_secrets_no_bundled_shared),
    (ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600, _check_secrets_env_local_0600),
    (ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS, _check_network_protected_ports),
)

_PASSING_STATES = frozenset(
    {ProductionReadinessState.PASSED, ProductionReadinessState.NOT_APPLICABLE}
)

# The complete closed check-ID set; every ID must have a runner.
_ALL_CHECK_IDS = frozenset(check_id for check_id, _ in _CHECK_BUILDERS) | frozenset(
    check_id for check_id, _ in _DEFERRED_WORKLOAD_CHECKS
)
assert len(_ALL_CHECK_IDS) == len(ProductionReadinessCheckId), "check vocabulary is not total"


def _derive_reason_code(check: ProductionReadinessCheck) -> str:
    """Map a check's state/id to the closed reason-code vocabulary (§7.3)."""
    if check.state is ProductionReadinessState.PASSED:
        return ProductionReadinessReasonCode.OK.value
    if check.state is ProductionReadinessState.NOT_APPLICABLE:
        return ProductionReadinessReasonCode.NOT_APPLICABLE.value
    if check.state is ProductionReadinessState.UNAVAILABLE:
        return ProductionReadinessReasonCode.EVIDENCE_UNAVAILABLE.value

    failed_by_id: dict[ProductionReadinessCheckId, ProductionReadinessReasonCode] = {
        ProductionReadinessCheckId.ENV_PRODUCTION: ProductionReadinessReasonCode.NOT_PRODUCTION,
        ProductionReadinessCheckId.COMPOSE_NON_DEV: ProductionReadinessReasonCode.DEV_COMPOSE,
        ProductionReadinessCheckId.HTTP_AUTHORIZATION_REQUIRED: (
            ProductionReadinessReasonCode.AUTH_BYPASS_IN_PRODUCTION
        ),
        ProductionReadinessCheckId.AUTHN_PROVIDER: ProductionReadinessReasonCode.AUTHN_MISSING,
        ProductionReadinessCheckId.AUTHZ_BACKEND: ProductionReadinessReasonCode.AUTHZ_MISSING,
        ProductionReadinessCheckId.TLS_EXTERNAL_ENDPOINT: (
            ProductionReadinessReasonCode.TLS_NOT_REPRESENTED
        ),
        ProductionReadinessCheckId.OIDC_ISSUER_AUDIENCE_JWKS: (
            ProductionReadinessReasonCode.OIDC_MISSING
        ),
        ProductionReadinessCheckId.AUDIT_KEY_BACKEND: ProductionReadinessReasonCode.AUDIT_KEY_MISSING,
        ProductionReadinessCheckId.POLICY_COMPILED_VERIFICATION: (
            ProductionReadinessReasonCode.POLICY_UNLOADABLE
        ),
        ProductionReadinessCheckId.SECRETS_NO_BUNDLED_SHARED: (
            ProductionReadinessReasonCode.CREDENTIALS_BUNDLED_OR_SHARED
        ),
        ProductionReadinessCheckId.SECRETS_ENV_LOCAL_0600: (
            ProductionReadinessReasonCode.SECRET_MODE_PERMISSIVE
        ),
        ProductionReadinessCheckId.NETWORK_PROTECTED_PORTS: (
            ProductionReadinessReasonCode.PROTECTED_PORTS_EXPOSED
        ),
    }
    return failed_by_id.get(check.id, ProductionReadinessReasonCode.EVIDENCE_UNAVAILABLE).value


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _compute_digests(
    project_root: Path,
    phlo_dir: Path,
    service_names: tuple[str, ...],
) -> dict[str, str]:
    """Hash the exact inputs this report actually inspected (§7.3 digests)."""
    import importlib.metadata as metadata

    try:
        release = metadata.version("phlo")
    except metadata.PackageNotFoundError:
        release = "unknown"

    config_file = project_root / "phlo.yaml"
    config_bytes = config_file.read_bytes() if config_file.exists() else b""

    compose_file = phlo_dir / "docker-compose.yml"
    compose_bytes = compose_file.read_bytes() if compose_file.exists() else b""

    policy_bytes = b""
    try:
        from phlo.security.validation import _project_rbac_loader

        rbac = _project_rbac_loader().load()
        if rbac:
            policy_bytes = json.dumps(rbac, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        policy_bytes = b""

    return {
        "release": release,
        "config": _sha256_hex(config_bytes),
        "compose": _sha256_hex(compose_bytes),
        "policy": _sha256_hex(policy_bytes),
        "services": _sha256_hex("\n".join(service_names).encode("utf-8")),
    }


def run_production_readiness(
    plan: Any,
    project_root: Path | str,
    environment: str | None = None,
) -> ProductionReadinessReport:
    """Evaluate production readiness for a selected service set.

    ``plan`` is any object exposing ``phlo_dir``, ``compose_file``, and
    ``service_names`` (the CLI ``StartPreflightPlan`` satisfies this). The
    evaluation is read-only; the report is deterministic and secret-free.
    """
    project_root = Path(project_root)
    phlo_dir = Path(plan.phlo_dir)
    compose_file = Path(plan.compose_file)
    service_names = tuple(sorted(plan.service_names))

    effective_env = load_effective_environment(phlo_dir, project_root)
    environment = _resolve_environment_value(environment, effective_env)

    context: dict[str, Any] = {
        "environment": environment,
        "effective_env": effective_env,
        "compose_file": compose_file,
        "phlo_dir": phlo_dir,
    }

    checks: list[ProductionReadinessCheck] = [builder(context) for _, builder in _CHECK_BUILDERS]
    for check_id, workload in _DEFERRED_WORKLOAD_CHECKS:
        checks.append(_deferred_workload_check(check_id, workload))

    generated_at = datetime.now(UTC).isoformat()
    checks = [
        replace(
            check,
            observation_time=generated_at,
            reason_code=check.reason_code or _derive_reason_code(check),
        )
        for check in checks
    ]

    passed = all(check.state in _PASSING_STATES for check in checks)
    return ProductionReadinessReport(
        schema_version="1",
        stage="static_preflight",
        report_id=uuid4().hex,
        environment=environment,
        generated_at=generated_at,
        passed=passed,
        services=service_names,
        digests=_compute_digests(project_root, phlo_dir, service_names),
        checks=tuple(checks),
    )
