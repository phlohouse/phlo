"""Tests for OpenMetadata capability resolution helpers.

Helpers must resolve providers through the capability registry, read stable
capability metadata rather than provider internals, and raise a clear
RuntimeError when the required capability or metadata is missing.
"""

from unittest.mock import Mock, patch

from phlo_openmetadata.capabilities import (
    resolve_catalog_scanner,
    resolve_query_engine_catalog,
    resolve_query_engine_service_type,
)


def test_resolve_catalog_scanner_returns_provider():
    """Capability helper returns the resolved scanner provider."""
    provider = Mock()

    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch(
            "phlo_openmetadata.capabilities.resolve_capability",
            return_value=Mock(provider=provider),
        ) as resolve_mock,
    ):
        result = resolve_catalog_scanner("nessie")

    assert result is provider
    resolve_mock.assert_called_once_with("catalog_scanner", "nessie")


def test_resolve_catalog_scanner_raises_when_missing():
    """Capability helper fails clearly when scanner capability is unavailable."""
    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch("phlo_openmetadata.capabilities.resolve_capability", return_value=None),
    ):
        try:
            resolve_catalog_scanner("nessie")
        except RuntimeError as exc:
            assert "nessie" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected RuntimeError")


def test_resolve_query_engine_catalog_uses_capability_metadata():
    """Catalog helper reads stable metadata instead of provider internals."""
    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch(
            "phlo_openmetadata.capabilities.resolve_capability",
            return_value=Mock(metadata={"catalog": "iceberg_dev"}),
        ) as resolve_mock,
    ):
        result = resolve_query_engine_catalog("duckdb")

    assert result == "iceberg_dev"
    resolve_mock.assert_called_once_with("query_engine", "duckdb")


def test_resolve_query_engine_catalog_falls_back_when_metadata_missing():
    """Catalog helper fails when metadata is absent."""
    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch(
            "phlo_openmetadata.capabilities.resolve_capability",
            return_value=Mock(metadata={}),
        ),
    ):
        try:
            resolve_query_engine_catalog()
        except RuntimeError as exc:
            assert "catalog metadata" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected RuntimeError")


def test_resolve_query_engine_catalog_raises_when_capability_missing():
    """Catalog helper fails clearly when no query-engine capability resolves."""
    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch("phlo_openmetadata.capabilities.resolve_capability", return_value=None),
    ):
        try:
            resolve_query_engine_catalog("duckdb")
        except RuntimeError as exc:
            assert "duckdb" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected RuntimeError")


def test_resolve_query_engine_service_type_uses_capability_metadata():
    """Service type helper reads stable metadata from the query engine capability."""
    with (
        patch("phlo_openmetadata.capabilities._discover_capabilities"),
        patch(
            "phlo_openmetadata.capabilities.resolve_capability",
            return_value=Mock(metadata={"service_type": "Trino"}),
        ) as resolve_mock,
    ):
        result = resolve_query_engine_service_type("duckdb")

    assert result == "Trino"
    resolve_mock.assert_called_once_with("query_engine", "duckdb")
