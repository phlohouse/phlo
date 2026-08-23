"""Tests for Trino service plugin.

Validates service-definition fields, pinning of the upstream image by
digest with no local build, and that every runtime file copied into
.phlo/trino ships in installed wheels via package data.
"""

from pathlib import Path
from fnmatch import fnmatch
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
