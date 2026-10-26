"""Runtime context protocol and routing resolution shared across capabilities.

RuntimeRouting is the canonical routing record; ref, partition, and capability
overrides are recovered from orchestrator tags using fixed key precedence so
every capability resolves identical values from the same tag set.
Imported by phlo-dagster (adapter), phlo-dlt (decorator/executor), and
phlo-pandera; resolves routing identity via phlo._attempt and phlo._correlation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from phlo._attempt import attempt_from_tags
from phlo._correlation import resolve_project_identity
from phlo.capabilities.support import CapabilitySupport


@dataclass(frozen=True, slots=True)
class DataInterval:
    """A half-open extraction window [start, end), stored as UTC instants.

    This is an orchestration window, not a physical table partition transform.
    Aware endpoints are required; local calendar windows belong to the adapter.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Data interval endpoints must be timezone-aware")
        start = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        if start >= end:
            raise ValueError("Data interval start must precede end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True, slots=True)
class RuntimeRouting:
    """Canonical runtime routing data shared across capabilities."""

    environment: str | None = None
    ref: str | None = None
    partition_key: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    project_error: str | None = None
    attempt: int | None = 1
    attempt_error: str | None = None
    resources: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, str] = field(default_factory=dict)
    capability_overrides: dict[str, str] = field(default_factory=dict)
    data_interval: DataInterval | None = None
    interval_mode: Literal["scheduled", "replay"] = "scheduled"

    def __post_init__(self) -> None:
        if self.interval_mode not in ("scheduled", "replay"):
            raise ValueError("Interval mode must be 'scheduled' or 'replay'")


def capability_overrides_from_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """Extract capability overrides from canonical runtime tags."""
    overrides: dict[str, str] = {}
    for key, value in tags.items():
        if not value:
            continue
        if key.startswith("phlo/capability/"):
            capability_type = key.removeprefix("phlo/capability/")
        elif key.startswith("capability/"):
            capability_type = key.removeprefix("capability/")
        else:
            continue
        capability_type = capability_type.strip()
        if capability_type:
            overrides[capability_type] = value
    return overrides


def runtime_ref_from_tags(tags: Mapping[str, object]) -> str | None:
    """Resolve the first non-blank runtime ref using canonical precedence."""
    for key in ("phlo/wap_branch", "phlo/ref", "ref", "branch"):
        value = tags.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


class RuntimeContext(Protocol):
    """Orchestrator-agnostic runtime context."""

    run_id: str | None
    partition_key: str | None
    tags: dict[str, str]

    @property
    def logger(self) -> Any:
        """Return the orchestrator-provided logger."""
        ...

    @property
    def resources(self) -> dict[str, Any]:
        """Return runtime resources keyed by resource name."""
        ...

    @property
    def routing(self) -> RuntimeRouting:
        """Return canonical runtime routing information."""
        ...

    def get_resource(self, name: str) -> Any:
        """Return the runtime resource registered under the given name."""
        ...


def routing_from_context(context: RuntimeContext) -> RuntimeRouting:
    """Build canonical routing data from a runtime context.

    This helper keeps the initial migration path lightweight for existing
    contexts that expose only tags, resources, and the standard protocol
    fields.
    """
    direct_routing = getattr(context, "routing", None)
    if isinstance(direct_routing, RuntimeRouting):
        return direct_routing

    resources_value = getattr(context, "resources", {})
    resources = dict(resources_value) if isinstance(resources_value, Mapping) else {}

    tags_value = getattr(context, "tags", {})
    tags = (
        {str(key): str(value) for key, value in tags_value.items()}
        if isinstance(tags_value, Mapping)
        else {}
    )
    feature_flags = {
        key.removeprefix("feature/"): value
        for key, value in tags.items()
        if key.startswith("feature/")
    }
    capability_overrides = capability_overrides_from_tags(tags)
    environment = tags.get("environment") or tags.get("env")
    ref = runtime_ref_from_tags(tags)

    attempt, attempt_error = attempt_from_tags(tags)
    project = resolve_project_identity(tags)
    return RuntimeRouting(
        environment=environment,
        ref=ref,
        partition_key=getattr(context, "partition_key", None),
        run_id=getattr(context, "run_id", None),
        project_id=project.project_id,
        project_error=project.error,
        attempt=attempt,
        attempt_error=attempt_error,
        resources=resources,
        feature_flags=feature_flags,
        capability_overrides=capability_overrides,
        data_interval=getattr(context, "data_interval", None),
        interval_mode=getattr(context, "interval_mode", "scheduled"),
    )


def resolve_runtime_ref(
    context: RuntimeContext | None,
    *,
    support: CapabilitySupport | None = None,
    default_ref: str | None = None,
) -> str | None:
    """Resolve the effective ref for a capability from runtime routing and support metadata.

    Returns None when support marks refs unsupported; otherwise prefers the
    routing ref from context, falling back to default_ref when refs are
    supported but the runtime omitted one.
    """
    if support is not None and not support.supports_refs:
        return None
    if context is not None:
        routing = routing_from_context(context)
        if routing.ref:
            return routing.ref
    return default_ref
