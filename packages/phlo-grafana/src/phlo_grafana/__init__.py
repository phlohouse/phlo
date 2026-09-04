"""Grafana observability package for Phlo: metrics visualization, dashboard
management, and service integration as the primary visualization layer for
Prometheus-collected metrics.

Example:
    The package is auto-discovered via the phlo-grafana entry point:

    >>> from phlo.plugins import discover_plugins
    >>> plugins = discover_plugins()
    >>> grafana_plugin = plugins.get("grafana")

"""

from importlib.metadata import version

__version__ = version("phlo-grafana")
