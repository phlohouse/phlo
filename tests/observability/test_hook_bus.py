"""Tests for hook bus behavior.

Locks the dispatch contract: filters gate delivery, lower priority numbers
run first, and the failure policy decides whether handler exceptions
propagate to the emitter.
"""

from typing import cast

import pytest
from phlo_testing.hooks import MockHookBus

from phlo.hooks import HookEvent, QualityResultEvent
from phlo.plugins.hooks import AsyncHookHandler, FailurePolicy, HookFilter, HookRegistration

pytestmark = pytest.mark.core_regression


def test_hook_bus_filters_and_ordering() -> None:
    """Verify hook execution order honors filters and priority."""
    bus = MockHookBus()
    calls: list[str] = []

    def handler_a(_event) -> None:
        """Record invocation of the lower-priority handler."""
        calls.append("a")

    def handler_b(_event) -> None:
        """Record invocation of the higher-priority handler."""
        calls.append("b")

    bus.register(
        HookRegistration(
            hook_name="handler_a",
            handler=handler_a,
            priority=10,
            filters=HookFilter(event_types={"quality.result"}, asset_keys={"asset"}),
        ),
        plugin_name="plugin_a",
    )
    bus.register(
        HookRegistration(
            hook_name="handler_b",
            handler=handler_b,
            priority=5,
            filters=HookFilter(event_types={"quality.result"}),
        ),
        plugin_name="plugin_b",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=True,
    )
    bus.emit(event)
    assert calls == ["b", "a"]


def test_hook_bus_failure_policy_raise() -> None:
    """Verify exceptions propagate when failure policy is set to raise."""
    bus = MockHookBus()

    def handler(_event) -> None:
        """Raise a fixed error to validate failure propagation behavior."""
        raise RuntimeError("boom")

    bus.register(
        HookRegistration(
            hook_name="raise_hook",
            handler=handler,
            failure_policy=FailurePolicy.RAISE,
        ),
        plugin_name="plugin_raise",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=False,
    )

    with pytest.raises(RuntimeError, match="boom"):
        bus.emit(event)


@pytest.mark.anyio
async def test_hook_bus_emit_async_supports_mixed_handlers() -> None:
    """Verify async emit supports sync, async function, and async object handlers."""
    bus = MockHookBus()
    calls: list[str] = []

    def sync_handler(_event) -> None:
        calls.append("sync")

    async def async_handler(_event) -> None:
        calls.append("async")

    class AsyncObjectHandler:
        async def handle_event_async(self, _event: HookEvent) -> None:
            calls.append("async-object")

    bus.register(
        HookRegistration(
            hook_name="sync_handler",
            handler=sync_handler,
            priority=20,
            filters=HookFilter(event_types={"quality.result"}),
        ),
        plugin_name="plugin_sync",
    )
    bus.register(
        HookRegistration(
            hook_name="async_handler",
            handler=async_handler,
            priority=10,
            filters=HookFilter(event_types={"quality.result"}),
        ),
        plugin_name="plugin_async",
    )
    bus.register(
        HookRegistration(
            hook_name="async_object_handler",
            handler=cast(AsyncHookHandler, AsyncObjectHandler()),
            priority=5,
            filters=HookFilter(event_types={"quality.result"}),
        ),
        plugin_name="plugin_async_object",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=True,
    )

    await bus.emit_async(event)
    assert calls == ["async-object", "async", "sync"]


def test_hook_bus_sync_emit_rejects_async_handlers() -> None:
    """Verify sync emit rejects async handlers and points callers to emit_async."""
    bus = MockHookBus()

    async def async_handler(_event) -> None:
        return None

    bus.register(
        HookRegistration(
            hook_name="async_handler",
            handler=async_handler,
            failure_policy=FailurePolicy.RAISE,
        ),
        plugin_name="plugin_async",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=True,
    )

    with pytest.raises(TypeError, match="emit_async"):
        bus.emit(event)


def test_hook_bus_sync_emit_rejects_async_handlers_with_log_policy() -> None:
    """Verify async/sync mismatch TypeError is never swallowed by LOG policy."""
    bus = MockHookBus()

    async def async_handler(_event) -> None:
        return None

    bus.register(
        HookRegistration(
            hook_name="async_handler",
            handler=async_handler,
            failure_policy=FailurePolicy.LOG,
        ),
        plugin_name="plugin_async",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=True,
    )

    with pytest.raises(TypeError, match="emit_async"):
        bus.emit(event)


def test_hook_bus_filter_with_no_tags_passes_all_events() -> None:
    """Verify events are not rejected when filters.tags is None (issue #344)."""
    bus = MockHookBus()
    calls: list[str] = []

    def handler(_event) -> None:
        calls.append("called")

    bus.register(
        HookRegistration(
            hook_name="handler",
            handler=handler,
            priority=10,
            filters=HookFilter(event_types={"quality.result"}),
        ),
        plugin_name="plugin",
    )

    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="asset",
        check_name="null_check",
        passed=True,
        tags=None,
    )
    bus.emit(event)
    assert calls == ["called"]
