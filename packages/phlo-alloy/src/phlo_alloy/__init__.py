"""Grafana Alloy package for log collection and shipping to Loki.

Integrates Grafana Alloy, a vendor-neutral OpenTelemetry Collector distribution
with built-in Prometheus pipelines, providing the AlloyServicePlugin for
service lifecycle management within the Phlo platform. Auto-discovered via the
``phlo.services`` entry point; not intended for direct import. Requires the
``phlo`` core for plugin registration. ``__version__`` carries the package
version string. See https://grafana.com/docs/alloy/latest/ for Alloy docs.
"""

__version__ = "0.1.0"
