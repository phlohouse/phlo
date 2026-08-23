"""Unit tests for CapabilityFamily and CapabilityRegistry: snapshot
isolation of listings, register/lookup, and spec resolution."""

from __future__ import annotations

from dataclasses import dataclass

from phlo.capabilities.catalog import CapabilityFamily, named_family
from phlo.capabilities.registry import CapabilityRegistry
from phlo.capabilities.specs import (
    AssetCheckSpec,
    AssetSpec,
    QueryEngineSpec,
    ResourceSpec,
    RunSpec,
    TableStoreSpec,
)


@dataclass(frozen=True)
class DummySpec:
    name: str
    value: int


def test_capability_family_registers_and_lists_snapshot() -> None:
    family = CapabilityFamily[DummySpec, str](key=lambda spec: spec.name)

    family.register(DummySpec(name="trino", value=1))
    listed = family.list()
    listed.append(DummySpec(name="duckdb", value=2))

    assert family.list() == [DummySpec(name="trino", value=1)]


def test_capability_family_replaces_same_key() -> None:
    family = CapabilityFamily[DummySpec, str](key=lambda spec: spec.name)

    family.register(DummySpec(name="trino", value=1))
    family.register(DummySpec(name="trino", value=2))

    assert family.list() == [DummySpec(name="trino", value=2)]


def test_registry_core_families_keep_existing_interface() -> None:
    registry = CapabilityRegistry()
    asset = AssetSpec(
        key="raw.events",
        group=None,
        description=None,
        run=RunSpec(fn=lambda _ctx: []),
    )
    check = AssetCheckSpec(asset_key="raw.events", name="freshness", fn=lambda _ctx: None)
    resource = ResourceSpec(name="trino", resource=object())

    registry.register("asset", asset)
    registry.register("check", check)
    registry.register("resource", resource)

    assert registry.list("asset") == [asset]
    assert registry.list("check") == [check]
    assert registry.list("resource") == [resource]


def test_named_family_uses_spec_name() -> None:
    family: CapabilityFamily[DummySpec, str] = named_family()

    family.register(DummySpec(name="iceberg", value=1))

    assert family.list() == [DummySpec(name="iceberg", value=1)]


def test_registry_named_provider_families_keep_existing_interface() -> None:
    registry = CapabilityRegistry()
    table_store = TableStoreSpec(name="iceberg", provider=object())
    query_engine = QueryEngineSpec(name="trino", provider=object())

    registry.register("table_store", table_store)
    registry.register("query_engine", query_engine)

    assert registry.list("table_store") == [table_store]
    assert registry.list("query_engine") == [query_engine]
