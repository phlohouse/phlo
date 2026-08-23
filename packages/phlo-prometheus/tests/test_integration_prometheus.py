"""Integration tests for phlo-prometheus.

Smoke-level: the plugin instantiates and its packaged service definition
loads as a dict. Runs under the integration marker only.
"""

import pytest

pytestmark = pytest.mark.integration


def test_prometheus_plugin_initializes():
    """Test that Prometheus plugin can be instantiated."""
    from phlo_prometheus.plugin import PrometheusServicePlugin

    plugin = PrometheusServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "prometheus"


def test_prometheus_service_definition():
    """Test that service definition can be loaded."""
    from phlo_prometheus.plugin import PrometheusServicePlugin

    plugin = PrometheusServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
