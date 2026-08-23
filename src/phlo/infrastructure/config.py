"""
Infrastructure Configuration Loader

Loads infrastructure configuration from phlo.yaml.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from phlo.config.cache import project_root_cached
from phlo.config_schema import (
    ApiAuthorizationConfig,
    ApiConfig,
    InfrastructureConfig,
    ServiceConfig,
    WapConfig,
)
from phlo.logging import get_logger

logger = get_logger(__name__)


def _default_project_root() -> Path:
    """Resolve the default project root from environment or current working directory."""
    project_root = os.environ.get("PHLO_PROJECT_PATH")
    if project_root:
        raw_path = Path(project_root)
        if ".." in raw_path.parts:
            logger.warning(
                "project_path_traversal_rejected",
                raw=project_root,
                reason="path_traversal_sequence",
            )
            raise ValueError(f"PHLO_PROJECT_PATH contains path traversal: {project_root}")
        return raw_path.resolve()
    return Path.cwd()


@project_root_cached
def load_project_config(project_root: Path) -> dict[str, Any]:
    """Load raw project configuration from phlo.yaml."""
    started = time.perf_counter()
    config_path = project_root / "phlo.yaml"
    logger.debug(
        "project_config_load_started",
        project_root=str(project_root),
        path=str(config_path),
    )

    if not config_path.exists():
        logger.info(
            "project_config_load_completed",
            source="default",
            reason="missing_file",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return {}

    try:
        with config_path.open() as f:
            project_config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        logger.error("invalid_phlo_yaml", path=str(config_path), error=str(exc))
        raise

    if not isinstance(project_config, dict):
        logger.info(
            "project_config_load_completed",
            source="default",
            reason="empty_or_non_mapping",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return {}

    logger.info(
        "project_config_load_completed",
        source="file",
        key_count=len(project_config),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return project_config


@project_root_cached
def load_infrastructure_config(project_root: Path) -> InfrastructureConfig:
    """Load infrastructure configuration from phlo.yaml."""
    started = time.perf_counter()
    logger.debug("infrastructure_config_load_started", project_root=str(project_root))

    try:
        project_config = load_project_config(project_root)
        if not project_config:
            logger.info(
                "infrastructure_config_load_completed",
                source="default",
                reason="missing_project_config",
                services_count=0,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return InfrastructureConfig()

        infra_config_data = project_config.get("infrastructure", {})

        if not infra_config_data:
            logger.info(
                "infrastructure_config_load_completed",
                source="default",
                reason="missing_infrastructure_section",
                services_count=0,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return InfrastructureConfig()
        config = InfrastructureConfig(**infra_config_data)
        logger.info(
            "infrastructure_config_load_completed",
            source="file",
            services_count=len(config.services),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return config

    except ValidationError as exc:
        logger.error(
            "invalid_infrastructure_config",
            path=str(project_root / "phlo.yaml"),
            error=str(exc),
        )
        raise


@project_root_cached
def load_wap_config(project_root: Path | None = None) -> WapConfig:
    """Load the project-level WAP policy from ``phlo.yaml``.

    Keeping this separate from infrastructure configuration makes the policy
    available to both the host CLI and the Dagster process running in ``/app``.
    """
    root = project_root or _default_project_root()
    project_config = load_project_config(root)
    wap_config = project_config.get("wap", {}) if project_config else {}
    if wap_config is None:
        wap_config = {}
    return WapConfig(**wap_config)


def get_project_name_from_config(project_root: Path | None = None) -> str | None:
    """Get project name from phlo.yaml."""
    if project_root is None:
        project_root = _default_project_root()

    try:
        project_config = load_project_config(project_root)
        return project_config.get("name") if project_config else None
    except Exception:
        logger.warning("failed_to_read_project_name", path=str(project_root / "phlo.yaml"))
        return None


def get_capability_defaults_from_config(project_root: Path | None = None) -> dict[str, str]:
    """Return capability defaults declared in phlo.yaml.

    Entries whose key or value is missing, empty, or not a string are dropped
    silently rather than raising.
    """
    project_config = load_project_config(project_root)
    capabilities = project_config.get("capabilities", {})
    if not isinstance(capabilities, dict):
        return {}

    defaults = capabilities.get("defaults", {})
    if not isinstance(defaults, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, value in defaults.items():
        if isinstance(key, str) and isinstance(value, str) and key and value:
            normalized[key] = value
    return normalized


def get_authentication_config(project_root: Path | None = None) -> dict[str, Any]:
    """Return the root-level authentication config from phlo.yaml.

    Expected shape:

        authentication:
          provider: proxy
          proxy:
            trusted_proxies:
              - 127.0.0.1/32

    Returns {} when unconfigured; raises ValueError when the block is not a
    mapping.
    """
    if project_root is None:
        project_root = _default_project_root()

    project_config = load_project_config(project_root)
    if not isinstance(project_config, dict) or not project_config:
        return {}

    auth_config = project_config.get("authentication")
    if auth_config is None:
        return {}
    if not isinstance(auth_config, dict):
        raise ValueError("phlo.yaml authentication must be a mapping")

    return auth_config


def get_regulated_config(project_root: Path | None = None) -> bool | None:
    """Return the root-level regulated mode setting from phlo.yaml.

    Expected shape:

        regulated: true

    Returns True/False when configured, otherwise None. Falls back to the
    deprecated ``regulated_mode`` key with a DeprecationWarning; raises
    ValueError when the value is not a boolean.
    """
    if project_root is None:
        project_root = _default_project_root()

    project_config = load_project_config(project_root)
    if not isinstance(project_config, dict) or not project_config:
        return None

    value = project_config.get("regulated")
    if value is None:
        deprecated_value = project_config.get("regulated_mode")
        if deprecated_value is not None:
            import warnings

            warnings.warn(
                "phlo.yaml 'regulated_mode' is deprecated, use 'regulated' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning(
                "deprecated_config_key",
                old="regulated_mode",
                new="regulated",
                message="phlo.yaml 'regulated_mode' is deprecated, use 'regulated' instead",
            )
            value = deprecated_value
    if value is None:
        return None
    if isinstance(value, bool):
        return value

    raise ValueError("phlo.yaml 'regulated' must be a boolean")


def get_regulated_mode_config(project_root: Path | None = None) -> bool | None:
    """Deprecated: use get_regulated_config() instead."""
    import warnings

    warnings.warn(
        "get_regulated_mode_config() is deprecated, use get_regulated_config() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_regulated_config(project_root)


def get_authentication_provider_config(project_root: Path | None = None) -> str | None:
    """Return the configured authentication provider name from phlo.yaml.

    Expected shape:

        authentication:
          provider: proxy

    Returns None when unconfigured; raises ValueError when the provider is
    empty or invalid.
    """
    if project_root is None:
        project_root = _default_project_root()

    auth_config = get_authentication_config(project_root)
    if not auth_config:
        return None

    provider = auth_config.get("provider")
    if provider is None:
        return None
    if not isinstance(provider, str):
        raise ValueError("phlo.yaml authentication.provider must be a string")

    normalized = provider.strip()
    if not normalized:
        raise ValueError("phlo.yaml authentication.provider cannot be empty")
    return normalized


def get_configured_authentication_provider_name(project_root: Path | None = None) -> str | None:
    """Return the one runtime authentication provider selection.

    ``PHLO_AUTHENTICATION_METHOD`` is the legacy environment spelling, so it
    remains supported only when it agrees with the canonical provider setting.
    Rejecting disagreement here keeps startup validation and every runtime
    resolver on one authoritative selection.
    """
    method = os.environ.get("PHLO_AUTHENTICATION_METHOD", "").strip()
    provider = os.environ.get("PHLO_AUTHENTICATION_PROVIDER", "").strip()
    configured = (get_authentication_provider_config(project_root) or "").strip()
    selections = [
        ("PHLO_AUTHENTICATION_METHOD", method),
        ("PHLO_AUTHENTICATION_PROVIDER", provider),
        ("phlo.yaml authentication.provider", configured),
    ]
    selected = [(source, value) for source, value in selections if value]
    if selected and any(value.lower() != selected[0][1].lower() for _, value in selected[1:]):
        raise ValueError(
            "Conflicting authentication settings: provider selection must match across "
            "environment and phlo.yaml"
        )
    return selected[0][1] if selected else None


def get_configured_authorization_backend_name(project_root: Path | None = None) -> str | None:
    """Return the one runtime authorization backend selection.

    Environment and project configuration are separate inputs to startup, so
    disagreement must fail rather than letting validation inspect one backend
    while enforcement silently resolves another.
    """
    from_config = get_api_authorization_config(project_root)
    configured = (from_config.backend if from_config and from_config.backend else "").strip()
    from_env = os.environ.get("PHLO_AUTHORIZATION_BACKEND", "").strip()
    if from_env and configured and from_env.lower() != configured.lower():
        raise ValueError(
            "Conflicting authorization settings: PHLO_AUTHORIZATION_BACKEND and "
            "phlo.yaml authorization backend must match"
        )
    return from_env or configured or None


def get_api_authorization_config(project_root: Path | None = None) -> ApiAuthorizationConfig | None:
    """Return validated phlo-api authorization settings from phlo.yaml.

    Precedence inside phlo.yaml is:
    1. services.phlo-api.authorization
    2. api.authorization
    """
    project_config = load_project_config(project_root)
    if not isinstance(project_config, dict) or not project_config:
        return None

    services = project_config.get("services", {})
    if isinstance(services, dict):
        phlo_api_service = services.get("phlo-api")
        if isinstance(phlo_api_service, dict):
            service_auth = phlo_api_service.get("authorization")
            if isinstance(service_auth, dict):
                return ApiAuthorizationConfig(**service_auth)

    api_config = project_config.get("api")
    if isinstance(api_config, dict):
        validated = ApiConfig(**api_config)
        return validated.authorization

    return None


def get_service_config(service_key: str, project_root: Path | None = None) -> ServiceConfig | None:
    """Get configuration for a specific service."""
    infra = load_infrastructure_config(project_root)
    return infra.get_service(service_key)


def get_container_name(
    service_key: str,
    project_name: str,
    project_root: Path | None = None,
) -> str | None:
    """Get container name for a service."""
    infra = load_infrastructure_config(project_root)
    return infra.get_container_name(service_key, project_name)


def clear_config_cache() -> None:
    """Clear the configuration cache."""
    load_project_config.cache_clear()
    load_infrastructure_config.cache_clear()
    load_wap_config.cache_clear()
