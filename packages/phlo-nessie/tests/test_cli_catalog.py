"""Tests the nessie catalog CLI: backend selection and behavior when the
catalog endpoint is unreachable."""

import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from phlo_nessie import cli_catalog
from phlo_nessie import catalog_backend


def test_pyiceberg_catalog_config_resolves_unreachable_minio_endpoint(
    tmp_path, monkeypatch
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("MINIO_API_PORT=19001\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIO_API_PORT", raising=False)
    monkeypatch.delenv("ICEBERG_S3_ENDPOINT", raising=False)
    monkeypatch.delenv("S3_ENDPOINT", raising=False)

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    config = catalog_backend._pyiceberg_catalog_config("main")

    assert config["s3.endpoint"] == "http://localhost:19001"


def test_pyiceberg_catalog_config_uses_nessie_warehouse_identifier(monkeypatch) -> None:
    monkeypatch.setenv("ICEBERG_WAREHOUSE_PATH", "s3://other-lake/other-warehouse")

    config = catalog_backend._pyiceberg_catalog_config("main")

    assert config["warehouse"] == "warehouse"


def test_get_iceberg_catalog_loads_catalog_backend(monkeypatch) -> None:
    mock_catalog = MagicMock()
    mock_loader = MagicMock(return_value=mock_catalog)

    monkeypatch.setattr(catalog_backend, "load_pyiceberg_catalog", mock_loader)

    result = cli_catalog._get_iceberg_catalog(ref="dev")

    mock_loader.assert_called_once_with(ref="dev")
    assert result is mock_catalog


def test_get_iceberg_catalog_raises_clear_error_when_backend_missing(monkeypatch) -> None:
    original_import = __import__

    def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "phlo_nessie.catalog_backend":
            raise ImportError("missing backend")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", raising_import)

    with pytest.raises(RuntimeError, match="Iceberg catalog support is not installed"):
        cli_catalog._get_iceberg_catalog()


def test_value_or_call_supports_property_and_method_values() -> None:
    assert cli_catalog._value_or_call(2) == 2
    assert cli_catalog._value_or_call(lambda: 2) == 2


def test_schema_field_display_supports_current_pyiceberg_nested_field() -> None:
    field = SimpleNamespace(name="id", field_type="long", required=True)

    assert cli_catalog._schema_field_display(field) == ("id", "long", "✓")


def test_schema_field_display_supports_current_pyiceberg_optional_field() -> None:
    field = SimpleNamespace(name="nickname", field_type="string", required=False)

    assert cli_catalog._schema_field_display(field) == ("nickname", "string", "")


def test_schema_field_display_supports_legacy_type_optional_marker() -> None:
    field_type = SimpleNamespace(is_optional=True)
    field = SimpleNamespace(name="name", type=field_type)

    assert cli_catalog._schema_field_display(field) == ("name", str(field_type), "")


def test_schema_field_display_supports_legacy_type_required_marker() -> None:
    field_type = SimpleNamespace(is_optional=False)
    field = SimpleNamespace(name="name", type=field_type)

    assert cli_catalog._schema_field_display(field) == ("name", str(field_type), "✓")
