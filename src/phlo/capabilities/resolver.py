"""Capability resolver for runtime provider selection.

Resolution is deterministic and never raises: an explicit name wins, then
runtime/env/config overrides, and an unnamed request only resolves when
exactly one provider is installed — otherwise it returns None so callers can
surface guidance. Also reports unsatisfied plugin capability requirements.
Imported by phlo core (capabilities package, migrations executor) and phlo-dagster to resolve
runtime capability providers deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from phlo.capabilities.registry import CapabilityRegistry, get_capability_registry
from phlo.capabilities.runtime import RuntimeContext, routing_from_context
from phlo.capabilities.support import CapabilitySupport
from phlo.config import get_settings
from phlo.infrastructure import get_capability_defaults_from_config
from phlo.logging import get_logger

if TYPE_CHECKING:
    from phlo.plugins.base.plugin import PluginMetadata

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResolutionResult:
    """Resolved provider object and metadata."""

    capability_type: str
    name: str
    provider: Any
    metadata: dict[str, Any]
    support: CapabilitySupport


def list_capabilities(
    capability_type: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[str]:
    """List registered capability names for a given capability type."""
    registry = registry or get_capability_registry()
    specs = registry.list(capability_type)
    logger.debug(
        "capability_listed",
        capability_type=capability_type,
        capability_count=len(specs),
    )
    return [spec.name for spec in specs]


def resolve_capability(
    capability_type: str,
    name: str | None = None,
    *,
    runtime: RuntimeContext | None = None,
    registry: CapabilityRegistry | None = None,
) -> ResolutionResult | None:
    """Resolve a capability provider by type and optional name.

    If ``name`` is omitted and exactly one provider is installed, resolve it.
    Otherwise return ``None`` so callers can surface deterministic guidance.
    """
    registry = registry or get_capability_registry()

    requested_name = name or configured_capability_name(capability_type, runtime=runtime)
    specs = registry.list(capability_type)
    if requested_name is not None:
        for spec in specs:
            if spec.name == requested_name:
                logger.debug(
                    "capability_resolved",
                    capability_type=capability_type,
                    capability_name=spec.name,
                )
                return ResolutionResult(
                    capability_type=capability_type,
                    name=spec.name,
                    provider=spec.provider,
                    metadata=spec.metadata,
                    support=spec.support,
                )
        logger.debug(
            "capability_resolution_name_not_found",
            capability_type=capability_type,
            requested_name=requested_name,
            available_names=[spec.name for spec in specs],
        )
        return None

    if len(specs) != 1:
        logger.debug(
            "capability_resolution_ambiguous",
            capability_type=capability_type,
            candidate_count=len(specs),
            candidate_names=[spec.name for spec in specs],
        )
        return None
    spec = specs[0]
    logger.debug(
        "capability_resolved_default",
        capability_type=capability_type,
        capability_name=spec.name,
    )
    return ResolutionResult(
        capability_type=capability_type,
        name=spec.name,
        provider=spec.provider,
        metadata=spec.metadata,
        support=spec.support,
    )


def configured_capability_name(
    capability_type: str,
    *,
    runtime: RuntimeContext | None = None,
) -> str | None:
    """Return the configured provider name for a capability type, if any."""
    if runtime is not None:
        routing = routing_from_context(runtime)
        override = routing.capability_overrides.get(capability_type)
        if override:
            return override
    env_override = get_settings().phlo_default_capabilities.get(capability_type)
    if env_override:
        return env_override
    return get_capability_defaults_from_config().get(capability_type)


def missing_required_capabilities(
    plugin: PluginMetadata,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[str]:
    """Return capability requirements that are currently unsatisfied."""
    registry = registry or get_capability_registry()
    missing: list[str] = []

    for capability in plugin.requires_capabilities:
        if ":" in capability:
            capability_type, expected_name = capability.split(":", 1)
            if resolve_capability(capability_type, expected_name, registry=registry) is None:
                missing.append(capability)
            continue

        if not list_capabilities(capability, registry=registry):
            missing.append(capability)

    if missing:
        logger.debug(
            "plugin_required_capabilities_missing",
            plugin_name=plugin.name,
            missing_capabilities=missing,
        )
    return missing
