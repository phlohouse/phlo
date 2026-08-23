"""Tests for low-level plugin entry-point loading helpers.

Stubbed entry points exercise settings-driven filtering (enabled, whitelist,
blacklist), load failures, and registration against the global registry.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from phlo.plugins import PluginMetadata, SourceConnectorPlugin
from phlo.plugins.discovery import _plugin_loading
from phlo.plugins.discovery.registry import get_global_registry

pytestmark = pytest.mark.core_regression


class _SettingsStub:
    plugins_enabled = True
    plugins_whitelist: list[str] = []
    plugins_blacklist: list[str] = []


class _EntryPointStub:
    def __init__(self, name: str, value: str, target: object) -> None:
        self.name = name
        self.value = value
        self._target = target

    def load(self) -> object:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class _SourcePlugin(SourceConnectorPlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="source_plugin", version="1.0.0")

    def fetch_data(self, config: dict[str, object]) -> Iterator[dict[str, int]]:
        yield {"id": 1}


@pytest.fixture
def settings_stub(monkeypatch: pytest.MonkeyPatch) -> _SettingsStub:
    settings = _SettingsStub()
    monkeypatch.setattr(_plugin_loading, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def clean_registry():
    registry = get_global_registry()
    registry.clear()
    yield registry
    registry.clear()


def test_is_plugin_allowed_honors_blacklist_and_whitelist(
    settings_stub: _SettingsStub,
) -> None:
    """Allow-list and deny-list settings are enforced before plugin load."""
    settings_stub.plugins_blacklist = ["blocked"]
    settings_stub.plugins_whitelist = ["allowed"]

    assert _plugin_loading.is_plugin_allowed("blocked") is False
    assert _plugin_loading.is_plugin_allowed("missing") is False
    assert _plugin_loading.is_plugin_allowed("allowed") is True


def test_discover_plugins_uses_failure_level_for_entry_point_errors(
    clean_registry,
    settings_stub: _SettingsStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry-point load failures use the requested logger level and continue discovery."""
    warnings: list[dict[str, object]] = []

    monkeypatch.setattr(
        _plugin_loading,
        "entry_points_for_group",
        lambda group: [
            _EntryPointStub("broken", "tests:broken", RuntimeError("boom")),
            _EntryPointStub("source", "tests:source", _SourcePlugin()),
        ],
    )

    def _capture_warning(event: str, **fields: object) -> None:
        warnings.append({"event": event, "fields": fields})

    monkeypatch.setattr(_plugin_loading.logger, "warning", _capture_warning)

    discovered = _plugin_loading.discover_plugins(
        plugin_type="source_connector",
        auto_register=False,
        failure_level="warning",
    )

    assert [plugin.metadata.name for plugin in discovered["source_connector"]] == ["source_plugin"]
    assert warnings == [
        {
            "event": "plugin_load_failed",
            "fields": {
                "plugin_name": "broken",
                "entry_point": "tests:broken",
                "plugin_type": "source_connector",
                "exc_info": True,
            },
        }
    ]


def test_discover_plugins_can_collect_failure_details(
    clean_registry,
    settings_stub: _SettingsStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor can collect entry-point load failures without parsing logs."""
    failures: list[dict[str, str]] = []

    monkeypatch.setattr(
        _plugin_loading,
        "entry_points_for_group",
        lambda group: [_EntryPointStub("broken", "tests:broken", RuntimeError("boom"))],
    )

    discovered = _plugin_loading.discover_plugins(
        plugin_type="source_connector",
        auto_register=False,
        failure_level="debug",
        failure_sink=failures,
    )

    assert discovered["source_connector"] == []
    assert failures == [
        {
            "plugin_name": "broken",
            "entry_point": "tests:broken",
            "plugin_type": "source_connector",
            "error": "boom",
            "error_type": "RuntimeError",
        }
    ]


def test_discover_plugins_strict_raises_for_entry_point_errors(
    clean_registry,
    settings_stub: _SettingsStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict discovery fails fast when an entry point cannot load."""
    monkeypatch.setattr(
        _plugin_loading,
        "entry_points_for_group",
        lambda group: [_EntryPointStub("broken", "tests:broken", RuntimeError("boom"))],
    )

    with pytest.raises(_plugin_loading.PluginDiscoveryError) as exc_info:
        _plugin_loading.discover_plugins(
            plugin_type="source_connector",
            auto_register=False,
            strict=True,
        )

    assert exc_info.value.plugin_name == "broken"
    assert exc_info.value.entry_point == "tests:broken"
    assert exc_info.value.plugin_type == "source_connector"


def test_discover_plugins_strict_raises_for_invalid_plugin_base(
    clean_registry,
    settings_stub: _SettingsStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict discovery also fails fast for invalid loaded plugin objects."""
    monkeypatch.setattr(
        _plugin_loading,
        "entry_points_for_group",
        lambda group: [_EntryPointStub("plain", "tests:plain", object())],
    )

    with pytest.raises(_plugin_loading.PluginDiscoveryError) as exc_info:
        _plugin_loading.discover_plugins(
            plugin_type="source_connector",
            auto_register=False,
            strict=True,
        )

    assert exc_info.value.plugin_name == "plain"
    assert exc_info.value.reason == "invalid_base_class"


def test_discover_plugins_skips_disallowed_and_invalid_plugins(
    clean_registry,
    settings_stub: _SettingsStub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disallowed, wrong-base, and wrong-type entry points do not register."""
    errors: list[dict[str, object]] = []
    settings_stub.plugins_blacklist = ["blocked"]

    monkeypatch.setattr(
        _plugin_loading,
        "entry_points_for_group",
        lambda group: [
            _EntryPointStub("blocked", "tests:blocked", _SourcePlugin()),
            _EntryPointStub("plain", "tests:plain", object()),
            _EntryPointStub("wrong-type", "tests:wrong", _SourcePlugin()),
        ],
    )
    monkeypatch.setattr(
        _plugin_loading.logger,
        "error",
        lambda event, **fields: errors.append({"event": event, "fields": fields}),
    )

    discovered = _plugin_loading.discover_plugins(
        plugin_type="quality_check",
        auto_register=True,
    )

    assert discovered["quality_check"] == []
    assert clean_registry.list("quality_check") == []
    assert [error["event"] for error in errors] == [
        "plugin_invalid_base_class",
        "plugin_incorrect_type",
    ]
    assert errors[1]["fields"]["expected_type"] == "QualityCheckPlugin"
    assert errors[1]["fields"]["actual_type"] == "_SourcePlugin"
