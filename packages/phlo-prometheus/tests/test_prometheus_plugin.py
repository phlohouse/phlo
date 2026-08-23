"""Tests for the Prometheus service plugin.

Locks the shipped service definition: pinned upstream image digest, named
volume storage instead of a bind mount, and no local Dockerfile build.
"""

from phlo_prometheus.plugin import PrometheusServicePlugin


def test_prometheus_service_definition():
    """Validate Prometheus service definition defaults."""
    plugin = PrometheusServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "prometheus"
    assert defn["profile"] == "observability"
    assert "prometheus-data:/prometheus" in defn["compose"]["volumes"]
    assert "./volumes/prometheus:/prometheus" not in defn["compose"]["volumes"]
    assert defn["image"] == (
        "${PROMETHEUS_IMAGE:-prom/prometheus:v3.13.1@"
        "sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893}"
    )
    assert "build" not in defn
    assert all(file_spec["source"] != "Dockerfile" for file_spec in defn["files"])


def test_prometheus_plugin_metadata():
    """Validate Prometheus plugin metadata."""
    plugin = PrometheusServicePlugin()
    meta = plugin.metadata

    assert meta.name == "prometheus"
    assert "observability" in meta.tags
