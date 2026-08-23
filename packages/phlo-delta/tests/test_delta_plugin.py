"""Tests for the Delta Lake resource provider plugin.

Verifies the provider exposes exactly one canonical table_store resource plus
table_store and schema_migrator capability metadata with Delta-specific
support flags.
"""

from phlo.capabilities import CapabilitySupport
from phlo_delta.plugin import DeltaResourceProvider
from phlo_delta.resource import DeltaResource
from phlo_delta.schema_migrator import DeltaSchemaMigrator


def test_delta_provider_registers_table_store_resource() -> None:
    """Resource provider should expose canonical table_store runtime resource."""
    provider = DeltaResourceProvider()

    resources = provider.get_resources()

    assert len(resources) == 1
    assert resources[0].name == "table_store"
    assert isinstance(resources[0].resource, DeltaResource)


def test_delta_provider_registers_table_store_capability() -> None:
    """Resource provider should expose table_store capability metadata."""
    provider = DeltaResourceProvider()

    table_stores = provider.get_table_stores()

    assert len(table_stores) == 1
    assert table_stores[0].name == "delta"
    assert isinstance(table_stores[0].provider, DeltaResource)
    assert table_stores[0].support == CapabilitySupport(
        supports_snapshots=True,
        supports_schema_evolution=True,
        supports_time_travel=True,
    )


def test_delta_provider_registers_schema_migrator_capability() -> None:
    """Resource provider should expose schema_migrator capability metadata."""
    provider = DeltaResourceProvider()

    migrators = provider.get_schema_migrators()

    assert len(migrators) == 1
    assert migrators[0].name == "delta"
    assert isinstance(migrators[0].provider, DeltaSchemaMigrator)
    assert migrators[0].support == CapabilitySupport(supports_schema_evolution=True)
