"""Environment alias resolution for Nessie settings.

Ensures the default ref reads NESSIE_DEFAULT_REF while legacy Iceberg variables
are ignored rather than silently honored.
"""

from phlo_nessie.settings import get_settings


def test_nessie_default_ref_uses_nessie_env(monkeypatch) -> None:
    """Ensure Nessie default ref reads from NESSIE_DEFAULT_REF."""
    monkeypatch.setenv("NESSIE_DEFAULT_REF", "dev")
    get_settings.cache_clear()
    try:
        assert get_settings().nessie_default_ref == "dev"
    finally:
        get_settings.cache_clear()


def test_nessie_default_ref_ignores_legacy_iceberg_env(monkeypatch) -> None:
    """Ensure removed legacy Iceberg ref env is ignored."""
    monkeypatch.delenv("NESSIE_DEFAULT_REF", raising=False)
    monkeypatch.setenv("ICEBERG_NESSIE_REF", "dev")
    get_settings.cache_clear()
    try:
        assert get_settings().nessie_default_ref == "main"
    finally:
        get_settings.cache_clear()
