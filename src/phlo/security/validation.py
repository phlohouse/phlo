"""Startup validation for regulated mode.

Validates that registered regulated surfaces are properly configured
at application startup. Fails fast if required surfaces are missing or inactive.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from phlo.logging import get_logger
from phlo.rbac.compiler import COMPILER_REGISTRY
from phlo.rbac.config import RBACConfigLoader
from phlo.rbac.models import CANONICAL_ACTIONS, CanonicalRBAC, ResourceType
from phlo.security.gating import validate_service_selection
from phlo.security.mode import is_regulated

logger = get_logger(__name__)

REQUIRED_AUTHORIZATION_MODE = "required"
PHLO_AUDIT_HMAC_KEY_ENV = "PHLO_AUDIT_HMAC_KEY"
PHLO_SIGNATURE_HMAC_KEY_ENV = "PHLO_SIGNATURE_HMAC_KEY"

# These services are v1 implementation backends. They are not supported as
# direct user-facing entry points in a regulated deployment; the production
# Compose profile must keep them off the host network and out of Traefik.
INTERNAL_BACKEND_SERVICES = frozenset({"postgres", "minio", "nessie", "trino"})


def _project_rbac_loader() -> RBACConfigLoader:
    """Load canonical RBAC from the same configured project as runtime startup."""
    from phlo.infrastructure.config import _default_project_root

    return RBACConfigLoader(_default_project_root() / ".phlo")


class RegulatedValidationError(Exception):
    """Raised when regulated mode validation fails."""


RegulatedModeError = RegulatedValidationError  # deprecated alias


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    name: str
    passed: bool
    message: str
    required: bool = True


@dataclass
class RegulatedValidationReport:
    """Complete validation report for regulated mode."""

    regulated_enabled: bool
    passed: bool
    checks: list[ValidationResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_check(self, result: ValidationResult) -> None:
        """Add a validation check result."""
        self.checks.append(result)
        if not result.passed and result.required:
            self.errors.append(f"{result.name}: {result.message}")
            self.passed = False


RegulatedModeValidationReport = RegulatedValidationReport  # deprecated alias


def _check_authorization_backend() -> ValidationResult:
    """Validate that an authorization backend is configured."""
    from phlo.capabilities import resolve_capability
    from phlo.infrastructure.config import get_configured_authorization_backend_name

    try:
        backend_name = get_configured_authorization_backend_name() or ""
    except ValueError as exc:
        return ValidationResult(
            name="authorization_backend_configured",
            passed=False,
            message=str(exc),
        )
    if not backend_name:
        return ValidationResult(
            name="authorization_backend_configured",
            passed=False,
            message="No authorization backend name is configured",
        )

    if resolve_capability("authorization_policy_backend", backend_name) is None:
        return ValidationResult(
            name="authorization_backend_configured",
            passed=False,
            message=f"Authorization backend {backend_name!r} is not registered",
        )

    return ValidationResult(
        name="authorization_backend_configured",
        passed=True,
        message=f"Authorization backend {backend_name!r} is configured and registered",
    )


def _check_fail_closed_mode() -> ValidationResult:
    """Validate fail-closed mode is enabled."""
    from phlo.infrastructure.config import get_api_authorization_config

    mode_env = os.environ.get("PHLO_AUTHORIZATION_MODE", "").strip().lower()

    if mode_env == REQUIRED_AUTHORIZATION_MODE:
        return ValidationResult(
            name="fail_closed_mode",
            passed=True,
            message=f"Fail-closed mode enabled via environment ({REQUIRED_AUTHORIZATION_MODE})",
        )

    if mode_env and mode_env != REQUIRED_AUTHORIZATION_MODE:
        return ValidationResult(
            name="fail_closed_mode",
            passed=False,
            message=f"Authorization mode is '{mode_env}' but regulated mode requires '{REQUIRED_AUTHORIZATION_MODE}'",
        )

    config = get_api_authorization_config()
    mode = config.mode if config else None

    if mode == REQUIRED_AUTHORIZATION_MODE:
        return ValidationResult(
            name="fail_closed_mode",
            passed=True,
            message=f"Fail-closed mode enabled via config ({REQUIRED_AUTHORIZATION_MODE})",
        )

    return ValidationResult(
        name="fail_closed_mode",
        passed=False,
        message=f"Authorization mode is '{mode or 'optional (default)'}' but regulated mode requires '{REQUIRED_AUTHORIZATION_MODE}'",
    )


def _check_compliance_hmac_keys() -> ValidationResult:
    """Validate regulated compliance HMAC keys are explicitly configured."""
    audit_key = os.environ.get(PHLO_AUDIT_HMAC_KEY_ENV, "").strip()
    signature_key = os.environ.get(PHLO_SIGNATURE_HMAC_KEY_ENV, "").strip()

    if not audit_key:
        return ValidationResult(
            name="compliance_hmac_keys_configured",
            passed=False,
            message=f"{PHLO_AUDIT_HMAC_KEY_ENV} is required for regulated audit sealing",
        )

    if not signature_key:
        return ValidationResult(
            name="compliance_hmac_keys_configured",
            passed=False,
            message=(f"{PHLO_SIGNATURE_HMAC_KEY_ENV} is required for regulated signature sealing"),
        )

    return ValidationResult(
        name="compliance_hmac_keys_configured",
        passed=True,
        message="Regulated audit and signature HMAC keys are configured",
    )


def _check_canonical_rbac() -> ValidationResult:
    """Validate canonical RBAC configuration exists and is valid."""
    loader = _project_rbac_loader()

    try:
        roles_config = loader.load_roles()
        policies_config = loader.load_policies()
    except FileNotFoundError as e:
        return ValidationResult(
            name="canonical_rbac_configured",
            passed=False,
            message=f"Canonical RBAC configuration not found: {e}",
        )
    except Exception as e:
        return ValidationResult(
            name="canonical_rbac_configured",
            passed=False,
            message=f"Failed to load canonical RBAC configuration: {e}",
        )

    rbac = CanonicalRBAC.from_configs(roles_config, policies_config)
    errors = rbac.validate()

    if errors:
        return ValidationResult(
            name="canonical_rbac_configured",
            passed=False,
            message=f"Canonical RBAC validation failed: {'; '.join(errors)}",
        )

    return ValidationResult(
        name="canonical_rbac_configured",
        passed=True,
        message=f"Canonical RBAC configured and valid (version hash: {rbac.version_hash})",
    )


def _check_backend_coverage() -> ValidationResult:
    """Validate required backend compilers are available."""
    try:
        loader = _project_rbac_loader()
        roles_config = loader.load_roles()
        policies_config = loader.load_policies()
        rbac = CanonicalRBAC.from_configs(roles_config, policies_config)

        unsupported: set[str] = set()
        for policy in rbac.policies.policies:
            action_supported = False
            for compiler_class in COMPILER_REGISTRY.values():
                compiler = compiler_class(backend=None)
                applicability = compiler.policy_applicability(policy.action, policy.resource_type)
                if applicability in {"trino", "surface"}:
                    action_supported = True
                    break
            if not action_supported:
                unsupported.add(policy.action)

        if unsupported:
            return ValidationResult(
                name="backend_sync_status",
                passed=False,
                message=f"Actions without compiler support: {', '.join(sorted(unsupported))}",
            )

    except Exception as e:
        return ValidationResult(
            name="backend_sync_status",
            passed=False,
            message=f"Failed to validate backend coverage: {e}",
        )

    return ValidationResult(
        name="backend_sync_status",
        passed=True,
        message="Backend compilers are available and cover all configured actions",
    )


def _production_compose_path() -> Path:
    """Return the generated Compose file for the configured project."""
    from phlo.infrastructure.config import _default_project_root

    return _default_project_root() / ".phlo" / "docker-compose.yml"


def _has_public_traefik_route(config: dict[str, Any]) -> bool:
    """Return whether a Compose service retains a Traefik route label."""
    labels = config.get("labels", {})
    if isinstance(labels, dict):
        return any(str(key).startswith("traefik.") for key in labels)
    if isinstance(labels, list):
        return any(isinstance(label, str) and label.startswith("traefik.") for label in labels)
    return False


def _check_internal_backend_boundary() -> ValidationResult:
    """Reject a regulated deployment whose generated backend services are public.

    This validates the rendered deployment contract rather than trying to
    infer it from service manifests. A production project can safely use these
    backends over the internal Compose network, but a published host port,
    Traefik route, or host network mode would bypass that v1 boundary.
    """
    compose_path = _production_compose_path()
    if not compose_path.exists():
        return ValidationResult(
            name="internal_backend_boundary",
            passed=False,
            message=(
                "Regulated mode requires a generated .phlo/docker-compose.yml; "
                "render it with 'phlo services init --production'"
            ),
        )

    try:
        document = yaml.safe_load(compose_path.read_text()) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return ValidationResult(
            name="internal_backend_boundary",
            passed=False,
            message=f"Could not read generated Compose configuration: {exc}",
        )

    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return ValidationResult(
            name="internal_backend_boundary",
            passed=False,
            message="Generated Compose configuration must contain a services mapping",
        )

    violations: list[str] = []
    for service_name in sorted(INTERNAL_BACKEND_SERVICES):
        config = services.get(service_name)
        if config is None:
            continue
        if not isinstance(config, dict):
            violations.append(f"{service_name} has an invalid service configuration")
            continue
        if config.get("ports"):
            violations.append(f"{service_name} publishes host ports")
        if config.get("network_mode") == "host":
            violations.append(f"{service_name} uses host networking")
        if _has_public_traefik_route(config):
            violations.append(f"{service_name} has public Traefik labels")

    if violations:
        return ValidationResult(
            name="internal_backend_boundary",
            passed=False,
            message=(
                "Regulated backend boundary is not internal: "
                f"{'; '.join(violations)}. Regenerate with 'phlo services init --production'."
            ),
        )

    configured = sorted(name for name in INTERNAL_BACKEND_SERVICES if name in services)
    return ValidationResult(
        name="internal_backend_boundary",
        passed=True,
        message=(
            "Generated regulated Compose keeps backend services internal"
            + (f": {', '.join(configured)}" if configured else "")
        ),
    )


def _check_identity_provider() -> ValidationResult:
    """Validate that an identity provider is configured."""
    from phlo.infrastructure.config import (
        get_authentication_config,
        get_configured_authentication_provider_name,
    )

    try:
        configured_name = (get_configured_authentication_provider_name() or "").lower()
    except ValueError as exc:
        return ValidationResult(
            name="identity_provider_configured",
            passed=False,
            message=str(exc),
        )
    supported = {"proxy", "jwt", "service_token"}
    if configured_name not in supported:
        return ValidationResult(
            name="identity_provider_configured",
            passed=False,
            message=(
                f"Unsupported regulated identity provider {configured_name or '<missing>'!r}; "
                f"choose one of {sorted(supported)}"
            ),
        )

    block = get_authentication_config().get(configured_name, {})
    block = block if isinstance(block, dict) else {}
    secret_env = {
        "proxy": "PHLO_AUTH_PROXY_SHARED_SECRET",
        "jwt": "PHLO_AUTH_JWT_SECRET",
        "service_token": "PHLO_AUTH_SERVICE_TOKENS",
    }[configured_name]
    config_key = {"proxy": "shared_secret", "jwt": "secret", "service_token": "tokens"}[
        configured_name
    ]
    if not os.environ.get(secret_env, "").strip() and not block.get(config_key):
        return ValidationResult(
            name="identity_provider_configured",
            passed=False,
            message=f"Configured regulated provider {configured_name!r} is missing {secret_env}",
        )

    if configured_name == "jwt":
        issuer = os.environ.get("PHLO_AUTH_JWT_ISSUER", "").strip() or block.get("issuer")
        audience = os.environ.get("PHLO_AUTH_JWT_AUDIENCE", "").strip() or block.get("audience")
        if not isinstance(issuer, str) or not issuer.strip():
            return ValidationResult(
                name="identity_provider_configured",
                passed=False,
                message="Regulated jwt provider requires PHLO_AUTH_JWT_ISSUER",
            )
        if not isinstance(audience, str) or not audience.strip():
            return ValidationResult(
                name="identity_provider_configured",
                passed=False,
                message="Regulated jwt provider requires PHLO_AUTH_JWT_AUDIENCE",
            )
    elif configured_name == "service_token":
        try:
            from phlo.capabilities.authentication import _load_service_token_config

            _load_service_token_config()
        except (TypeError, ValueError) as exc:
            return ValidationResult(
                name="identity_provider_configured",
                passed=False,
                message=f"Invalid regulated service-token configuration: {exc}",
            )

    from phlo.capabilities import list_capabilities

    if configured_name not in list_capabilities("authentication_provider"):
        return ValidationResult(
            name="identity_provider_configured",
            passed=False,
            message=(
                f"Configured regulated provider {configured_name!r} is not registered; "
                "enable its capability provider before regulated startup"
            ),
        )

    return ValidationResult(
        name="identity_provider_configured",
        passed=True,
        message=f"Configured regulated identity provider: {configured_name}",
    )


def _configured_service_names() -> list[str]:
    """Return the selected service names from project config and environment."""
    from phlo.cli.commands.services.utils import get_enabled_disabled_service_names
    from phlo.infrastructure.config import _default_project_root, load_project_config

    configured: set[str] = set()
    raw_enabled = os.environ.get("PHLO_ENABLED_SERVICES", "")
    configured.update(name.strip() for name in raw_enabled.split(",") if name.strip())
    project = load_project_config(_default_project_root())
    enabled, disabled = get_enabled_disabled_service_names(project)
    configured.update(enabled)
    configured.difference_update(disabled)
    return sorted(configured)


def _check_phlo_api_adapter(runtime: Any) -> ValidationResult:
    """Validate that phlo-api is registered and active on the runtime.

    Per plan: phlo-api is the only required regulated surface in v1.
    Startup must fail if the adapter is missing, not installed, or inactive.
    """
    from phlo.capabilities import get_capability_registry

    registered = get_capability_registry().list("regulated_surface")
    phlo_api_spec = next((s for s in registered if s.name == "phlo-api"), None)

    if phlo_api_spec is None:
        return ValidationResult(
            name="phlo_api_adapter_registered",
            passed=False,
            message="phlo-api regulated surface adapter is not registered",
        )

    adapter = phlo_api_spec.provider
    try:
        is_active = adapter.is_active(runtime)
    except Exception:
        is_active = False

    if not is_active:
        return ValidationResult(
            name="phlo_api_adapter_active",
            passed=False,
            message="phlo-api adapter is registered but inactive on this runtime",
        )

    return ValidationResult(
        name="phlo_api_adapter_registered",
        passed=True,
        message="phlo-api regulated surface adapter is registered and active",
    )


def _collect_adapter_taxonomy(runtime: Any) -> tuple[set[str], set[str]]:
    """Collect canonical actions and resource types from all registered adapters."""
    from phlo.capabilities import get_capability_registry

    surface_actions: set[str] = set()
    surface_resource_types: set[str] = set()

    for spec in get_capability_registry().list("regulated_surface"):
        adapter = spec.provider
        with suppress(Exception):
            ops = adapter.list_operations()
            for op in ops:
                surface_actions.add(op["action"])
                surface_resource_types.add(op["resource_type"])

    return surface_actions, surface_resource_types


def _check_registered_surfaces(runtime: Any) -> ValidationResult:
    """Check registered regulated surfaces status for informational reporting.

    Reports registered surfaces and surfaces not yet integrated (dagster, cli).
    This is an informational check only (not required to pass).

    For surfaces that support the runtime-aware is_active(runtime) protocol,
    passes the runtime so activation can be checked against the real framework.
    """
    from phlo.capabilities import get_capability_registry

    registered = get_capability_registry().list("regulated_surface")
    registered_names = {spec.name for spec in registered}

    not_yet_integrated: list[str] = []
    for name in ["cli"]:
        if name not in registered_names:
            not_yet_integrated.append(name)

    report: dict[str, Any] = {
        "registered": [],
        "not_yet_integrated": not_yet_integrated,
    }

    for spec in registered:
        adapter = spec.provider
        is_active_result: bool | None = None
        with suppress(Exception):
            is_active_result = adapter.is_active(runtime)

        ops: list[Any] = []
        with suppress(Exception):
            ops = adapter.list_operations()

        report["registered"].append(
            {
                "name": spec.name,
                "framework": getattr(adapter, "framework_type", "unknown"),
                "active": is_active_result,
                "operation_count": len(ops),
            }
        )

    registered_msgs = [r["name"] for r in report["registered"]]
    return ValidationResult(
        name="regulated_surfaces_registered",
        passed=True,
        required=False,
        message=f"Registered: {', '.join(sorted(registered_msgs))}; not yet integrated: {', '.join(not_yet_integrated)}",
    )


def _check_settings_backend() -> ValidationResult:
    """Validate that regulated mode uses durable settings storage.

    Memory mode is never permitted in regulated deployments because writes
    are not durable and disappear at process restart.
    """
    from phlo.plugins.observatory_settings import ObservatorySettingsStorageConfig

    try:
        config = ObservatorySettingsStorageConfig()
    except Exception as exc:
        return ValidationResult(
            name="settings_backend_durable",
            passed=False,
            message=f"Settings backend configuration is invalid: {exc}",
        )

    backend = config.observatory_settings_backend
    if backend == "memory":
        return ValidationResult(
            name="settings_backend_durable",
            passed=False,
            message=(
                "Regulated mode requires durable settings storage; memory backend is not permitted"
            ),
        )

    return ValidationResult(
        name="settings_backend_durable",
        passed=True,
        message=f"Settings backend '{backend}' is durable",
    )


def run_regulated_validation(
    surface_actions: list[str] | None = None,
    surface_resource_types: list[str] | None = None,
    config_regulated: bool | None = None,
    runtime: Any = None,
) -> RegulatedValidationReport:
    """Run all regulated mode validation checks.

    surface_actions and surface_resource_types optionally override the taxonomy
    used for validation; when omitted they are collected from registered adapter
    operations. config_regulated is the regulated-mode setting from the config
    file, and runtime is the framework runtime (e.g., FastAPI app) to validate
    adapter wiring against.

    Returns a RegulatedValidationReport with all check results.
    """
    regulated = is_regulated(config_regulated)

    report = RegulatedValidationReport(
        regulated_enabled=regulated,
        passed=True,
    )

    if not regulated:
        logger.info("regulated_not_enabled", skipping_validation=True)
        report.add_check(
            ValidationResult(
                name="regulated_enabled",
                passed=True,
                message="Regulated mode is not enabled, skipping validation",
                required=False,
            )
        )
        return report

    logger.info("regulated_validation_started")

    report.add_check(_check_identity_provider())
    report.add_check(_check_canonical_rbac())
    report.add_check(_check_authorization_backend())
    report.add_check(_check_fail_closed_mode())
    report.add_check(_check_compliance_hmac_keys())

    adapter_actions, adapter_resource_types = _collect_adapter_taxonomy(runtime)
    effective_actions = set(surface_actions) if surface_actions else adapter_actions
    effective_resource_types = (
        set(surface_resource_types) if surface_resource_types else adapter_resource_types
    )
    report.add_check(
        _check_canonical_taxonomy(list(effective_actions), list(effective_resource_types))
    )
    report.add_check(_check_backend_coverage())
    report.add_check(_check_internal_backend_boundary())
    report.add_check(_check_settings_backend())

    report.add_check(_check_phlo_api_adapter(runtime))

    report.add_check(_check_registered_surfaces(runtime))

    service_result = validate_service_selection(_configured_service_names())
    if service_result["blocked"]:
        blocked_msgs = [b["reason"] for b in service_result["blocked"]]
        report.add_check(
            ValidationResult(
                name="unsupported_surfaces_disabled",
                passed=False,
                message=f"Blocked services: {'; '.join(blocked_msgs)}",
            )
        )

    _verify_compiled_rbac(report)

    if report.passed:
        logger.info("regulated_validation_passed")
    else:
        logger.error(
            "regulated_validation_failed",
            errors=report.errors,
        )

    if report.warnings:
        logger.warning("regulated_validation_warnings", warnings=report.warnings)

    return report


def run_regulated_mode_validation(**kwargs):
    """Deprecated: use run_regulated_validation() instead."""
    import warnings

    warnings.warn(
        "run_regulated_mode_validation() is deprecated, use run_regulated_validation() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return run_regulated_validation(**kwargs)


def _check_canonical_taxonomy(
    surface_actions: list[str] | None = None,
    surface_resource_types: list[str] | None = None,
) -> ValidationResult:
    """Validate surface operations use canonical taxonomy."""
    errors: list[str] = []

    if surface_actions:
        non_canonical = set(surface_actions) - CANONICAL_ACTIONS
        if non_canonical:
            errors.append(f"Non-canonical actions: {', '.join(sorted(non_canonical))}")

    if surface_resource_types:
        non_canonical = set(surface_resource_types) - {rt.value for rt in ResourceType}
        if non_canonical:
            errors.append(f"Non-canonical resource types: {', '.join(sorted(non_canonical))}")

    if errors:
        return ValidationResult(
            name="canonical_taxonomy_supported",
            passed=False,
            message="; ".join(errors),
        )

    return ValidationResult(
        name="canonical_taxonomy_supported",
        passed=True,
        message="All surface operations use canonical actions and resource types",
    )


def require_regulated_validation(
    surface_actions: list[str] | None = None,
    surface_resource_types: list[str] | None = None,
    config_regulated: bool | None = None,
    runtime: Any = None,
) -> None:
    """Run validation and raise RegulatedValidationError if required checks fail.

    Use this at application startup to fail fast in regulated mode.
    """
    report = run_regulated_validation(
        surface_actions=surface_actions,
        surface_resource_types=surface_resource_types,
        config_regulated=config_regulated,
        runtime=runtime,
    )

    if report.regulated_enabled and not report.passed:
        error_message = f"Regulated mode validation failed. Errors: {'; '.join(report.errors)}"
        raise RegulatedValidationError(error_message)


def require_regulated_mode_validation(**kwargs):
    """Deprecated: use require_regulated_validation() instead."""
    import warnings

    warnings.warn(
        "require_regulated_mode_validation() is deprecated, use require_regulated_validation() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return require_regulated_validation(**kwargs)


def _verify_compiled_rbac(report: RegulatedValidationReport) -> None:
    """Verify that compiled RBAC grants match backend state.

    For each registered backend compiler, calls verify() to compare
    desired state (from compiled RBAC) against actual backend state.

    Results are added as warnings, not hard failures, because:
    - Not all compilers have verify() implemented
    - Some backends may not be reachable at startup
    - We want to surface drift without breaking existing deployments
    """
    from phlo.rbac.compiler import CompilerContext

    try:
        rbac_loader = _project_rbac_loader()
        rbac = rbac_loader.load()
    except Exception:
        report.warnings.append("compiled_rbac_verify: could not load RBAC config")
        logger.debug("compiled_rbac_verify_skipped", reason="rbac_load_failed")
        return

    if not COMPILER_REGISTRY:
        report.warnings.append("compiled_rbac_verify: no backend compilers registered")
        return

    for backend_name, compiler_class in sorted(COMPILER_REGISTRY.items()):
        context = CompilerContext(environment="regulated", backend_name=backend_name)
        try:
            compiler = compiler_class(backend=None)
            result = compiler.verify(rbac, context)
            if not result.in_sync:
                missing_names = [a.name for a in result.missing][:5]
                extra_names = [a.name for a in result.extra][:5]
                parts = []
                if missing_names:
                    parts.append(f"{len(result.missing)} missing: {missing_names}")
                if extra_names:
                    parts.append(f"{len(result.extra)} extra: {extra_names}")
                report.warnings.append(
                    f"compiled_rbac_verify({backend_name}): out of sync — {'; '.join(parts)}"
                )
                logger.warning(
                    "compiled_rbac_out_of_sync",
                    backend=backend_name,
                    missing_count=len(result.missing),
                    extra_count=len(result.extra),
                )
            else:
                logger.info("compiled_rbac_in_sync", backend=backend_name)
        except NotImplementedError:
            report.warnings.append(
                f"compiled_rbac_verify({backend_name}): verify() not implemented"
            )
        except Exception as exc:
            report.warnings.append(f"compiled_rbac_verify({backend_name}): {exc}")
            logger.debug(
                "compiled_rbac_verify_error",
                backend=backend_name,
                error=str(exc),
            )
