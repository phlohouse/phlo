"""Tests for Python-first Sling asset discovery.

Verifies the decorator registers multiple assets and applies default modes at
collection time.
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo_sling import SlingReplication
from phlo_sling.decorator import clear_sling_assets, get_sling_assets, phlo_sling_assets


def test_phlo_sling_assets_registers_multiple_assets() -> None:
    """Discovery decorator should register one asset per replication definition."""
    clear_sling_assets()

    @phlo_sling_assets(group="files", owner="data-team")
    def discover_assets():
        return [
            SlingReplication(
                stream_name="file:///mnt/share/customers/*.csv",
                table_name="customers_stage",
                source_conn="LOCAL",
                target_conn="WAREHOUSE",
                object="raw.customers_stage",
                mode="full-refresh",
                description="Customer CSV drop",
                metadata={"path_kind": "folder"},
                tags={"format": "csv"},
            ),
            SlingReplication(
                stream_name="file:///mnt/share/reports/*.xlsx",
                table_name="reports_stage",
                source_conn="LOCAL",
                target_conn="WAREHOUSE",
                object="raw.reports_stage",
                mode="full-refresh",
                source_options={"sheet": "Sheet1!A:F"},
                group_name="finance",
            ),
        ]

    assets = get_sling_assets()

    assert len(assets) == 2
    assert assets[0].key == "sling_customers_stage"
    assert assets[0].group == "files"
    assert assets[0].description == "Customer CSV drop"
    assert assets[0].metadata["owner"] == "data-team"
    assert assets[0].metadata["path_kind"] == "folder"
    assert assets[0].tags["format"] == "csv"
    assert assets[1].group == "finance"

    clear_sling_assets()


def test_phlo_sling_assets_uses_default_mode(monkeypatch) -> None:
    """Discovery decorator should honor SLING_DEFAULT_MODE when mode is omitted."""
    clear_sling_assets()
    monkeypatch.setattr(
        "phlo_sling.decorator.get_settings",
        lambda: SimpleNamespace(sling_default_mode="full-refresh"),
    )

    @phlo_sling_assets(group="files")
    def discover_assets():
        return [
            SlingReplication(
                stream_name="file:///mnt/share/customers/*.csv",
                table_name="customers_stage",
                source_conn="LOCAL",
                target_conn="WAREHOUSE",
                object="raw.customers_stage",
            )
        ]

    assets = get_sling_assets()

    assert assets[0].metadata["mode"] == "full-refresh"

    clear_sling_assets()
