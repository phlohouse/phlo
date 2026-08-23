"""Tests for DLT settings environment resolution.

Settings read only DLT_* variables; the removed legacy
ICEBERG_DEFAULT_NAMESPACE must be ignored. Each case clears the settings
cache so env changes are picked up.
"""

from phlo_dlt.registry import TableConfig
from phlo_dlt.settings import get_settings


def test_dlt_namespace_uses_dlt_env(monkeypatch) -> None:
    """Ensure DLT namespace reads from DLT_DEFAULT_NAMESPACE."""
    monkeypatch.setenv("DLT_DEFAULT_NAMESPACE", "bronze")
    get_settings.cache_clear()
    try:
        assert get_settings().dlt_default_namespace == "bronze"
        table = TableConfig(
            table_name="events",
            table_schema=object(),
            validation_schema=None,
            unique_key="id",
            group_name="ingestion",
        )
        assert table.full_table_name == "bronze.events"
    finally:
        get_settings.cache_clear()


def test_dlt_namespace_ignores_legacy_iceberg_env(monkeypatch) -> None:
    """Ensure removed legacy Iceberg namespace env is ignored."""
    monkeypatch.delenv("DLT_DEFAULT_NAMESPACE", raising=False)
    monkeypatch.setenv("ICEBERG_DEFAULT_NAMESPACE", "bronze")
    get_settings.cache_clear()
    try:
        assert get_settings().dlt_default_namespace == "raw"
    finally:
        get_settings.cache_clear()
