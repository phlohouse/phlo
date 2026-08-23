"""Tests for the Loki service plugin.

Pins the service definition to the observability profile with a named
volume, an upstream image pinned by digest, and no in-container health
check for the distroless image.
"""

from phlo_loki.plugin import LokiServicePlugin


def test_loki_service_definition():
    """Validate Loki service definition fields."""

    plugin = LokiServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "loki"
    assert defn["profile"] == "observability"
    assert "loki-data:/loki" in defn["compose"]["volumes"]
    assert "./volumes/loki:/loki" not in defn["compose"]["volumes"]


def test_loki_service_uses_pinned_upstream_image() -> None:
    definition = LokiServicePlugin().service_definition

    assert definition["image"] == (
        "grafana/loki:3.7.4@sha256:87f0a067673756a3cede1bcbf0c74875f7df9b09fddb53e399d0c576f756cfcc"
    )
    assert "build" not in definition
    assert "LOKI_VERSION" not in definition["env_vars"]


def test_loki_distroless_image_does_not_claim_an_in_container_probe() -> None:
    definition = LokiServicePlugin().service_definition

    assert "healthcheck" not in definition["compose"]
    assert all(file["dest"] != "loki/loki_healthcheck.go" for file in definition["files"])


def test_loki_plugin_metadata():
    """Validate Loki plugin metadata tags and name."""

    plugin = LokiServicePlugin()
    meta = plugin.metadata

    assert meta.name == "loki"
    assert "observability" in meta.tags
