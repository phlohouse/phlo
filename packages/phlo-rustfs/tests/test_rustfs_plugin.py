"""Tests for RustFS service plugin.

This module contains comprehensive tests for the RustFS service and resource
provider plugins. Tests validate service definitions, plugin metadata, and
object store capability exposure.

Functions:
    test_rustfs_service_definition: Validates RustFS service definition fields.
    test_rustfs_plugin_metadata: Validates RustFS plugin metadata.
    test_rustfs_setup_service_definition: Validates setup service definition.
    test_rustfs_setup_plugin_metadata: Validates setup plugin metadata.
    test_rustfs_resource_provider_exposes_object_store: Tests object store capability.
"""

from phlo_rustfs.plugin import RustfsResourceProvider, RustfsServicePlugin, RustfsSetupServicePlugin


def test_rustfs_service_definition():
    """Validate RustFS service definition fields.

    Verifies that the RustfsServicePlugin returns a valid service definition
    with the expected name, category, and dependencies. Checks the Docker
    image reference and service dependency declarations.

    Asserts:
        - service_definition["name"] == "rustfs"
        - service_definition["category"] == "core"
        - service_definition["default"] is False
        - "rustfs/rustfs" in service_definition["image"]
        - "rustfs-volume-setup" in service_definition["depends_on"]
    """
    plugin = RustfsServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "rustfs"
    assert service_definition["category"] == "core"
    assert service_definition["default"] is False
    assert "rustfs/rustfs" in service_definition["image"]
    assert "rustfs-volume-setup" in service_definition["depends_on"]
    assert "rustfs-data:/data" in service_definition["compose"]["volumes"]


def test_rustfs_volume_setup_uses_named_volume():
    """RustFS ownership setup should not require host bind-mount chown."""

    import yaml
    from importlib.resources import files

    service_definition = yaml.safe_load(
        files("phlo_rustfs").joinpath("rustfs-volume-setup.yaml").read_text()
    )

    assert "rustfs-data:/data" in service_definition["compose"]["volumes"]


def test_rustfs_plugin_metadata():
    """Validate RustFS plugin metadata.

    Verifies that RustfsServicePlugin returns proper PluginMetadata with
    expected name, version, and tags for discovery and categorization.

    Asserts:
        - metadata.name == "rustfs"
        - metadata.version == "0.1.0"
        - "storage" in metadata.tags
        - "s3" in metadata.tags
    """
    plugin = RustfsServicePlugin()
    metadata = plugin.metadata

    assert metadata.name == "rustfs"
    assert metadata.version == "0.1.0"
    assert "storage" in metadata.tags
    assert "s3" in metadata.tags


def test_rustfs_setup_service_definition():
    """Validate RustFS setup service definition fields.

    Verifies that the RustfsSetupServicePlugin returns a valid service
    definition for the bucket initialization container with correct
    name, category, and dependency on the main rustfs service.

    Asserts:
        - service_definition["name"] == "rustfs-setup"
        - service_definition["category"] == "core"
        - "rustfs" in service_definition["depends_on"]
    """
    plugin = RustfsSetupServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "rustfs-setup"
    assert service_definition["category"] == "core"
    assert "rustfs" in service_definition["depends_on"]


def test_rustfs_setup_plugin_metadata():
    """Validate RustFS setup plugin metadata.

    Verifies that RustfsSetupServicePlugin returns proper PluginMetadata
    identifying it as a bootstrap/initialization service.

    Asserts:
        - metadata.name == "rustfs-setup"
        - "bootstrap" in metadata.tags
    """
    plugin = RustfsSetupServicePlugin()
    metadata = plugin.metadata

    assert metadata.name == "rustfs-setup"
    assert "bootstrap" in metadata.tags


def test_rustfs_resource_provider_exposes_object_store(monkeypatch) -> None:
    """Verify RustFS exposes an object_store capability.

    Tests that RustfsResourceProvider correctly exposes an ObjectStoreSpec
    through get_object_stores(). Mocks the S3 connection details to avoid
    external dependencies during testing.

    Asserts:
        - Object stores list has exactly one entry
        - Object store name is "rustfs"
        - Endpoint metadata matches mocked value
    """
    monkeypatch.setattr(
        "phlo_rustfs.plugin.RustfsObjectStoreProvider.to_sling_connection",
        lambda _self: {
            "type": "s3",
            "endpoint": "http://rustfs:9000",
            "access_key_id": "rustfs",
            "secret_access_key": "secret",
            "region": "us-east-1",
        },
    )

    provider = RustfsResourceProvider()

    object_stores = provider.get_object_stores()

    assert len(object_stores) == 1
    assert object_stores[0].name == "rustfs"
    assert object_stores[0].metadata["endpoint"] == "http://rustfs:9000"
