"""Tests that Observatory derives Iceberg compatibility from capability metadata.

Registry specs for table stores, catalogs, and query engines carry compatibility
blocks; these tests pin how those blocks combine into the reported compatibility
report and which named checks run against them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from phlo_api.observatory_api import iceberg as iceberg_api


class _Registry:
    def __init__(self, specs: dict[str, list[SimpleNamespace]]) -> None:
        self._specs = specs

    def list(self, family: str) -> list[SimpleNamespace]:
        return self._specs.get(family, [])


@pytest.mark.anyio
async def test_observatory_reports_compatibility_from_capabilities(monkeypatch) -> None:
    registry = _Registry(
        {
            "table_store": [
                SimpleNamespace(
                    metadata={
                        "compatibility": {
                            "target": "apache-iceberg-1.11",
                            "rest_catalog": {
                                "type": "rest",
                                "pyiceberg_ref_strategy": "uri-path",
                            },
                            "checks": ["rest-catalog-type", "pyiceberg-ref-in-uri"],
                        }
                    }
                )
            ],
            "catalog": [
                SimpleNamespace(
                    metadata={
                        "compatibility": {
                            "target": "apache-iceberg-1.11",
                            "rest_catalog": {"nessie_uri_suffix": "/iceberg"},
                            "checks": ["nessie-iceberg-rest-uri"],
                        }
                    }
                )
            ],
            "query_engine": [
                SimpleNamespace(
                    name="trino",
                    metadata={
                        "compatibility": {
                            "target": "apache-iceberg-1.11",
                            "rest_catalog": {"trino_ref_strategy": "rest-catalog-prefix"},
                            "engines": {
                                "trino": {
                                    "catalog_type": "rest",
                                    "iceberg_table_spec_versions": [1, 2],
                                }
                            },
                            "checks": ["trino-prefix-property", "trino-table-spec-v1-v2"],
                        }
                    },
                )
            ],
        }
    )
    monkeypatch.setattr(iceberg_api, "_load_capability_registry", lambda: registry)

    compatibility = await iceberg_api.get_compatibility()

    assert compatibility.target == "apache-iceberg-1.11"
    assert compatibility.rest_catalog["type"] == "rest"
    assert compatibility.rest_catalog["pyiceberg_ref_strategy"] == "uri-path"
    assert compatibility.rest_catalog["trino_ref_strategy"] == "rest-catalog-prefix"
    assert compatibility.engines["trino"]["catalog_type"] == "rest"
    assert compatibility.engines["trino"]["iceberg_table_spec_versions"] == [1, 2]
    assert compatibility.checks == [
        "rest-catalog-type",
        "pyiceberg-ref-in-uri",
        "nessie-iceberg-rest-uri",
        "trino-prefix-property",
        "trino-table-spec-v1-v2",
    ]


@pytest.mark.anyio
async def test_observatory_does_not_report_hardcoded_compatibility_without_capabilities(
    monkeypatch,
) -> None:
    monkeypatch.setattr(iceberg_api, "_load_capability_registry", lambda: _Registry({}))

    compatibility = await iceberg_api.get_compatibility()

    assert compatibility.target == "unavailable"
    assert compatibility.rest_catalog == {}
    assert compatibility.engines == {}
    assert compatibility.checks == []
