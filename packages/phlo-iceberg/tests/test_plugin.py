"""Tests for the Iceberg resource provider plugin.

Verifies the provider exposes exactly one canonical table_store resource plus
table_store and schema_migrator capability metadata with Iceberg-specific
support flags.
"""

from phlo.capabilities import CapabilitySupport
from phlo_iceberg.plugin import IcebergResourceProvider
from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.schema_migrator import IcebergSchemaMigrator


def test_iceberg_provider_registers_table_store_resource() -> None:
    """Resource provider should expose canonical table_store runtime resource."""
    provider = IcebergResourceProvider()

    resources = provider.get_resources()

    assert len(resources) == 1
    assert resources[0].name == "table_store"
    assert isinstance(resources[0].resource, IcebergResource)


def test_iceberg_provider_registers_table_store_capability() -> None:
    """Resource provider should expose table_store capability metadata."""
    provider = IcebergResourceProvider()

    table_stores = provider.get_table_stores()

    assert len(table_stores) == 1
    assert table_stores[0].name == "iceberg"
    assert isinstance(table_stores[0].provider, IcebergResource)
    assert table_stores[0].support == CapabilitySupport(
        supports_refs=True,
        supports_snapshots=True,
        supports_schema_evolution=True,
        supports_time_travel=True,
    )


def test_iceberg_provider_registers_schema_migrator_capability() -> None:
    """Resource provider should expose schema_migrator capability metadata."""
    provider = IcebergResourceProvider()

    migrators = provider.get_schema_migrators()

    assert len(migrators) == 1
    assert migrators[0].name == "iceberg"
    assert isinstance(migrators[0].provider, IcebergSchemaMigrator)
    assert migrators[0].support == CapabilitySupport(supports_schema_evolution=True)
