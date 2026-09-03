"""Phlo plugin for Iceberg resource provider capabilities.

This module registers the Iceberg plugin with Phlo's plugin system,
exposing ``IcebergResource`` as a table store and ``IcebergSchemaMigrator``
as a schema migration provider.

The plugin advertises full Iceberg capability support including:
- Branch/tag references (via Nessie)
- Snapshot-based time travel
- Native schema evolution
- Snapshot management

Example:
    Plugin is auto-discovered by Phlo's plugin system::

        # In pyproject.toml or entry_points:
        [project.entry-points."phlo.resource_providers"]
        iceberg = "phlo_iceberg.plugin:IcebergResourceProvider"

        # The plugin automatically registers IcebergResource
        # and IcebergSchemaMigrator for use in Dagster assets.

    Access via Phlo capability system::

        from phlo.capabilities import SlingConnectionSpec, get_resource

        # Get Iceberg resource
        iceberg = get_resource("table_store", name="iceberg")
        result = iceberg.append_parquet("raw.events", "/data/events.parquet")

        # Get schema migrator
        migrator = get_resource("schema_migrator", name="iceberg")
        plan = migrator.diff_schema(table_name="raw.users", desired=schema)


Loaded through the phlo plugin entry-point mechanism at startup rather than imported
directly; registers IcebergResourceProvider through phlo.capabilities and phlo.plugins.
"""

from phlo.capabilities import (
    EvidenceProfileContributionSpec,
    CapabilitySupport,
    ResourceSpec,
    SchemaMigrationSpec,
    SlingConnectionSpec,
    TableStoreSpec,
)
from phlo.capabilities import BackupContributorSpec
from phlo.plugins.base import PluginMetadata, ResourceProviderPlugin

from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.schema_migrator import IcebergSchemaMigrator

ICEBERG_COMPATIBILITY_METADATA = {
    "target": "apache-iceberg-1.11",
    "rest_catalog": {
        "type": "rest",
        "pyiceberg_ref_strategy": "uri-path",
    },
    "checks": [
        "rest-catalog-type",
        "pyiceberg-ref-in-uri",
        "warehouse-configured",
        "s3-path-style-access",
    ],
}


class IcebergResourceProvider(ResourceProviderPlugin):
    def get_backup_contributors(self) -> list[BackupContributorSpec]:
        """Expose the iceberg metadata inventory contribution (ADR 0049 §3)."""
        from phlo_iceberg.continuity import IcebergBackupContributor

        return [BackupContributorSpec(name="iceberg", provider=IcebergBackupContributor())]

    def get_evidence_profile_contributions(self) -> list[EvidenceProfileContributionSpec]:
        """Declare this provider's blessed run-evidence contribution."""
        from phlo.run_evidence.profiles import EvidenceProfileContribution
        from phlo.run_evidence.reconciliation import RequiredEvidenceRecord, RequiredEvidenceStage

        contribution = EvidenceProfileContribution(
            contribution_id="iceberg.snapshot",
            provider="iceberg",
            profile_id="wap",
            profile_version="1",
            stages=(RequiredEvidenceStage(stage_type="publish", provider="iceberg"),),
            required_records=(RequiredEvidenceRecord(family="resource", minimum=1),),
        )
        return [EvidenceProfileContributionSpec(name="iceberg.snapshot", provider=contribution)]

    def get_sling_connections(self) -> list[SlingConnectionSpec]:
        """Expose the iceberg Sling connection through the neutral seam."""
        from phlo_iceberg.settings import get_settings

        return [SlingConnectionSpec(name="iceberg", provider=get_settings())]

    """Resource provider plugin for Iceberg/Nessie catalog access.

    Registers Iceberg capabilities with Phlo's plugin system, providing:
    - Table storage via ``IcebergResource``
    - Schema migration via ``IcebergSchemaMigrator``

    The plugin advertises full Iceberg capability support for versioning,
    snapshots, and schema evolution.

    Example:
        Plugin registration::

            # Plugin is auto-registered via entry points
            # In pyproject.toml:
            [project.entry-points."phlo.resource_providers"]
            iceberg = "phlo_iceberg.plugin:IcebergResourceProvider"

        Access resources::

            from phlo.plugins import get_resource_provider

            provider = get_resource_provider("iceberg")
            resources = provider.get_resources()

            # Get table store
            table_store = provider.get_table_stores()[0]
            resource = table_store.provider

            # Use resource
            resource.append_parquet("raw.events", "/data/events.parquet")

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata.

        Example:
            Check plugin capabilities::

                provider = IcebergResourceProvider()
                meta = provider.metadata

                print(f"Plugin: {meta.name} v{meta.version}")
                print(f"Supports refs: {meta.support.supports_refs}")
                print(f"Supports snapshots: {meta.support.supports_snapshots}")

        """
        return PluginMetadata(
            name="iceberg",
            version="0.1.0",
            description="Iceberg/Nessie catalog resource for Phlo",
            support=CapabilitySupport(
                supports_refs=True,
                supports_snapshots=True,
                supports_schema_evolution=True,
                supports_time_travel=True,
            ),
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Get resource specs exposed by this plugin.

        Returns the primary Iceberg resource for table operations.

        Example:
            Get resources::

                provider = IcebergResourceProvider()
                specs = provider.get_resources()

                for spec in specs:
                    print(f"Resource: {spec.name}")
                    # Use spec.resource for table operations

        """
        return [ResourceSpec(name="table_store", resource=IcebergResource())]

    def get_table_stores(self) -> list[TableStoreSpec]:
        """Get table-store capability specs exposed by this plugin.

        Example:
            Get table store capabilities::

                provider = IcebergResourceProvider()
                stores = provider.get_table_stores()

                for store in stores:
                    print(f"Store: {store.name}")
                    print(f"Supports refs: {store.support.supports_refs}")
                    # Access store.provider for IcebergResource

        """
        return [
            TableStoreSpec(
                name="iceberg",
                provider=IcebergResource(),
                metadata={"compatibility": ICEBERG_COMPATIBILITY_METADATA},
                support=CapabilitySupport(
                    supports_refs=True,
                    supports_snapshots=True,
                    supports_schema_evolution=True,
                    supports_time_travel=True,
                ),
            )
        ]

    def get_schema_migrators(self) -> list[SchemaMigrationSpec]:
        """Get schema-migrator capability specs exposed by this plugin.

        Example:
            Get schema migrator::

                provider = IcebergResourceProvider()
                migrators = provider.get_schema_migrators()

                for migrator in migrators:
                    print(f"Migrator: {migrator.name}")
                    # Use migrator.provider for schema operations
                    # migrator.provider.diff_schema(...)

        """
        return [
            SchemaMigrationSpec(
                name="iceberg",
                provider=IcebergSchemaMigrator(),
                support=CapabilitySupport(supports_schema_evolution=True),
            )
        ]
