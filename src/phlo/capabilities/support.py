"""Structured capability support metadata.

CapabilitySupport lets providers advertise optional behaviour without forcing
every implementation to fake advanced semantics; coerce_capability_support
normalises raw payloads or returns all-False defaults.

Shared foundation of phlo.capabilities: resolver, specs, runtime, and plugin base all import it.
Defines the CapabilitySupport contract and imports nothing else from phlo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CapabilitySupport:
    """Describe concrete guarantees a provider supports.

    These flags let providers advertise optional behavior without forcing
    every implementation to fake advanced semantics.
    """

    supports_refs: bool = False
    supports_snapshots: bool = False
    supports_schema_evolution: bool = False
    supports_atomic_validation: bool = False
    supports_promote: bool = False
    supports_time_travel: bool = False
    supports_metrics: bool = False
    supports_logs: bool = False
    supports_dashboards: bool = False
    supports_alerts: bool = False
    supports_permissions: bool = False
    supports_attributes: bool = False

    def to_dict(self) -> dict[str, bool]:
        """Return the support metadata as a plain dictionary."""
        return asdict(self)


def coerce_capability_support(
    value: CapabilitySupport | Mapping[str, Any] | None,
) -> CapabilitySupport:
    """Normalize raw support metadata into ``CapabilitySupport``."""
    if isinstance(value, CapabilitySupport):
        return value
    if value is None:
        return CapabilitySupport()
    if isinstance(value, Mapping):
        allowed_keys = {
            "supports_refs",
            "supports_snapshots",
            "supports_schema_evolution",
            "supports_atomic_validation",
            "supports_promote",
            "supports_time_travel",
            "supports_metrics",
            "supports_logs",
            "supports_dashboards",
            "supports_alerts",
            "supports_permissions",
            "supports_attributes",
        }
        payload = {key: bool(raw_value) for key, raw_value in value.items() if key in allowed_keys}
        return CapabilitySupport(**payload)
    raise TypeError(f"Unsupported capability support payload: {type(value)!r}")
