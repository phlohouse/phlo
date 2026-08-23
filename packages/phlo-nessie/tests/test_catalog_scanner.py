"""Tests for Nessie catalog scanner capability resolution.

Stubs a query engine to verify namespace listing goes through the QueryEngine
capability, that resolution returns None when the capability is missing, and
that the configured capability name is honoured.
"""

from __future__ import annotations

import phlo_nessie.catalog_scanner as catalog_scanner_module

from phlo_nessie.catalog_scanner import NessieTableScanner


class _QueryEngine:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, sql: str, params=None, schema=None):
        self.queries.append(sql)
        return self.rows


def test_list_namespaces_uses_query_engine_capability(monkeypatch) -> None:
    """Fallback namespace scans should resolve the query engine via capabilities."""
    engine = _QueryEngine([("raw",), ("curated",)])
    monkeypatch.setattr(
        catalog_scanner_module,
        "get_settings",
        lambda: type("Settings", (), {"nessie_query_engine": None})(),
    )
    monkeypatch.setattr(
        catalog_scanner_module,
        "resolve_capability",
        lambda capability_type, name, **_kwargs: (
            type("Resolution", (), {"provider": engine})()
            if capability_type == "query_engine" and name is None
            else None
        ),
    )
    scanner = NessieTableScanner("http://nessie.example")

    namespaces = scanner._list_namespaces_via_trino()

    assert namespaces == [{"namespace": ["raw"]}, {"namespace": ["curated"]}]
    assert engine.queries == ["SHOW SCHEMAS"]


def test_get_query_engine_returns_none_when_capability_missing(monkeypatch) -> None:
    """Fallback paths should tolerate missing query engine providers."""
    monkeypatch.setattr(
        catalog_scanner_module,
        "get_settings",
        lambda: type("Settings", (), {"nessie_query_engine": None})(),
    )
    monkeypatch.setattr(
        catalog_scanner_module,
        "resolve_capability",
        lambda *_args, **_kwargs: None,
    )
    scanner = NessieTableScanner("http://nessie.example")

    assert scanner._get_query_engine() is None


def test_get_query_engine_uses_configured_capability_name(monkeypatch) -> None:
    """Fallback scans should honor a configured query_engine provider name."""
    engine = _QueryEngine([])
    monkeypatch.setattr(
        catalog_scanner_module,
        "get_settings",
        lambda: type("Settings", (), {"nessie_query_engine": "duckdb"})(),
    )
    monkeypatch.setattr(
        catalog_scanner_module,
        "resolve_capability",
        lambda capability_type, name, **_kwargs: (
            type("Resolution", (), {"provider": engine})()
            if capability_type == "query_engine" and name == "duckdb"
            else None
        ),
    )
    scanner = NessieTableScanner("http://nessie.example")

    assert scanner._get_query_engine() is engine
