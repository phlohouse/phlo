"""Capability provider for Nessie catalog/versioning resources.

This module exposes Nessie as a capability-native provider within the Phlo
plugin system. It registers Nessie resources, catalogs, and catalog scanners
for discovery by other components.

Example:
    >>> from phlo_nessie.resource_provider import NessieResourceProvider
    >>> provider = NessieResourceProvider()
    >>> resources = provider.get_resources()
    >>> catalogs = provider.get_catalogs()

Classes:
    NessieResourceProvider: Expose Nessie as capability provider.


Loaded through the phlo plugin entry-point mechanism at startup rather than imported
directly; exposes Nessie as a capability resource provider.
"""

from __future__ import annotations

from phlo.capabilities import (
    BackendReadinessSpec,
    CapabilitySupport,
    CatalogScannerSpec,
    CatalogSpec,
    ResourceSpec,
)
from phlo.plugins.base import PluginMetadata, ResourceProviderPlugin

from phlo_nessie.catalog_scanner import NessieTableScanner
from phlo_nessie.resource import NessieResource

NESSIE_COMPATIBILITY_METADATA = {
    "target": "apache-iceberg-1.11",
    "rest_catalog": {"nessie_uri_suffix": "/iceberg"},
    "checks": ["nessie-iceberg-rest-uri"],
}


class NessieResourceProvider(ResourceProviderPlugin):
    def get_backend_readiness(self) -> list[BackendReadinessSpec]:
        """Expose the nessie security readiness inspector (read-only)."""
        from phlo_nessie.security_readiness import NessieReadinessProvider

        return [BackendReadinessSpec(name="nessie", provider=NessieReadinessProvider())]

    """Expose Nessie as a capability-native catalog/versioning provider.
    This plugin registers Nessie with the Phlo capability system, exposing
    it as a catalog, catalog scanner, and versioning resource for other
    components to discover and use.

    Example:
        >>> provider = NessieResourceProvider()
        >>> catalogs = provider.get_catalogs()
        >>> scanners = provider.get_catalog_scanners()
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Nessie resource provider."""
        return PluginMetadata(
            name="nessie",
            version="0.1.0",
            description="Versioned catalog provider with branch and merge support",
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Expose the raw Nessie client as a runtime resource."""
        return [ResourceSpec(name="catalog_versioning", resource=NessieResource())]

    def get_catalogs(self) -> list[CatalogSpec]:
        """Expose Nessie as a catalog capability."""
        support = CapabilitySupport(
            supports_refs=True,
            supports_snapshots=False,
            supports_promote=True,
        )
        return [
            CatalogSpec(
                name="nessie",
                provider=NessieResource(),
                metadata={"compatibility": NESSIE_COMPATIBILITY_METADATA},
                support=support,
            )
        ]

    def get_catalog_scanners(self) -> list[CatalogScannerSpec]:
        """Expose Nessie table scanning as a capability."""
        return [
            CatalogScannerSpec(
                name="nessie",
                provider=NessieTableScanner.from_config(),
                support=CapabilitySupport(),
            )
        ]
