"""Tests for plugin metadata serialization, including capability
support flags in the dict payload."""

from __future__ import annotations

from phlo.capabilities import CapabilitySupport
from phlo.plugins.base import Plugin, PluginMetadata
from phlo.plugins.discovery._registry_metadata import plugin_metadata_to_dict


class _DummyPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="dummy",
            version="1.0.0",
            support=CapabilitySupport(
                supports_refs=True,
                supports_atomic_validation=True,
            ),
        )


def test_plugin_metadata_to_dict_includes_support() -> None:
    payload = plugin_metadata_to_dict(_DummyPlugin())

    assert payload["support"] == {
        "supports_refs": True,
        "supports_snapshots": False,
        "supports_schema_evolution": False,
        "supports_atomic_validation": True,
        "supports_promote": False,
        "supports_time_travel": False,
        "supports_metrics": False,
        "supports_logs": False,
        "supports_dashboards": False,
        "supports_alerts": False,
        "supports_permissions": False,
        "supports_attributes": False,
    }
