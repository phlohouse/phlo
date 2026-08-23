"""Tests for capability-driven Trino API defaults.

Verifies that default catalog, ref, URL, and discovery schemas come from
query-engine capability metadata and fail clearly when unconfigured.
Also pins externalized URIs rewriting internal poll URLs onto the
client-facing base and structured connection-health errors.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from phlo_api.observatory_api import iceberg, search
from phlo_api.observatory_api import trino


def test_resolve_default_catalog_uses_query_engine_metadata(monkeypatch) -> None:
    """Catalog resolution should prefer query-engine capability metadata."""
    monkeypatch.delenv("PHLO_QUERY_CATALOG", raising=False)
    monkeypatch.delenv("TRINO_CATALOG", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch(
            "phlo_api.observatory_api.trino.resolve_capability",
            return_value=Mock(metadata={"default_catalog": "warehouse"}),
        ),
    ):
        assert trino.resolve_default_catalog() == "warehouse"


def test_resolve_default_catalog_requires_configuration(monkeypatch) -> None:
    """Catalog resolution should fail clearly when nothing provides a default."""
    monkeypatch.delenv("PHLO_QUERY_CATALOG", raising=False)
    monkeypatch.delenv("TRINO_CATALOG", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch("phlo_api.observatory_api.trino.resolve_capability", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="No default query catalog"):
            trino.resolve_default_catalog()


def test_resolve_default_ref_uses_query_engine_metadata(monkeypatch) -> None:
    """Ref resolution should prefer query-engine capability metadata."""
    monkeypatch.delenv("PHLO_DEFAULT_REF", raising=False)
    monkeypatch.delenv("NESSIE_DEFAULT_REF", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch(
            "phlo_api.observatory_api.trino.resolve_capability",
            return_value=Mock(metadata={"default_ref": "dev"}),
        ),
    ):
        assert trino.resolve_default_ref() == "dev"


def test_resolve_trino_url_uses_query_engine_metadata(monkeypatch) -> None:
    """Query-engine URL resolution should prefer capability metadata."""
    monkeypatch.delenv("PHLO_QUERY_ENGINE_URL", raising=False)
    monkeypatch.delenv("TRINO_URL", raising=False)
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", lambda _host: "127.0.0.1")

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch(
            "phlo_api.observatory_api.trino.resolve_capability",
            return_value=Mock(metadata={"host": "query", "port": 9999, "scheme": "https"}),
        ),
    ):
        assert trino.resolve_trino_url() == "https://query:9999"


def test_resolve_trino_url_requires_configuration(monkeypatch) -> None:
    """Query-engine URL resolution should fail clearly when not configured."""
    monkeypatch.delenv("PHLO_QUERY_ENGINE_URL", raising=False)
    monkeypatch.delenv("TRINO_URL", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch("phlo_api.observatory_api.trino.resolve_capability", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="No query-engine URL is configured"):
            trino.resolve_trino_url()


def test_externalize_trino_uri_uses_client_facing_base_url() -> None:
    """Trino may return container-internal poll URLs behind a mapped Docker port."""
    assert (
        trino._externalize_trino_uri(
            "http://127.0.0.1:8080/v1/statement/queued/query/token/1",
            "http://localhost:10005",
        )
        == "http://localhost:10005/v1/statement/queued/query/token/1"
    )


def test_externalize_trino_uri_preserves_client_facing_base_path() -> None:
    assert (
        trino._externalize_trino_uri(
            "http://trino:8080/v1/statement/queued/query/token/1",
            "https://proxy.example.com/trino",
        )
        == "https://proxy.example.com/trino/v1/statement/queued/query/token/1"
    )


@pytest.mark.anyio
async def test_check_connection_returns_structured_error_when_url_unconfigured(monkeypatch) -> None:
    """Connection health should report config failures without raising."""
    monkeypatch.setattr(
        trino,
        "resolve_trino_url",
        lambda _override=None: (_ for _ in ()).throw(RuntimeError("missing query engine url")),
    )

    result = await trino.check_connection()

    assert result.connected is False
    assert result.error == "missing query engine url"


def test_resolve_table_discovery_schemas_uses_query_engine_metadata(monkeypatch) -> None:
    """Table discovery should use configured schema metadata when present."""
    monkeypatch.delenv("PHLO_API_DISCOVERY_SCHEMAS", raising=False)
    monkeypatch.delenv("PHLO_DEFAULT_REF", raising=False)
    monkeypatch.delenv("NESSIE_DEFAULT_REF", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch(
            "phlo_api.observatory_api.trino.resolve_capability",
            return_value=Mock(metadata={"discovery_schemas": ["bronze", "silver"]}),
        ),
    ):
        assert trino.resolve_table_discovery_schemas() == ["bronze", "silver"]


def test_resolve_table_discovery_schemas_requires_configuration(monkeypatch) -> None:
    """Table discovery should fail clearly without explicit schemas or branch context."""
    monkeypatch.delenv("PHLO_API_DISCOVERY_SCHEMAS", raising=False)
    monkeypatch.delenv("PHLO_DEFAULT_REF", raising=False)
    monkeypatch.delenv("NESSIE_DEFAULT_REF", raising=False)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch("phlo_api.observatory_api.trino.resolve_capability", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="No table-discovery schemas are configured"):
            trino.resolve_table_discovery_schemas()


@pytest.mark.anyio
async def test_get_tables_uses_explicit_discovery_schemas_without_default_ref(monkeypatch) -> None:
    """Table listing should not require a default ref when schemas are configured."""
    monkeypatch.setenv("PHLO_API_DISCOVERY_SCHEMAS", "bronze,silver")

    async def fake_execute(query: str, *_args, **_kwargs):
        if '"bronze"' in query:
            return {"rows": [{"Table": "dlt_orders"}]}
        if '"silver"' in query:
            return {"rows": [{"Table": "stg_orders"}]}
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(iceberg, "execute_trino_query", fake_execute)
    monkeypatch.setattr(iceberg, "resolve_default_catalog", lambda: "warehouse")
    monkeypatch.setattr(
        iceberg,
        "resolve_default_ref",
        lambda: (_ for _ in ()).throw(AssertionError("default ref should not be resolved")),
    )

    result = await iceberg.get_tables()

    assert [table.schema_name for table in result] == ["bronze", "silver"]


@pytest.mark.anyio
async def test_search_index_uses_table_schema_without_default_ref(monkeypatch) -> None:
    """Search indexing should reuse discovered table schemas instead of forcing a default ref."""

    async def fake_assets(_dagster_url: str | None = None):
        return [
            SimpleNamespace(
                id="orders",
                key_path="orders",
                group_name="core",
                compute_kind="sql",
            )
        ]

    async def fake_tables(*_args, **_kwargs):
        return [
            SimpleNamespace(
                catalog="warehouse",
                schema_name="silver",
                name="stg_orders",
                full_name='"warehouse"."silver"."stg_orders"',
                layer="silver",
            )
        ]

    async def fake_schema(table: str, schema: str, branch: str | None, *_args, **_kwargs):
        assert table == "stg_orders"
        assert schema == "silver"
        assert branch is None
        return [SimpleNamespace(name="order_id", type="bigint")]

    monkeypatch.setattr(search, "get_assets", fake_assets)
    monkeypatch.setattr(search, "get_tables", fake_tables)
    monkeypatch.setattr(search, "get_table_schema", fake_schema)
    monkeypatch.setattr(search, "resolve_default_catalog", lambda: "warehouse")

    result = await search.get_search_index(include_columns=True)

    assert isinstance(result, search.SearchIndex)
    assert [column.table_schema for column in result.columns] == ["silver"]
