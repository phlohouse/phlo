"""Phlo Prometheus monitoring package.

Provides Prometheus service integration for the Phlo data platform, enabling
metrics collection and monitoring. The package is loaded automatically by the
Phlo plugin system::

    from phlo.plugins import load_plugin
    plugin = load_plugin("prometheus")
    definition = plugin.service_definition

"""

from importlib.metadata import version

__version__ = version("phlo-prometheus")
