"""Registers the Loki service plugin.

Built declaratively via service_plugin_class(): the module declares plugin
metadata only, with no behaviour of its own.
Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; declares service metadata against phlo.plugins.
"""

from __future__ import annotations

from phlo.plugins import service_plugin_class


LokiServicePlugin = service_plugin_class(
    "LokiServicePlugin",
    name="loki",
    version="0.1.0",
    description="Log aggregation and querying",
    author="Phlo Team",
    tags=["observability", "logs"],
)


LokiVolumeSetupServicePlugin = service_plugin_class(
    "LokiVolumeSetupServicePlugin",
    name="loki-volume-setup",
    version="0.1.0",
    description="Initialize Loki data volume permissions",
    author="Phlo Team",
    tags=["observability"],
    service_definition_file="volume_setup.yaml",
)
