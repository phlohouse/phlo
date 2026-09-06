"""Capability provider for the Polaris catalog.

Registers Polaris as a snapshot-promotion catalog capability, plus its client
resource, catalog scanner, security readiness inspector, and backup
contributor. Polaris deliberately advertises ``supports_refs=False``: it is a
snapshot-promotion catalog, not a branch/merge versioned catalog.
"""

from __future__ import annotations

from phlo.capabilities import (
    BackendReadinessSpec,
    BackupContributorSpec,
    CapabilitySupport,
    CatalogScannerSpec,
    CatalogSpec,
    ResourceSpec,
)
from phlo.plugins.base import PluginMetadata, ResourceProviderPlugin

from phlo_polaris.catalog_scanner import PolarisTableScanner
from phlo_polaris.promotion import PolarisSnapshotPromotionCatalog
from phlo_polaris.resource import PolarisResource

POLARIS_COMPATIBILITY_METADATA = {
    "target": "apache-iceberg-1.11",
    "rest_catalog": {"polaris_uri_suffix": "/api/catalog"},
    "checks": ["polaris-iceberg-rest-uri"],
}


class PolarisResourceProvider(ResourceProviderPlugin):
    """Expose Polaris as a capability-native snapshot-promotion provider."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Polaris resource provider."""
        return PluginMetadata(
            name="polaris",
            version="0.1.0",
            description="Apache Polaris catalog with snapshot-based WAP promotion",
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Expose the Polaris management client as a runtime resource."""
        return [ResourceSpec(name="polaris", resource=PolarisResource())]

    def get_catalogs(self) -> list[CatalogSpec]:
        """Expose Polaris as a snapshot-promotion catalog capability."""
        support = CapabilitySupport(
            supports_refs=False,
            supports_snapshots=True,
            supports_promote=True,
        )
        return [
            CatalogSpec(
                name="polaris",
                provider=PolarisSnapshotPromotionCatalog(),
                metadata={"compatibility": POLARIS_COMPATIBILITY_METADATA},
                support=support,
            )
        ]

    def get_catalog_scanners(self) -> list[CatalogScannerSpec]:
        """Expose Polaris table scanning as a capability."""
        return [
            CatalogScannerSpec(
                name="polaris",
                provider=PolarisTableScanner.from_config(),
                support=CapabilitySupport(),
            )
        ]

    def get_backend_readiness(self) -> list[BackendReadinessSpec]:
        """Expose the polaris security readiness inspector (read-only)."""
        from phlo_polaris.security_readiness import PolarisReadinessProvider

        return [BackendReadinessSpec(name="polaris", provider=PolarisReadinessProvider())]

    def get_backup_contributors(self) -> list[BackupContributorSpec]:
        """Expose the polaris release-ledger backup contribution capability."""
        from phlo_polaris.continuity import PolarisBackupContributor

        return [BackupContributorSpec(name="polaris", provider=PolarisBackupContributor())]
