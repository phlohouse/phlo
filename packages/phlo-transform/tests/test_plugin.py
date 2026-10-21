"""Entry-point registration and decorator-asset exposure."""

from __future__ import annotations

from importlib.metadata import entry_points

import pytest

from phlo import transform
from phlo.plugins.discovery import discover_plugins, get_global_registry
from phlo_transform.plugin import TransformAssetProvider, TransformProvider


@pytest.fixture(autouse=True)
def _clean_registries():
    transform.clear_transform_assets()
    get_global_registry().clear()
    yield
    transform.clear_transform_assets()
    get_global_registry().clear()


def test_package_registers_all_transform_entry_points() -> None:
    asset_entry = next(
        entry_point
        for entry_point in entry_points(group="phlo.plugins.assets")
        if entry_point.name == "transform"
    )
    provider_entry = next(
        entry_point
        for entry_point in entry_points(group="phlo.plugins.transformation_providers")
        if entry_point.name == "transform"
    )
    cli_entry = next(
        entry_point
        for entry_point in entry_points(group="phlo.plugins.cli")
        if entry_point.name == "transform"
    )

    assert asset_entry.load() is TransformAssetProvider
    assert provider_entry.load() is TransformProvider
    assert cli_entry.load()().metadata.name == "transform"


def test_providers_report_transform_metadata() -> None:
    assert TransformProvider().metadata.name == "transform"
    assert TransformAssetProvider().metadata.name == "transform"
    assert TransformProvider().metadata.homepage == "https://github.com/phlohouse/phlo-transform"


def test_sql_decorator_registers_without_manual_plugin_setup() -> None:
    @transform.sql(table="silver.orders", depends_on=["bronze.orders"])
    def orders() -> str:
        return "select * from bronze.orders"

    assets = TransformProvider().get_asset_retriever()()

    assert [asset.key for asset in assets] == ["transform_silver_orders"]
    assert assets[0].metadata["sql"] == "select * from bronze.orders"


def test_asset_provider_exposes_declared_assets() -> None:
    @transform.sql(table="gold.orders")
    def orders() -> str:
        return "select * from silver.orders"

    assets = list(TransformAssetProvider().get_assets())

    assert [asset.key for asset in assets] == ["transform_gold_orders"]


def test_phlo_discovers_transform_providers() -> None:
    discover_plugins(plugin_type="asset_provider", auto_register=True)
    discover_plugins(plugin_type="transformation_provider", auto_register=True)
    registry = get_global_registry()

    assert isinstance(registry.get("asset_provider", "transform"), TransformAssetProvider)
    assert isinstance(registry.get("transformation_provider", "transform"), TransformProvider)
