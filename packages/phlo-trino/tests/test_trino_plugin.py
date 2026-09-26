"""Tests for Trino service plugin.

Validates service-definition fields, pinning of the upstream image by
digest with no local build, and that every runtime file copied into
.phlo/trino ships in installed wheels via package data.
"""

from pathlib import Path
from fnmatch import fnmatch
import json
import tomllib

from phlo_trino.plugin import TrinoServicePlugin


def test_trino_service_definition():
    """Validate Trino service definition fields."""

    plugin = TrinoServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "trino"
    assert service_definition["category"] == "core"


def test_trino_uses_the_upstream_image_without_a_local_build():
    service_definition = TrinoServicePlugin().service_definition

    assert service_definition["image"] == (
        "trinodb/trino:483@sha256:db58cc93e593a2706553745f276bb119c9810e69918be56ecde088ba7ccb0534"
    )
    assert "build" not in service_definition
    assert all(file_spec["source"] != "Dockerfile" for file_spec in service_definition["files"])


def test_trino_runtime_files_are_included_in_package_data():
    """Every file copied into .phlo/trino must be present in installed wheels."""

    project_root = Path(__file__).parents[1]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text())
    package_data = set(pyproject["tool"]["setuptools"]["package-data"]["phlo_trino"])

    service_definition = TrinoServicePlugin().service_definition
    file_sources = {file_spec["source"] for file_spec in service_definition["files"]}

    for source in file_sources:
        if "*" in source:
            continue
        assert any(fnmatch(source, pattern) for pattern in package_data)

    assert "Dockerfile" not in package_data


def test_optional_preview_bundle_pins_distinct_read_only_refs_with_budgets():
    project_root = Path(__file__).parents[1]
    preview = project_root / "src" / "phlo_trino" / "preview"
    catalogs = {
        "prod": (preview / "catalog" / "iceberg_preview_prod.properties").read_text(),
        "staging": (preview / "catalog" / "iceberg_preview_staging.properties").read_text(),
    }
    assert "iceberg.rest-catalog.prefix=main" in catalogs["prod"]
    assert "iceberg.rest-catalog.prefix=dev" in catalogs["staging"]
    assert all("iceberg.security=READ_ONLY" in text for text in catalogs.values())

    access = json.loads((preview / "access-control.json").read_text())
    assert access["catalogs"][0] == {
        "user": "phlo_api_preview",
        "catalog": "iceberg_preview_(prod|staging)",
        "allow": "read-only",
    }
    assert access["catalogs"][1]["user"] == "phlo_api_preview"
    assert access["catalogs"][1]["allow"] == "none"
    assert access["system_session_properties"][0] == {
        "user": "phlo_api_preview",
        "allow": False,
    }

    groups = json.loads((preview / "resource-groups.json").read_text())
    assert groups["physicalDataScanQuotaPeriod"] == "1h"
    api_group = groups["rootGroups"][0]["subGroups"][0]["subGroups"][0]
    assert api_group["hardConcurrencyLimit"] == 2
    assert api_group["maxQueued"] == 0
    assert api_group["hardPhysicalDataScanLimit"] == "1GB"

    session_property_rules = json.loads((preview / "session-property-config.json").read_text())
    assert session_property_rules == [
        {
            "group": "^global\\.preview\\.api$",
            "sessionProperties": {
                "query_max_run_time": "20s",
                "query_max_planning_time": "5s",
                "query_max_scan_physical_bytes": "256MB",
            },
        }
    ]

    config = (preview / "config.properties.template").read_text()
    assert "http-server.https.enabled=true" in config
    assert "http-server.authentication.type=PASSWORD" in config
    assert "http-server.https.port=8443" in config
    session_config = (preview / "session-property-config.properties").read_text()
    assert "session-property-config.configuration-manager=file" in session_config
    assert "session-property-manager.config-file=" in session_config


def test_optional_preview_bundle_is_not_activated_by_the_trino_service():
    service_definition = TrinoServicePlugin().service_definition
    runtime_files = {file_spec["source"] for file_spec in service_definition["files"]}
    assert not any(path.startswith("preview/") for path in runtime_files)
