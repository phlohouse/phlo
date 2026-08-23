"""Hook plugin interfaces and registration types.

Hooks register as frozen HookRegistration records with priority ordering,
optional event/asset/tag filters, and a FailurePolicy deciding whether a
handler error is ignored, logged (default), or raised. Handlers may be sync
callables, async callables, or HookProvider plugins.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from phlo.hooks.events import HookEvent
from phlo.plugins.base import Plugin


class FailurePolicy(StrEnum):
    """Failure handling policy for hook handlers."""

    IGNORE = "ignore"
    LOG = "log"
    RAISE = "raise"


@dataclass(frozen=True)
class HookFilter:
    """Filter criteria for deciding whether a hook should run."""

    event_types: set[str] | None = None
    asset_keys: set[str] | None = None
    tags: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Normalize iterable fields to sets for efficient matching."""

        if self.event_types is not None:
            object.__setattr__(self, "event_types", set(self.event_types))
        if self.asset_keys is not None:
            object.__setattr__(self, "asset_keys", set(self.asset_keys))
        if self.tags is not None:
            object.__setattr__(self, "tags", dict(self.tags))


@dataclass(frozen=True)
class HookRegistration:
    """Registration details for a hook handler."""

    hook_name: str
    handler: (
        Callable[[HookEvent], None]
        | Callable[[HookEvent], Awaitable[None]]
        | HookHandler
        | AsyncHookHandler
    )
    priority: int = 100
    filters: HookFilter | None = None
    failure_policy: FailurePolicy = FailurePolicy.LOG


@runtime_checkable
class HookProvider(Protocol):
    """Protocol for plugins that expose hook registrations."""

    def get_hooks(self) -> Iterable[HookRegistration]:
        """Return hook registrations exposed by the implementing plugin."""

        ...


@runtime_checkable
class HookHandler(Protocol):
    """Protocol for handler objects implementing hook dispatch."""

    def handle_event(self, event: HookEvent) -> None:
        """Handle a hook event emitted by the hook bus."""

        ...


@runtime_checkable
class AsyncHookHandler(Protocol):
    """Protocol for async handler objects implementing hook dispatch."""

    async def handle_event_async(self, event: HookEvent) -> None:
        """Handle a hook event emitted by the async hook bus."""

        ...


class HookPlugin(Plugin, HookProvider):
    """Base class for hook-only plugins."""

    def get_hooks(self) -> Iterable[HookRegistration]:
        """Return hook registrations for this plugin."""

        return []
