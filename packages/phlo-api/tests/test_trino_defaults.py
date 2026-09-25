"""Test capability-driven Trino defaults and client-facing poll URLs."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

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
