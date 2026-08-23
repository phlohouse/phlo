"""Tests for the Sling replication decorator.

Verifies replication mode validation, the incremental update_key requirement,
and that decorated replications register provider-neutral assets in the
global registry until cleared.
"""

import pytest
from types import SimpleNamespace

from phlo.exceptions import PhloConfigError
from phlo_sling.decorator import (
    _validate_incremental_config,
    _validate_replication_mode,
    clear_sling_assets,
    get_sling_assets,
    phlo_sling_assets,
    phlo_sling_replication,
)
from phlo_sling.registry import SlingReplication


def test_validate_replication_mode_valid():
    """Valid modes do not raise."""
    for mode in ("full-refresh", "incremental", "snapshot", "backfill"):
        _validate_replication_mode(mode)


def test_validate_replication_mode_invalid():
    """Invalid mode raises PhloConfigError."""
    with pytest.raises(PhloConfigError):
        _validate_replication_mode("upsert")


def test_validate_incremental_requires_update_key():
    """Incremental mode without update_key raises."""
    with pytest.raises(PhloConfigError):
        _validate_incremental_config("incremental", None)


def test_validate_incremental_with_update_key():
    """Incremental mode with update_key does not raise."""
    _validate_incremental_config("incremental", "updated_at")


def test_decorator_registers_asset():
    """Decorator registers an asset in the global registry."""
    clear_sling_assets()

    @phlo_sling_replication(
        stream_name="public.users",
        table_name="users",
        source_conn="TEST_PG",
        group="test",
        mode="full-refresh",
    )
    def my_replication(context):
        return None

    assets = get_sling_assets()
    assert len(assets) == 1
    assert assets[0].key == "sling_users"
    assert assets[0].group == "test"
    assert "sling" in assets[0].kinds
    clear_sling_assets()


def test_sling_replication_asset_has_provider_neutral_metadata() -> None:
    """Sling assets should expose provider-neutral metadata for core surfaces."""
    clear_sling_assets()
    try:

        @phlo_sling_replication(
            stream_name="public.users",
            table_name="users",
            source_conn="PHLO_POSTGRES",
            group="raw",
            mode="incremental",
            primary_key="id",
            update_key="updated_at",
        )
        def users(context):
            return None

        asset = get_sling_assets()[0]

        assert asset.tags["provider"] == "sling"
        assert asset.tags["asset_type"] == "ingestion"
        assert asset.metadata["provider"] == "sling"
        assert asset.metadata["asset_type"] == "ingestion"
        assert asset.metadata["table_name"] == "users"
        assert asset.metadata["source_name"] == "public.users"
        assert asset.metadata["write_mode"] == "incremental"
        assert asset.metadata["primary_key"] == ["id"]
    finally:
        clear_sling_assets()


def test_sling_replication_reserved_metadata_overrides_extras() -> None:
    """Provider-neutral Sling metadata should not be overridable by extras."""
    clear_sling_assets()
    try:

        @phlo_sling_assets(group="raw")
        def discover_users():
            return [
                SlingReplication(
                    stream_name="public.users",
                    table_name="users",
                    source_conn="PHLO_POSTGRES",
                    mode="incremental",
                    primary_key="id",
                    update_key="updated_at",
                    tags={"provider": "custom", "asset_type": "custom"},
                    metadata={
                        "provider": "custom",
                        "asset_type": "custom",
                        "source_name": "custom",
                        "write_mode": "custom",
                    },
                )
            ]

        asset = get_sling_assets()[0]

        assert asset.tags["provider"] == "sling"
        assert asset.tags["asset_type"] == "ingestion"
        assert asset.metadata["provider"] == "sling"
        assert asset.metadata["asset_type"] == "ingestion"
        assert asset.metadata["source_name"] == "public.users"
        assert asset.metadata["write_mode"] == "incremental"
    finally:
        clear_sling_assets()


def test_decorator_attaches_config():
    """Decorator attaches _phlo_replication_config to the function."""
    clear_sling_assets()

    @phlo_sling_replication(
        stream_name="public.orders",
        table_name="orders",
        source_conn="TEST_PG",
        group="test",
        mode="full-refresh",
        primary_key="id",
    )
    def my_replication(context):
        return None

    assert hasattr(my_replication, "_phlo_replication_config")
    config = my_replication._phlo_replication_config  # type: ignore[attr-defined]
    assert config.stream_name == "public.orders"  # type: ignore[attr-defined]
    assert config.primary_key == ["id"]  # type: ignore[attr-defined]
    clear_sling_assets()


def test_decorator_uses_configured_default_mode(monkeypatch) -> None:
    """Decorator should honor SLING_DEFAULT_MODE when mode is omitted."""
    clear_sling_assets()
    monkeypatch.setattr(
        "phlo_sling.decorator.get_settings",
        lambda: SimpleNamespace(sling_default_mode="full-refresh"),
    )

    @phlo_sling_replication(
        stream_name="public.users",
        table_name="users",
        source_conn="TEST_PG",
        group="test",
    )
    def my_replication(context):
        return None

    config = my_replication._phlo_replication_config  # type: ignore[attr-defined]
    assert config.mode == "full-refresh"  # type: ignore[attr-defined]
    clear_sling_assets()
