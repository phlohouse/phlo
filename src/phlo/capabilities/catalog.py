"""Shared catalog storage for capability registrations.

CapabilityFamily is plain keyed dict storage; CapabilityFamilyDefinition layers
on the family's spec type, key function, and optional provider-method hook so
specs can be harvested straight off provider instances.

Imported within the capabilities package as its specification catalog.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

SpecT = TypeVar("SpecT")
KeyT = TypeVar("KeyT", bound=Hashable)


class NamedSpec(Protocol):
    """Protocol for specs addressable by a string name."""

    name: str


NamedSpecT = TypeVar("NamedSpecT", bound=NamedSpec)


@dataclass(slots=True)
class CapabilityFamily(Generic[SpecT, KeyT]):
    """Registration storage for one capability family."""

    key: Callable[[SpecT], KeyT]
    _items: dict[KeyT, SpecT] = field(default_factory=dict)

    def register(self, spec: SpecT) -> None:
        """Store spec under its family key, replacing any existing entry."""
        self._items[self.key(spec)] = spec

    def list(self) -> builtins.list[SpecT]:
        """Return registered specs in registration order."""
        return builtins.list(self._items.values())

    def clear(self) -> None:
        """Remove all registered specs."""
        self._items.clear()


def named_family() -> CapabilityFamily[NamedSpecT, str]:
    """Create a capability family keyed by each spec's name attribute."""
    return CapabilityFamily(key=lambda spec: spec.name)


@dataclass(frozen=True, slots=True)
class CapabilityFamilyDefinition(Generic[SpecT, KeyT]):
    """Metadata describing one named capability family."""

    name: str
    spec_type: type[SpecT]
    key: Callable[[SpecT], KeyT]
    provider_method: str | None = None

    def family(self) -> CapabilityFamily[SpecT, KeyT]:
        """Create empty storage for this capability family."""
        return CapabilityFamily(key=self.key)

    def provider_specs(self, provider: Any) -> list[SpecT]:
        """Return specs exposed by a provider for this family."""
        if self.provider_method is None:
            return []
        method = getattr(provider, self.provider_method)
        return list(method())
