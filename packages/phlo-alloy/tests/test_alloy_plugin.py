"""Tests for the Alloy observability service plugin.

Locks the shipped service definition: immutable upstream image digest, a
writable storage path for the non-root runtime, and a plain startup
dependency on Loki with no custom health probe.
"""

from phlo_alloy.plugin import AlloyServicePlugin


def test_alloy_service_definition():
    """Validate Alloy service definition defaults."""
    plugin = AlloyServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "alloy"
    assert defn["profile"] == "observability"


def test_alloy_service_uses_pinned_upstream_image() -> None:
    definition = AlloyServicePlugin().service_definition

    assert definition["image"] == (
        "grafana/alloy:v1.18.0@"
        "sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308"
    )
    assert "build" not in definition
    assert all(file["dest"] != "alloy/Dockerfile" for file in definition["files"])


def test_alloy_service_uses_writable_storage_for_the_non_root_runtime() -> None:
    """Alloy must not use its default relative state directory under an unwritable cwd."""
    definition = AlloyServicePlugin().service_definition

    assert "--storage.path=/tmp/alloy" in definition["compose"]["command"]


def test_alloy_starts_after_distroless_loki_without_a_custom_probe() -> None:
    definition = AlloyServicePlugin().service_definition

    assert definition["compose"]["depends_on"]["loki"] == {"condition": "service_started"}


def test_alloy_plugin_metadata():
    """Validate Alloy plugin metadata."""
    plugin = AlloyServicePlugin()
    meta = plugin.metadata

    assert meta.name == "alloy"
    assert "observability" in meta.tags
