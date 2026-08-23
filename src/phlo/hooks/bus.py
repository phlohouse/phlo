"""Hook bus dispatching events to registered plugin handlers.

Handlers run in ascending priority order with deterministic tie-breaking;
per-handler failure policy decides whether an error aborts dispatch or lets
remaining handlers run. Core hook providers register lazily on first emit.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from phlo.hooks.events import HookEvent
from phlo.logging import get_logger
from phlo.plugins.hooks import (
    AsyncHookHandler,
    FailurePolicy,
    HookFilter,
    HookHandler,
    HookProvider,
    HookRegistration,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class RegisteredHook:
    """Internal record for a registered hook handler."""

    plugin_name: str
    hook_name: str
    handler: (
        Callable[[HookEvent], None]
        | Callable[[HookEvent], Awaitable[None]]
        | HookHandler
        | AsyncHookHandler
    )
    priority: int
    filters: HookFilter | None
    failure_policy: FailurePolicy


class HookBus:
    """Dispatch hook events to registered handlers.

    Handlers run in ascending priority order, with ties broken by plugin name
    then hook name so dispatch order is fully deterministic. A handler failure
    stops dispatch only when the failure policy says so; IGNORE and LOG let
    remaining handlers run.
    """

    def __init__(self) -> None:
        """Initialize hook bus storage and lazy-discovery state."""
        self._hooks: list[RegisteredHook] = []
        self._discovered = False

    def emit(self, event: HookEvent) -> None:
        """Emit an event to all matching hooks."""
        self._ensure_discovered()
        for hook in sorted(
            self._hooks, key=lambda item: (item.priority, item.plugin_name, item.hook_name)
        ):
            if hook.filters and not self._matches_filters(hook.filters, event):
                continue
            # TypeErrors always propagate and never reach the failure policy:
            # they signal a dispatch misuse such as an async handler invoked
            # through the sync path, which no policy should absorb. Handler
            # bugs surface as any other exception type instead.
            try:
                self._invoke_handler(hook.handler, event)
            except TypeError:
                raise
            except Exception as exc:
                if self._handle_failure(hook=hook, error=exc):
                    continue
                raise

    async def emit_async(self, event: HookEvent) -> None:
        """Emit an event asynchronously to all matching hooks."""
        self._ensure_discovered()
        for hook in sorted(
            self._hooks, key=lambda item: (item.priority, item.plugin_name, item.hook_name)
        ):
            if hook.filters and not self._matches_filters(hook.filters, event):
                continue
            try:
                await self._invoke_handler_async(hook.handler, event)
            except TypeError:
                raise
            except Exception as exc:
                if self._handle_failure(hook=hook, error=exc):
                    continue
                raise

    def register(self, registration: HookRegistration, *, plugin_name: str) -> None:
        """Register a hook handler."""
        self._hooks.append(
            RegisteredHook(
                plugin_name=plugin_name,
                hook_name=registration.hook_name,
                handler=registration.handler,
                priority=registration.priority,
                filters=registration.filters,
                failure_policy=registration.failure_policy,
            )
        )

    def register_provider(self, provider: HookProvider, *, plugin_name: str | None = None) -> None:
        """Register hooks from a provider."""
        resolved_name = plugin_name or _resolve_plugin_name(provider) or "unknown"
        for hook in provider.get_hooks():
            self.register(hook, plugin_name=resolved_name)

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks.clear()
        self._discovered = False

    def _ensure_discovered(self) -> None:
        """Discover plugins and register hook providers on first use."""
        if self._discovered:
            return
        from phlo.hooks.telemetry import CoreTelemetryHookProvider
        from phlo.plugins.discovery import discover_plugins, get_global_registry
        from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider

        self.register_provider(CoreTelemetryHookProvider(), plugin_name="core")
        self.register_provider(CoreRunEvidenceHookProvider(), plugin_name="core")
        discover_plugins(auto_register=True)
        registry = get_global_registry()
        for plugin in registry.iter_plugins():
            if isinstance(plugin, HookProvider):
                self.register_provider(plugin)
        self._discovered = True

    @staticmethod
    def _invoke_handler(
        handler: (
            Callable[[HookEvent], None]
            | Callable[[HookEvent], Awaitable[None]]
            | HookHandler
            | AsyncHookHandler
        ),
        event: HookEvent,
    ) -> None:
        """Dispatch a hook handler regardless of implementation style."""
        if isinstance(handler, AsyncHookHandler):
            raise TypeError(
                "Async hook handler requires HookBus.emit_async(). "
                "Use a sync handler or call emit_async for this event."
            )
        if isinstance(handler, HookHandler):
            handler.handle_event(event)
            return
        result = handler(event)
        # Close the orphaned coroutine before raising so it does not linger
        # until garbage collection and emit a "never awaited" warning.
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError(
                "Async hook function requires HookBus.emit_async(). "
                "Use a sync handler or call emit_async for this event."
            )

    @staticmethod
    async def _invoke_handler_async(
        handler: (
            Callable[[HookEvent], None]
            | Callable[[HookEvent], Awaitable[None]]
            | HookHandler
            | AsyncHookHandler
        ),
        event: HookEvent,
    ) -> None:
        """Dispatch a hook handler from an async execution context."""
        if isinstance(handler, AsyncHookHandler):
            await handler.handle_event_async(event)
            return
        if isinstance(handler, HookHandler):
            handler.handle_event(event)
            return
        result = handler(event)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _handle_failure(*, hook: RegisteredHook, error: Exception) -> bool:
        """Apply failure policy, returning True when dispatch should continue."""
        policy = hook.failure_policy
        if policy == FailurePolicy.IGNORE:
            return True
        if policy == FailurePolicy.LOG:
            logger.exception(
                "Hook failed: %s.%s (%s)",
                hook.plugin_name,
                hook.hook_name,
                error,
            )
            return True
        return False

    @staticmethod
    def _matches_filters(filters: HookFilter, event: HookEvent) -> bool:
        """Return whether an event satisfies the provided hook filters."""
        if filters.event_types and event.event_type not in filters.event_types:
            return False
        if filters.asset_keys:
            event_asset_keys = _event_asset_keys(event)
            if not event_asset_keys or not filters.asset_keys.intersection(event_asset_keys):
                return False
        return filters.tags is None or all(event.tags.get(k) == v for k, v in filters.tags.items())


def _event_asset_keys(event: HookEvent) -> set[str]:
    """Collect asset keys from hook event payloads."""
    keys: set[str] = set()
    asset_key = getattr(event, "asset_key", None)
    if isinstance(asset_key, str):
        keys.add(asset_key)
    asset_keys = getattr(event, "asset_keys", None)
    if isinstance(asset_keys, Iterable) and not isinstance(asset_keys, (str, bytes)):
        for item in asset_keys:
            if isinstance(item, str):
                keys.add(item)
    return keys


def _resolve_plugin_name(provider: Any) -> str | None:
    """Resolve a plugin name from a provider metadata attribute."""
    metadata = getattr(provider, "metadata", None)
    if metadata is None:
        return None
    return getattr(metadata, "name", None)


_GLOBAL_HOOK_BUS = HookBus()


def get_hook_bus() -> HookBus:
    """Return the global hook bus singleton."""
    return _GLOBAL_HOOK_BUS
