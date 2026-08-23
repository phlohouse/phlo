"""Tests for ServiceManifest: definition/source-path pairing, resolver
error handling, and service discovery from plugin manifests."""

from pathlib import Path

import pytest

from phlo.plugins.discovery._service_definition import ServiceDefinition
from phlo.plugins.discovery.registry import get_global_registry
from phlo.plugins.discovery.service_manifest import (
    ServiceManifest,
    ServiceManifestError,
    ServiceManifestResolver,
)
from phlo.plugins.discovery.services import ServiceDiscovery


def test_service_manifest_wraps_definition_and_source_path(tmp_path: Path) -> None:
    definition = ServiceDefinition.from_dict(
        {
            "name": "postgres",
            "image": "postgres:16",
            "default": True,
            "depends_on": [],
        },
        tmp_path / "service.yaml",
    )

    manifest = ServiceManifest(definition=definition, source_path=tmp_path / "service.yaml")

    assert manifest.name == "postgres"
    assert manifest.definition is definition
    assert manifest.source_path == tmp_path / "service.yaml"


def test_service_manifest_error_includes_context() -> None:
    error = ServiceManifestError(
        "invalid service definition",
        service_name="postgres",
        source_path=Path("services/postgres/service.yaml"),
    )

    assert (
        str(error)
        == "invalid service definition: service=postgres source=services/postgres/service.yaml"
    )


def test_resolver_loads_service_yaml_files_from_directory(tmp_path: Path) -> None:
    service_dir = tmp_path / "services"
    service_dir.mkdir()
    (service_dir / "postgres.service.schema.yaml").write_text("name: ignored\n", encoding="utf-8")
    (service_dir / "postgres.yaml").write_text("name: ignored\nimage: busybox\n", encoding="utf-8")
    (service_dir / "service.yaml").write_text(
        "name: postgres\ndescription: Postgres\nimage: postgres:16\ndefault: true\n",
        encoding="utf-8",
    )

    resolver = ServiceManifestResolver(services_dir=service_dir)

    manifests = resolver.resolve_directory_manifests()

    assert [manifest.name for manifest in manifests] == ["postgres"]
    assert manifests[0].definition.image == "postgres:16"


def test_resolver_raises_contextual_error_for_bad_yaml(tmp_path: Path) -> None:
    service_dir = tmp_path / "services"
    service_dir.mkdir()
    bad_yaml = service_dir / "service.yaml"
    bad_yaml.write_text("name: [", encoding="utf-8")

    resolver = ServiceManifestResolver(services_dir=service_dir)

    with pytest.raises(ServiceManifestError) as exc:
        resolver.resolve_directory_manifests()

    assert "invalid service definition file" in str(exc.value)
    assert f"source={bad_yaml}" in str(exc.value)


def test_resolver_loads_plugin_manifest_and_companion_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "phlo_fake"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "worker-setup.yaml").write_text(
        "name: worker-setup\ndescription: Worker setup\nimage: busybox\ndefault: false\n",
        encoding="utf-8",
    )

    class FakePlugin:
        service_definition = {
            "name": "worker",
            "description": "Worker",
            "image": "busybox",
            "default": True,
        }

    plugin = FakePlugin()

    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.get_registered_service_plugins",
        lambda: {"worker": plugin},
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.resolve_plugin_source_path",
        lambda _plugin: package_dir,
    )

    resolver = ServiceManifestResolver()

    manifests = resolver.resolve_plugin_manifests()

    assert [manifest.name for manifest in manifests] == ["worker", "worker-setup"]


def test_resolver_skips_companion_duplicate_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "phlo_fake"
    package_dir.mkdir()
    (package_dir / "service-setup.yaml").write_text(
        "name: worker\ndescription: Duplicate worker\nimage: duplicate\n",
        encoding="utf-8",
    )

    class FakePlugin:
        service_definition = {
            "name": "worker",
            "description": "Worker",
            "image": "busybox",
        }

    plugin = FakePlugin()
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.get_registered_service_plugins",
        lambda: {"worker": plugin},
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.resolve_plugin_source_path",
        lambda _plugin: package_dir,
    )

    manifests = ServiceManifestResolver().resolve_plugin_manifests()

    assert [manifest.name for manifest in manifests] == ["worker"]


def test_service_discovery_uses_manifest_resolver_for_directory_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    get_global_registry().clear()
    monkeypatch.setattr(
        "phlo.plugins.discovery._service_loading.discover_plugins",
        lambda plugin_type, auto_register: None,
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    service_dir = tmp_path / "services"
    service_dir.mkdir()
    (service_dir / "service.yaml").write_text(
        "name: postgres\ndescription: Postgres\nimage: postgres:16\n",
        encoding="utf-8",
    )

    discovery = ServiceDiscovery(services_dir=service_dir)

    services = discovery.discover(refresh=True)

    assert list(services) == ["postgres"]
    assert services["postgres"].image == "postgres:16"


def test_resolver_expands_requested_services_with_dependencies(tmp_path: Path) -> None:
    definitions = [
        ServiceDefinition.from_dict({"name": "postgres", "image": "postgres:16"}, tmp_path),
        ServiceDefinition.from_dict(
            {"name": "api", "image": "api:latest", "depends_on": ["postgres"]},
            tmp_path,
        ),
    ]

    expanded = ServiceManifestResolver.expand_dependencies(definitions, ["api"])

    assert [service.name for service in expanded] == ["postgres", "api"]
