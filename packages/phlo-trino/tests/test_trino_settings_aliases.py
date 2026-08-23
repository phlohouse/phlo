"""Tests for Trino settings environment resolution.

Verifies the default ref reads TRINO_DEFAULT_REF and ignores the removed
legacy Iceberg ref variable, falling back to main. The cached settings are
cleared around each case.
"""

from phlo_trino.settings import get_settings


def test_trino_default_ref_uses_trino_env(monkeypatch) -> None:
    """Ensure Trino default ref reads from TRINO_DEFAULT_REF."""
    monkeypatch.setenv("TRINO_DEFAULT_REF", "dev")
    get_settings.cache_clear()
    try:
        assert get_settings().trino_default_ref == "dev"
    finally:
        get_settings.cache_clear()


def test_trino_default_ref_ignores_legacy_iceberg_env(monkeypatch) -> None:
    """Ensure removed legacy Iceberg ref env is ignored."""
    monkeypatch.delenv("TRINO_DEFAULT_REF", raising=False)
    monkeypatch.setenv("ICEBERG_NESSIE_REF", "dev")
    get_settings.cache_clear()
    try:
        assert get_settings().trino_default_ref == "main"
    finally:
        get_settings.cache_clear()
