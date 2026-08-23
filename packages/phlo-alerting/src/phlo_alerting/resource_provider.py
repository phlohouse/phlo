"""Resource provider plugin for phlo-alerting capabilities.

This module implements the ResourceProviderPlugin interface to expose
phlo-alerting as a neutral alert sink capability within the Phlo plugin
system. It enables other plugins to discover and use alerting functionality
through standardized capability contracts.

Examples:
    The plugin is automatically discovered by Phlo's plugin system:
        >>> from phlo.plugins import discover_plugins
        >>> plugins = discover_plugins()
        >>> # AlertingResourceProvider is registered automatically

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; registers its alert sink through phlo.capabilities.
"""

from __future__ import annotations

from phlo.capabilities import AlertSinkSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin

from phlo_alerting.alert_sink import AlertManagerSink


class AlertingResourceProvider(ResourceProviderPlugin):
    """Expose phlo-alerting as a neutral alert sink capability.

    Registers phlo-alerting with the Phlo capability system so other
    components can send alerts through the AlertSinkSpec contract.

    Examples:
        >>> provider = AlertingResourceProvider()
        >>> provider.metadata.name
        'alerting'
        >>> sinks = provider.get_alert_sinks()
        >>> len(sinks)
        1

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return the plugin identity used for capability discovery.

        Examples:
            >>> provider = AlertingResourceProvider()
            >>> meta = provider.metadata
            >>> meta.name
            'alerting'
            >>> meta.version
            '0.1.0'

        """
        return PluginMetadata(
            name="alerting",
            version="0.1.0",
            description="Alert sink capability provider",
            tags=["alerting"],
        )

    def get_resources(self) -> list:
        """Return no raw resources; alerting is exposed via get_alert_sinks().

        Examples:
            >>> provider = AlertingResourceProvider()
            >>> provider.get_resources()
            []

        """
        return []

    def get_alert_sinks(self) -> list[AlertSinkSpec]:
        """Expose phlo-alerting as a single AlertSinkSpec other components can send alerts through.

        Examples:
            >>> provider = AlertingResourceProvider()
            >>> sinks = provider.get_alert_sinks()
            >>> len(sinks)
            1
            >>> sinks[0].name
            'alerting'

        """
        return [
            AlertSinkSpec(
                name="alerting",
                provider=AlertManagerSink(),
            )
        ]
