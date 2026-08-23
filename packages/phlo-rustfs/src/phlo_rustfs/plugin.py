"""RustFS service plugin.

This module implements Phlo plugins for integrating RustFS into the service mesh.
It provides service definitions for running RustFS containers and initializing
buckets, plus resource providers that expose S3-compatible object storage
capabilities to other components.

Classes:
    RustfsServicePlugin: Main service plugin for the RustFS container.
    RustfsSetupServicePlugin: Service plugin for bucket initialization.
    RustfsObjectStoreProvider: Capability provider for S3 storage.
    RustfsResourceProvider: Resource provider exposing object store specs.

Functions:
    _load_service_definition: Loads YAML service definitions from package resources.

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; exposes S3 object-store capabilities via phlo.capabilities.
"""

from __future__ import annotations

from typing import Any

from phlo.capabilities import ObjectStoreSpec, ResourceSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin, service_plugin_class


RustfsServicePlugin = service_plugin_class(
    "RustfsServicePlugin",
    name="rustfs",
    version="0.1.0",
    description="S3-compatible object storage for data lake (RustFS)",
    author="Phlo Team",
    tags=["core", "storage", "s3"],
)


RustfsSetupServicePlugin = service_plugin_class(
    "RustfsSetupServicePlugin",
    name="rustfs-setup",
    version="0.1.0",
    description="Initialize RustFS buckets for data lake",
    author="Phlo Team",
    tags=["core", "storage", "bootstrap"],
    service_definition_file="rustfs-setup.yaml",
)


class RustfsObjectStoreProvider:
    """Capability provider for RustFS-backed object storage.

    Provides S3-compatible connection details for RustFS storage. This class
    implements the object store capability interface, allowing other components
    to obtain S3 connection parameters for integrating with RustFS.

    The provider reads configuration from RustfsSettings and formats it
    into S3-compatible dictionaries suitable for Sling and other S3 clients.
    """

    def to_sling_connection(self) -> dict[str, Any]:
        """Return a Sling-compatible S3 connection definition.
        Constructs an S3 connection dictionary formatted for use with Sling.
        Includes endpoint URL, credentials, and region information read from
        the cached RustfsSettings.

        Example:
            >>> provider = RustfsObjectStoreProvider()
            >>> conn = provider.to_sling_connection()
            >>> print(conn["type"])
            "s3"
        """
        from phlo_rustfs.settings import get_settings

        settings = get_settings()
        return {
            "type": "s3",
            "endpoint": f"http://{settings.rustfs_endpoint()}",
            "access_key_id": settings.rustfs_access_key,
            "secret_access_key": settings.rustfs_secret_key,
            "region": settings.s3_region,
        }


class RustfsResourceProvider(ResourceProviderPlugin):
    """Resource provider plugin for RustFS capabilities.

    Implements the ResourceProviderPlugin interface to expose RustFS object
    storage capabilities to the Phlo resource registry. This plugin allows
    other components to discover and connect to RustFS S3 storage.

    The provider exposes a single object store capability named "rustfs"
    that can be used for S3-compatible storage operations.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the RustFS resource provider."""
        return PluginMetadata(
            name="rustfs",
            version="0.1.0",
            description="RustFS object-store capability for Phlo",
            tags=["core", "storage", "s3"],
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Return resource specs exposed by this provider.
        Currently returns an empty list as RustFS does not expose any
        generic resources. Object store capabilities are exposed via
        get_object_stores instead.
        """
        return []

    def get_object_stores(self) -> list[ObjectStoreSpec]:
        """Return object-store capability specs exposed by this provider.
        Returns a list containing a single ObjectStoreSpec for the RustFS
        S3-compatible storage. The spec includes metadata about the storage
        type and endpoint URL.

        Example:
            >>> provider = RustfsResourceProvider()
            >>> stores = provider.get_object_stores()
            >>> len(stores)
            1
            >>> stores[0].name
            "rustfs"
        """
        provider = RustfsObjectStoreProvider()
        return [
            ObjectStoreSpec(
                name="rustfs",
                provider=provider,
                metadata={
                    "storage_system": "s3",
                    "type": "s3",
                    "endpoint": provider.to_sling_connection()["endpoint"],
                },
            )
        ]
