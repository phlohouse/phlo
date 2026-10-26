"""Fail-closed WAP routing for a single configured code location/backend.

Environment selection selects a publication ref, not a provider factory.
Only providers with a known non-secret backend identity can participate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from phlo.capabilities.resolver import ResolutionResult, resolve_capability
from phlo.capabilities.runtime import capability_overrides_from_tags
from phlo.config.project import get_default_environment, get_environment_targets
from phlo.config_schema import EnvironmentTarget
from phlo.infrastructure import load_wap_config

WAP_BINDING_TAG = "phlo/wap_binding"


def _endpoint_identity(url: str) -> str:
    """Canonicalise an endpoint without retaining URL credentials or tokens."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("WAP requires an HTTP(S) backend endpoint")
    # Query-based routing cannot be distinguished safely from token parameters.
    if parsed.query or parsed.fragment:
        raise ValueError("WAP backend endpoints cannot contain a query or fragment")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}:{port}{parsed.path.rstrip('/')}"


def _backend_identity(resolution: ResolutionResult) -> str:
    provider = resolution.provider
    identity: dict[str, Any] = {
        "provider": resolution.name,
        "class": f"{type(provider).__module__}.{type(provider).__qualname__}",
    }
    if resolution.name == "nessie":
        identity["endpoint"] = _endpoint_identity(provider.base_url)
        identity["writers"] = _nessie_writer_identity(identity["endpoint"])
    elif resolution.name == "polaris":
        identity.update(provider.wap_backend_identity())
        identity["endpoint"] = _endpoint_identity(identity["endpoint"])
    else:
        raise ValueError("WAP cannot prove this catalog's backend identity")
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _nessie_writer_identity(endpoint: str) -> dict[str, Any]:
    """Bind supported process writers too; selecting a ref cannot rebuild them."""
    identity: dict[str, Any] = {}
    store = resolve_capability("table_store")
    if store is not None:
        if store.name != "iceberg":
            raise ValueError("WAP Nessie requires the process Iceberg table-store provider")
        config = store.provider.wap_backend_identity()
        if _endpoint_identity(str(config["uri"])) != f"{endpoint}/iceberg/main":
            raise ValueError("WAP Iceberg writer and catalog have different backend endpoints")
        identity["table_store"] = {key: config[key] for key in ("type", "warehouse", "s3.region")}
        identity["table_store"]["s3.endpoint"] = _endpoint_identity(str(config["s3.endpoint"]))
    engine = resolve_capability("query_engine")
    if engine is not None:
        if engine.name != "trino":
            raise ValueError("WAP Nessie cannot prove this query engine's backend routing")
        properties = engine.provider._dynamic_catalog_properties("main")
        if _endpoint_identity(properties["iceberg.nessie-catalog.uri"]) != f"{endpoint}/api/v2":
            raise ValueError("WAP query engine and catalog have different backend endpoints")
        identity["query_engine"] = engine.provider.wap_backend_identity()
    return identity


@dataclass(frozen=True)
class WapContext:
    """Launch facts that configuration changes may block, but never redirect."""

    environment: str
    target_ref: str
    location: str
    backend_id: str
    strategy: str = "branch"
    require_candidate_bound_checks: bool = False

    def to_dict(self) -> dict[str, Any]:
        context = asdict(self)
        # Preserve existing launch bindings when the opt-in policy is disabled.
        if not self.require_candidate_bound_checks:
            context.pop("require_candidate_bound_checks")
        return context


def select_wap_target(environment: str | None = None) -> tuple[str, EnvironmentTarget]:
    """Validate selection without discovering providers or probing availability."""
    targets = get_environment_targets()
    selected = get_default_environment() if environment is None else environment
    if selected not in targets:
        raise ValueError(f"Unknown WAP environment {selected!r}")
    target = targets[selected]
    wap = load_wap_config()
    if not wap.repository_location_name or target.dagster_location != wap.repository_location_name:
        raise ValueError(
            "WAP cross-location routing is unsupported; use the configured WAP location"
        )
    if wap.strategy == "snapshot" and target.nessie_ref != "main":
        raise ValueError("Snapshot WAP does not support environment-specific publication refs")
    return selected, target


def resolve_wap_context(
    environment: str | None = None, *, resolution: ResolutionResult | None = None
) -> WapContext:
    """Select configured explicit/default target without availability fallback."""
    selected, target = select_wap_target(environment)
    resolution = resolution or resolve_capability("catalog")
    if resolution is None:
        raise ValueError("WAP catalog provider is unavailable")
    return WapContext(
        selected,
        target.nessie_ref,
        target.dagster_location,
        _backend_identity(resolution),
        load_wap_config().strategy,
        load_wap_config().require_candidate_bound_checks,
    )


def run_location(run: Any) -> str | None:
    origin = getattr(run, "remote_job_origin", None)
    repository = getattr(origin, "repository_origin", None)
    location = getattr(getattr(repository, "code_location_origin", None), "location_name", None)
    return location if isinstance(location, str) and location else None


def validate_wap_context(run: Any, binding: dict[str, Any], catalog: Any = None) -> None:
    """Reject foreign runs, configuration drift and caller-controlled routing."""
    context = binding.get("context")
    if not isinstance(context, dict):
        raise ValueError("legacy_launch_context_missing: retain branch; review and relaunch")
    tags = run.tags or {}
    if capability_overrides_from_tags(tags):
        raise ValueError("WAP capability overrides are unsupported")
    branch = binding.get("branch")
    if any(tags.get(key, branch) != branch for key in ("ref", "branch")):
        raise ValueError("WAP routing tags conflict")
    if any(
        tags.get(key, context.get("environment")) != context.get("environment")
        for key in ("environment", "env")
    ):
        raise ValueError("WAP environment tags conflict")
    if run_location(run) != context.get("location"):
        raise ValueError("WAP run code location does not match launch context")
    resolution = resolve_capability("catalog")
    if resolution is None or (catalog is not None and resolution.provider is not catalog):
        raise ValueError("WAP process catalog does not match launch context")
    current = resolve_wap_context(context.get("environment"), resolution=resolution)
    if current.to_dict() != context:
        raise ValueError("WAP launch context configuration drift; retain branch and review")
