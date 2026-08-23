"""Regression tests for plugin lifecycle hooks during discovery registration.

Uses entry-point stubs with observable initialize/cleanup events to lock the
replacement ordering and rollback guarantees of lifecycle-aware registration.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from phlo.plugins import (
    PluginMetadata,
    QualityCheckPlugin,
    SourceConnectorPlugin,
    discover_plugins,
)
from phlo.plugins.discovery import get_global_registry

pytestmark = pytest.mark.core_regression


class _SettingsStub:
    """Test settings stub for plugin discovery."""

    plugins_enabled = True
    plugins_whitelist: list[str] = []
    plugins_blacklist: list[str] = []


class _EntryPointStub:
    """Simple entry point stub for discovery tests."""

    def __init__(self, name: str, plugin: SourceConnectorPlugin) -> None:
        self.name = name
        self.value = f"tests:{name}"
        self._plugin = plugin

    def load(self) -> SourceConnectorPlugin:
        """Return plugin instance for entry point loading."""
        return self._plugin


class _LifecycleSourcePlugin(SourceConnectorPlugin):
    """Source plugin with observable initialize/cleanup lifecycle events."""

    def __init__(
        self,
        marker: str,
        events: list[str],
        *,
        name: str = "lifecycle_source",
        fail_initialize: bool = False,
        fail_cleanup: bool = False,
    ) -> None:
        self.marker = marker
        self._events = events
        self._name = name
        self._fail_initialize = fail_initialize
        self._fail_cleanup = fail_cleanup

    @property
    def metadata(self) -> PluginMetadata:
        """Return metadata with a stable plugin name for replacement tests."""
        return PluginMetadata(name=self._name, version=f"1.0.0-{self.marker}")

    def initialize(self, config: dict[str, object]) -> None:
        """Track plugin initialization and optionally fail."""
        self._events.append(f"initialize:{self.marker}")
        if self._fail_initialize:
            raise RuntimeError(f"initialize failed for {self.marker}")

    def cleanup(self) -> None:
        """Track plugin cleanup and optionally fail."""
        self._events.append(f"cleanup:{self.marker}")
        if self._fail_cleanup:
            raise RuntimeError(f"cleanup failed for {self.marker}")

    def fetch_data(self, config: dict[str, object]) -> Iterator[dict[str, int]]:
        """Yield one row to satisfy source plugin interface."""
        yield {"id": 1}


class _MultiTypeLifecyclePlugin(SourceConnectorPlugin, QualityCheckPlugin):
    """Plugin implementing multiple interfaces to test deduplicated cleanup."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    @property
    def metadata(self) -> PluginMetadata:
        """Return metadata shared across plugin interfaces."""
        return PluginMetadata(name="multi_type_plugin", version="1.0.0")

    def cleanup(self) -> None:
        """Track cleanup calls."""
        self._events.append("cleanup:multi")

    def fetch_data(self, config: dict[str, object]) -> Iterator[dict[str, int]]:
        """Yield one row to satisfy source plugin interface."""
        yield {"id": 1}

    def create_check(self, **kwargs):
        """Return no-op check for quality plugin interface."""
        return


@pytest.fixture
def clean_registry() -> Iterator:
    """Reset global plugin registry around each lifecycle regression test."""
    registry = get_global_registry()
    registry.clear()
    yield registry
    registry.clear()


def _patch_source_entry_points(
    monkeypatch: pytest.MonkeyPatch, entry_points: list[_EntryPointStub]
) -> None:
    """Patch settings and entry point discovery for source connector tests."""

    monkeypatch.setattr(
        "phlo.plugins.discovery._plugin_loading.get_settings", lambda: _SettingsStub()
    )

    def _fake_entry_points(*_args, **kwargs):
        group = kwargs.get("group")
        if group == "phlo.plugins.sources":
            return entry_points
        return []

    monkeypatch.setattr(
        "phlo.plugins.discovery._entry_points.importlib.metadata.entry_points",
        _fake_entry_points,
    )


def _capture_plugin_load_errors(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture plugin load errors to assert failure paths are not silent."""
    errors: list[dict[str, object]] = []

    def _capture(event: str, **kwargs: object) -> None:
        if event == "plugin_load_failed":
            errors.append(kwargs)

    monkeypatch.setattr("phlo.plugins.discovery._plugin_loading.logger.error", _capture)
    return errors


def _assert_plugin_load_failed_error(errors: list[dict[str, object]]) -> None:
    """Assert one plugin_load_failed error was emitted for lifecycle regressions."""
    assert len(errors) == 1
    assert errors[0] == {
        "plugin_name": "lifecycle_source",
        "entry_point": "tests:lifecycle_source",
        "plugin_type": "source_connector",
        "exc_info": True,
    }


@pytest.fixture
def lifecycle_signals(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture structured lifecycle observability signals emitted via log_event."""
    signals: list[dict[str, object]] = []

    def _capture_signal(
        _logger: object,
        level: str,
        event: str,
        **fields: object,
    ) -> None:
        signals.append({"level": level, "event": event, "fields": fields})

    monkeypatch.setattr("phlo.plugins.discovery._plugin_lifecycle.log_event", _capture_signal)
    return signals


def test_discover_plugins_emits_lifecycle_success_signals(
    clean_registry,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_signals: list[dict[str, object]],
) -> None:
    """Lifecycle success signals include initialize and replacement cleanup events."""
    events: list[str] = []
    existing = _LifecycleSourcePlugin(marker="old", events=events)
    clean_registry.register("source_connector", existing)

    incoming = _LifecycleSourcePlugin(marker="new", events=events)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=incoming)]
    )

    discover_plugins(plugin_type="source_connector", auto_register=True)

    signal_fields = [signal["fields"] for signal in lifecycle_signals]
    assert any(
        fields.get("lifecycle_phase") == "incoming_plugin_initialize"
        and fields.get("plugin_type") == "source_connector"
        for fields in signal_fields
        if fields and isinstance(fields, dict)
    )
    assert any(
        fields.get("lifecycle_phase") == "existing_plugin_cleanup"
        and fields.get("reason") == "replacement"
        and fields.get("target_plugin_name") == "lifecycle_source"
        for fields in signal_fields
        if fields and isinstance(fields, dict)
    )
    assert any(
        signal["event"] == "plugin_lifecycle_initialize_succeeded" for signal in lifecycle_signals
    )
    assert any(
        signal["event"] == "plugin_lifecycle_cleanup_succeeded" for signal in lifecycle_signals
    )


def test_discover_plugins_emits_lifecycle_failure_signals(
    clean_registry,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_signals: list[dict[str, object]],
) -> None:
    """Lifecycle failure signals include initialize and cleanup errors."""
    events: list[str] = []
    failing = _LifecycleSourcePlugin(
        marker="new",
        events=events,
        fail_initialize=True,
        fail_cleanup=True,
    )
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=failing)]
    )

    discover_plugins(plugin_type="source_connector", auto_register=True)

    initialize_failures = [
        signal
        for signal in lifecycle_signals
        if signal["event"] == "plugin_lifecycle_initialize_failed"
        and isinstance(signal["fields"], dict)
        and signal["fields"].get("lifecycle_phase") == "incoming_plugin_initialize"
    ]
    cleanup_failures = [
        signal
        for signal in lifecycle_signals
        if signal["event"] == "plugin_lifecycle_cleanup_failed"
        and isinstance(signal["fields"], dict)
        and signal["fields"].get("lifecycle_phase") == "incoming_plugin_cleanup"
    ]

    assert len(initialize_failures) == 1
    assert initialize_failures[0]["fields"]["plugin_type"] == "source_connector"
    assert initialize_failures[0]["fields"]["error_type"] == "RuntimeError"
    assert len(cleanup_failures) == 1
    assert cleanup_failures[0]["fields"]["reason"] == "initialize_failed"
    assert cleanup_failures[0]["fields"]["error_type"] == "RuntimeError"


def test_discover_plugins_calls_initialize_before_registration(
    clean_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Initialize is called when discovered plugin registration succeeds."""
    events: list[str] = []
    plugin = _LifecycleSourcePlugin(marker="new", events=events)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=plugin)]
    )

    discover_plugins(plugin_type="source_connector", auto_register=True)

    assert clean_registry.get("source_connector", "lifecycle_source") is plugin
    assert events == ["initialize:new"]


def test_discover_plugins_cleans_existing_plugin_before_replacement_registration(
    clean_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement flow cleans existing plugin before new registration."""
    events: list[str] = []
    existing = _LifecycleSourcePlugin(marker="old", events=events)
    clean_registry.register("source_connector", existing)

    incoming = _LifecycleSourcePlugin(marker="new", events=events)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=incoming)]
    )

    register_plugin = clean_registry.register

    def _track_register(family: str, plugin: SourceConnectorPlugin, replace: bool = False) -> None:
        events.append(f"register:{plugin.marker}")
        register_plugin(family, plugin, replace=replace)

    monkeypatch.setattr(clean_registry, "register", _track_register)

    discover_plugins(plugin_type="source_connector", auto_register=True)

    assert clean_registry.get("source_connector", "lifecycle_source") is incoming
    assert events == ["initialize:new", "cleanup:old", "register:new"]


def test_discover_plugins_keeps_existing_plugin_when_cleanup_fails(
    clean_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup failure during replacement preserves prior registry plugin."""
    events: list[str] = []
    existing = _LifecycleSourcePlugin(marker="old", events=events, fail_cleanup=True)
    clean_registry.register("source_connector", existing)

    incoming = _LifecycleSourcePlugin(marker="new", events=events)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=incoming)]
    )
    plugin_load_errors = _capture_plugin_load_errors(monkeypatch)

    discovered = discover_plugins(plugin_type="source_connector", auto_register=True)

    assert clean_registry.get("source_connector", "lifecycle_source") is existing
    assert discovered["source_connector"] == []
    assert events == ["initialize:new", "cleanup:old", "cleanup:new"]
    _assert_plugin_load_failed_error(plugin_load_errors)


def test_discover_plugins_keeps_existing_plugin_when_initialize_fails(
    clean_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Initialize failure does not touch existing plugin registration."""
    events: list[str] = []
    existing = _LifecycleSourcePlugin(marker="old", events=events)
    clean_registry.register("source_connector", existing)

    incoming = _LifecycleSourcePlugin(marker="new", events=events, fail_initialize=True)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=incoming)]
    )
    plugin_load_errors = _capture_plugin_load_errors(monkeypatch)

    discovered = discover_plugins(plugin_type="source_connector", auto_register=True)

    assert clean_registry.get("source_connector", "lifecycle_source") is existing
    assert discovered["source_connector"] == []
    assert events == ["initialize:new", "cleanup:new"]
    _assert_plugin_load_failed_error(plugin_load_errors)


def test_discover_plugins_recovers_existing_plugin_on_registration_failure(
    clean_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration failure after cleanup re-initializes existing plugin."""
    events: list[str] = []
    existing = _LifecycleSourcePlugin(marker="old", events=events)
    clean_registry.register("source_connector", existing)

    incoming = _LifecycleSourcePlugin(marker="new", events=events)
    _patch_source_entry_points(
        monkeypatch, [_EntryPointStub(name="lifecycle_source", plugin=incoming)]
    )

    register_plugin = clean_registry.register

    def _failing_register(
        family: str, plugin: SourceConnectorPlugin, replace: bool = False
    ) -> None:
        if plugin is incoming:
            raise RuntimeError("simulated registration failure")
        register_plugin(family, plugin, replace=replace)

    monkeypatch.setattr(clean_registry, "register", _failing_register)
    plugin_load_errors = _capture_plugin_load_errors(monkeypatch)

    discovered = discover_plugins(plugin_type="source_connector", auto_register=True)

    assert clean_registry.get("source_connector", "lifecycle_source") is existing
    assert discovered["source_connector"] == []
    assert events == ["initialize:new", "cleanup:old", "initialize:old", "cleanup:new"]
    _assert_plugin_load_failed_error(plugin_load_errors)


def test_registry_clear_calls_cleanup_during_shutdown(clean_registry) -> None:
    """Registry clear triggers plugin cleanup for registered plugins."""
    events: list[str] = []
    plugin = _LifecycleSourcePlugin(marker="shutdown", events=events)
    clean_registry.register("source_connector", plugin)

    clean_registry.clear()

    assert events == ["cleanup:shutdown"]
    assert len(clean_registry) == 0


def test_registry_clear_continues_when_cleanup_fails(clean_registry) -> None:
    """Registry clear continues teardown after cleanup errors."""
    events: list[str] = []
    failing = _LifecycleSourcePlugin(
        marker="failing",
        events=events,
        name="failing_plugin",
        fail_cleanup=True,
    )
    healthy = _LifecycleSourcePlugin(marker="healthy", events=events, name="healthy_plugin")
    clean_registry.register("source_connector", failing)
    clean_registry.register("source_connector", healthy)

    clean_registry.clear()

    assert events == ["cleanup:failing", "cleanup:healthy"]
    assert len(clean_registry) == 0


def test_registry_clear_deduplicates_cleanup_for_shared_plugin_instance(clean_registry) -> None:
    """Shared plugin instances are cleaned only once during clear."""
    events: list[str] = []
    plugin = _MultiTypeLifecyclePlugin(events)
    clean_registry.register("source_connector", plugin)
    clean_registry.register("quality_check", plugin)

    clean_registry.clear()
    clean_registry.clear()

    assert events == ["cleanup:multi"]
