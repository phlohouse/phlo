"""Capability discovery for asset and resource providers.

discover_capabilities() registers built-in authentication, authorization,
and observability providers first, then auto-registers discovered
plugins and converts each provider's specs into capability registrations.
A provider that raises for missing required config is an expected
deployment shape: it is logged as a warning without a traceback and
does not abort discovery of other providers.
Imported by the phlo-api backend and phlo-dagster framework to populate the capability registry.
"""

from __future__ import annotations

from typing import Any

from phlo.capabilities.authentication import (
    register_default_capability_providers as register_default_authentication_providers,
)
from phlo.capabilities.authorization import (
    register_default_capability_providers as register_default_authorization_providers,
)
from phlo.capabilities.observability import (
    register_default_capability_providers as register_default_observability_providers,
)
from phlo.capabilities.registry import (
    iter_provider_capabilities,
    register_capability,
)
from phlo.logging import get_logger
from phlo.plugins.discovery import discover_plugins, get_global_registry

logger = get_logger(__name__)


def discover_capabilities() -> None:
    """Discover capability providers and register their specs."""
    logger.info("capability_discovery_started")
    register_default_authentication_providers()
    from phlo.infrastructure.config import _default_project_root
    from phlo.rbac.config import RBACConfigLoader

    try:
        canonical_rbac = RBACConfigLoader(_default_project_root() / ".phlo").load()
    except (FileNotFoundError, ValueError):
        canonical_rbac = None
    register_default_authorization_providers(rbac=canonical_rbac)
    register_default_observability_providers()
    discover_plugins(plugin_type="asset_provider", auto_register=True)
    discover_plugins(plugin_type="resource_provider", auto_register=True)
    discover_plugins(plugin_type="quality_provider", auto_register=True)
    discover_plugins(plugin_type="ingestion_provider", auto_register=True)
    discover_plugins(plugin_type="orchestrator", auto_register=True)

    registry = get_global_registry()

    provider_counts = {
        plugin_type: _register_capabilities_for_plugin_type(registry, plugin_type)
        for plugin_type in (
            "asset_provider",
            "resource_provider",
            "quality_provider",
            "ingestion_provider",
            "orchestrator",
        )
    }

    logger.info(
        "capability_discovery_completed",
        asset_provider_count=provider_counts["asset_provider"],
        resource_provider_count=provider_counts["resource_provider"],
        quality_provider_count=provider_counts["quality_provider"],
        ingestion_provider_count=provider_counts["ingestion_provider"],
        orchestrator_provider_count=provider_counts["orchestrator"],
    )


def _register_capabilities_for_plugin_type(registry: Any, plugin_type: str) -> int:
    provider_count = 0
    for name in registry.list(plugin_type):
        plugin = registry.get(plugin_type, name)
        if plugin is None:
            continue
        provider_count += 1
        try:
            for family, specs in iter_provider_capabilities(plugin):
                for spec in specs:
                    register_capability(family, spec)
        except Exception as exc:
            # A resource provider that raises about missing required config is
            # an expected deployment shape, not a defect: keep the warning but
            # skip the traceback.
            missing_optional_config = (
                plugin_type == "resource_provider" and "must be provided" in str(exc)
            )
            logger.warning(
                "capability_provider_registration_failed",
                plugin_type=plugin_type,
                provider_name=name,
                error=str(exc),
                exc_info=not missing_optional_config,
            )
    return provider_count
