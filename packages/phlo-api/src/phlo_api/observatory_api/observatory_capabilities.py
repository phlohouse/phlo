"""Capability inventory helpers for the Observatory API.

Projects registered capabilities and UI contributions into provider-neutral
inventory models. Provider-native links stay suppressed until an explicit
public link policy exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from phlo_api.observatory_api.observatory_models import (
    ObservatoryCapabilityInventory,
    ObservatoryCapabilityProvider,
    ObservatoryCapabilitySupport,
    ObservatoryExternalLink,
    ObservatoryHealth,
    ObservatoryRouteRequirement,
    ObservatoryUiContribution,
)
from phlo_api.observatory_api.observatory_metadata import safe_metadata

CAPABILITY_FAMILIES = (
    "query_engine",
    "table_store",
    "catalog",
    "catalog_scanner",
    "object_store",
    "quality_backend",
    "maintenance_read_model",
    "orchestrator",
    "orchestrator_operations",
    "metadata_catalog",
    "lineage_sink",
    "governance_backend",
    "authorization_policy_backend",
    "authentication_provider",
    "publish_target",
    "alert_sink",
    "api_backend",
    "observability_backend",
    "regulated_surface",
    "observatory_extension",
)

ROUTE_REQUIREMENTS = [
    ObservatoryRouteRequirement(
        route_id="overview",
        label="Overview",
        path="/",
        reason="Core lakehouse attention surface.",
    ),
    ObservatoryRouteRequirement(
        route_id="tables",
        label="Tables",
        path="/tables",
        required_any=["query_engine", "table_store"],
        reason="Install a query engine or table store to browse physical tables.",
    ),
    ObservatoryRouteRequirement(
        route_id="workflows",
        label="Workflows",
        path="/workflows/new",
        reason="Core guided workflow creation surface.",
    ),
    ObservatoryRouteRequirement(
        route_id="lineage",
        label="Lineage",
        path="/lineage",
        required_any=["query_engine", "table_store", "lineage_sink"],
        optional=["quality_backend", "maintenance_read_model"],
        reason="Install lineage metadata or lineage providers to inspect impact.",
    ),
    ObservatoryRouteRequirement(
        route_id="quality",
        label="Quality",
        path="/quality",
        required_any=["quality_backend"],
        reason="Install a quality provider to triage checks.",
    ),
    ObservatoryRouteRequirement(
        route_id="logs",
        label="Logs",
        path="/logs",
        optional=["observability_backend", "maintenance_read_model"],
        reason="Core Phlo log evidence surface.",
    ),
    ObservatoryRouteRequirement(
        route_id="branches",
        label="Change Review",
        path="/branches",
        required_any=["catalog"],
        optional=["table_store"],
        reason="Install a catalog provider with refs to compare changes.",
    ),
    ObservatoryRouteRequirement(
        route_id="operations",
        label="Operations",
        path="/operations",
        required_any=["maintenance_read_model"],
        reason="Install a maintenance read-model provider to inspect operations.",
    ),
    ObservatoryRouteRequirement(
        route_id="runs",
        label="Runs",
        path="/runs",
        required_any=["orchestrator"],
        optional=["maintenance_read_model"],
        reason="Install an orchestrator provider to inspect run history.",
    ),
    ObservatoryRouteRequirement(
        route_id="storage",
        label="Storage",
        path="/storage",
        required_any=["table_store", "object_store"],
        nav=False,
        reason="Install a table store or object store to inspect storage surfaces.",
    ),
    ObservatoryRouteRequirement(
        route_id="observability",
        label="Observability",
        path="/observability",
        required_any=["observability_backend"],
        optional=["alert_sink"],
        nav=False,
        reason="Install an observability backend to inspect telemetry surfaces.",
    ),
    ObservatoryRouteRequirement(
        route_id="governance",
        label="Governance",
        path="/governance",
        required_any=[
            "governance_backend",
            "authorization_policy_backend",
            "authentication_provider",
            "regulated_surface",
        ],
        reason="Install a governance or policy provider to inspect regulated surfaces.",
    ),
    ObservatoryRouteRequirement(
        route_id="datasets",
        label="Datasets",
        path="/datasets",
        required_any=["metadata_catalog", "catalog_scanner"],
        reason="Install a metadata catalog or scanner to inspect datasets.",
    ),
    ObservatoryRouteRequirement(
        route_id="publishing",
        label="Publishing",
        path="/publishing",
        required_any=["publish_target", "metadata_catalog", "catalog_scanner"],
        optional=["authorization_policy_backend"],
        reason="Install a publish target or Dataset registry to inspect publishing readiness.",
    ),
    ObservatoryRouteRequirement(
        route_id="pipelines",
        label="Pipelines",
        path="/pipelines",
        required_any=["orchestrator", "orchestrator_operations", "maintenance_read_model"],
        optional=["quality_backend"],
        reason="Install an orchestrator or operations provider to inspect Dataset pipelines.",
    ),
    ObservatoryRouteRequirement(
        route_id="apis",
        label="APIs",
        path="/apis",
        required_any=["api_backend"],
        nav=False,
        reason="Install an API backend to inspect published API surfaces.",
    ),
    ObservatoryRouteRequirement(
        route_id="bi",
        label="BI",
        path="/bi",
        required_any=["publish_target"],
        optional=["query_engine"],
        nav=False,
        reason="Install a publish target to inspect BI surfaces.",
    ),
    ObservatoryRouteRequirement(
        route_id="extensions",
        label="Extensions",
        path="/extensions",
        nav=False,
        reason="Core extension inventory and settings surface.",
    ),
    ObservatoryRouteRequirement(
        route_id="services",
        label="Services",
        path="/services",
        reason="Core runtime service inventory.",
    ),
    ObservatoryRouteRequirement(
        route_id="settings",
        label="Settings",
        path="/settings",
        reason="Core Observatory settings.",
    ),
]


def build_capability_inventory(registry: Any | None) -> ObservatoryCapabilityInventory:
    """Build the full provider-neutral capability inventory."""
    providers: dict[str, list[ObservatoryCapabilityProvider]] = {}
    ui_contributions: list[ObservatoryUiContribution] = []
    if registry is not None:
        for capability_type in CAPABILITY_FAMILIES:
            providers[capability_type] = _providers_for_type(registry, capability_type)
        ui_contributions = _ui_contributions(registry)
    return ObservatoryCapabilityInventory(
        providers=providers,
        requirements=ROUTE_REQUIREMENTS,
        ui_contributions=ui_contributions,
    )


def _providers_for_type(
    registry: Any,
    capability_type: str,
) -> list[ObservatoryCapabilityProvider]:
    method = getattr(registry, "list", None)
    if not callable(method):
        return []
    try:
        specs = method(capability_type)
    except Exception:
        return []
    return [_provider_from_spec(capability_type, spec) for spec in specs]


def _provider_from_spec(capability_type: str, spec: Any) -> ObservatoryCapabilityProvider:
    raw_metadata = getattr(spec, "metadata", {})
    metadata = safe_metadata(raw_metadata)
    name = str(getattr(spec, "name", capability_type))
    return ObservatoryCapabilityProvider(
        capability_type=capability_type,
        name=name,
        display_name=str(metadata.get("display_name") or metadata.get("service_type") or name),
        package=str(metadata.get("package")) if metadata.get("package") else None,
        metadata=metadata,
        support=_support_from_spec(getattr(spec, "support", None)),
        health=ObservatoryHealth(state="unknown", message="registered"),
        native_links=_native_links(raw_metadata),
    )


def _support_from_spec(value: Any) -> ObservatoryCapabilitySupport:
    if value is None:
        return ObservatoryCapabilitySupport()
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        payload = {}
    return ObservatoryCapabilitySupport(**{key: bool(raw) for key, raw in payload.items()})


def _native_links(metadata: Any) -> list[ObservatoryExternalLink]:
    # Provider-native links often contain internal hosts or credentials. Keep the
    # capability inventory provider-neutral until explicit public link policy exists.
    return []


def _ui_contributions(registry: Any) -> list[ObservatoryUiContribution]:
    method = getattr(registry, "list", None)
    if not callable(method):
        return []
    try:
        specs = method("ui_contribution")
    except Exception:
        return []
    if isinstance(specs, str):
        return []
    try:
        iterator = iter(specs)
    except TypeError:
        return []

    contributions: list[ObservatoryUiContribution] = []
    for spec in iterator:
        contribution = _ui_contribution_from_spec(spec)
        if contribution is not None:
            contributions.append(contribution)
    return contributions


def _ui_contribution_from_spec(spec: Any) -> ObservatoryUiContribution | None:
    try:
        return ObservatoryUiContribution(
            name=_required_string(spec, "name"),
            capability_type=_required_string(spec, "capability_type"),
            capability_name=_required_string(spec, "capability_name"),
            surfaces=_string_list(_spec_value(spec, "surfaces", [])),
            read_models=_string_mapping(_spec_value(spec, "read_models", {})),
            actions=_string_list(_spec_value(spec, "actions", [])),
            # Provider-native UI links can expose internal hosts or secrets.
            # Keep them out until Observatory has an explicit public-link policy.
            native_links=[],
            metadata=safe_metadata(_spec_value(spec, "metadata", {})),
        )
    except Exception:
        return None


def _spec_value(spec: Any, key: str, default: Any = None) -> Any:
    if isinstance(spec, Mapping):
        if key in spec:
            return spec[key]
        if default is not None:
            return default
        raise KeyError(key)
    return getattr(spec, key) if default is None else getattr(spec, key, default)


def _required_string(spec: Any, key: str) -> str:
    value = _spec_value(spec, key)
    if value is None:
        raise ValueError(f"Missing UI contribution field: {key}")
    text = str(value)
    if not text:
        raise ValueError(f"Empty UI contribution field: {key}")
    return text


def _string_list(value: Any) -> list[str]:
    if value is None or isinstance(value, str | Mapping):
        return []
    try:
        return [str(item) for item in value if item is not None]
    except TypeError:
        return []


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(raw_value)
        for key, raw_value in value.items()
        if key is not None and raw_value is not None
    }
