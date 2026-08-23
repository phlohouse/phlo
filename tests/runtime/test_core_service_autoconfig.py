"""Integration tests for core service auto-configuration.

Every core service (postgres, minio, nessie, trino, dagster) must
declare its dependencies and auto-setup hooks, reference only
placeholders defined in env_vars, and own its host-port defaults so
auto-configuration never depends on undeclared configuration.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

import pytest
from phlo_dagster.plugin import DagsterServicePlugin
from phlo_minio.plugin import MinioServicePlugin
from phlo_nessie.plugin import NessieServicePlugin
from phlo_postgres.plugin import PostgresServicePlugin
from phlo_trino.plugin import TrinoServicePlugin

pytestmark = pytest.mark.integration

REQUIRED_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ServiceFixture:
    """Store service name and discovered service definition for assertions."""

    def __init__(self, name: str, definition: Mapping[str, Any]):
        """Initialize the fixture with a service identifier and its plugin
        service definition."""
        self.name = name
        self.definition = definition


CORE_SERVICES = {
    "postgres": ServiceFixture("postgres", PostgresServicePlugin().service_definition),
    "minio": ServiceFixture("minio", MinioServicePlugin().service_definition),
    "nessie": ServiceFixture("nessie", NessieServicePlugin().service_definition),
    "trino": ServiceFixture("trino", TrinoServicePlugin().service_definition),
    "dagster": ServiceFixture("dagster", DagsterServicePlugin().service_definition),
}


def _extract_required_placeholders(value: str) -> set[str]:
    """Extract ``${VAR}`` placeholder names (without delimiters) from a string."""
    return {match.group(1) for match in REQUIRED_PLACEHOLDER_RE.finditer(value)}


def _collect_required_placeholders(values: Iterable[object]) -> set[str]:
    """Collect the union of required placeholder names across string values."""
    placeholders: set[str] = set()
    for item in values:
        if isinstance(item, str):
            placeholders.update(_extract_required_placeholders(item))
    return placeholders


def _collect_required_service_placeholders(definition: Mapping[str, Any]) -> set[str]:
    """Collect required placeholders referenced by a service definition's image,
    build args, and compose environment/port data."""
    placeholders: set[str] = set()

    image = definition.get("image")
    if isinstance(image, str):
        placeholders.update(_extract_required_placeholders(image))

    build = definition.get("build")
    if isinstance(build, dict):
        args = build.get("args")
        if isinstance(args, dict):
            placeholders.update(_collect_required_placeholders(args.values()))

    compose = definition.get("compose")
    if isinstance(compose, dict):
        environment = compose.get("environment")
        if isinstance(environment, dict):
            placeholders.update(_collect_required_placeholders(environment.values()))
        ports = compose.get("ports")
        if isinstance(ports, list):
            placeholders.update(_collect_required_placeholders(ports))

    return placeholders


def test_core_service_dependencies_are_declared() -> None:
    """Verify core service dependency declarations match expected topology."""
    expected = {
        "dagster": {"postgres", "minio", "nessie", "trino"},
        "nessie": {"postgres", "minio"},
        "trino": {"nessie", "minio"},
        "postgres": {"postgres-volume-setup"},
        "minio": set(),
    }

    for name, fixture in CORE_SERVICES.items():
        depends_on = fixture.definition.get("depends_on", [])
        if isinstance(depends_on, list):
            assert set(depends_on) == expected[name]
        else:
            assert expected[name] == set()


def test_core_service_hooks_configured_for_auto_setup() -> None:
    """Verify required post-start hooks are configured for auto-setup."""
    dagster_hooks = CORE_SERVICES["dagster"].definition.get("hooks", {})
    assert isinstance(dagster_hooks, dict)
    post_start = dagster_hooks.get("post_start", [])
    assert isinstance(post_start, list)
    assert any(
        hook.get("name") == "dbt-compile" and hook.get("requires") == "phlo_dbt"
        for hook in post_start
        if isinstance(hook, dict)
    )

    nessie_hooks = CORE_SERVICES["nessie"].definition.get("hooks", {})
    assert isinstance(nessie_hooks, dict)
    post_start = nessie_hooks.get("post_start", [])
    assert isinstance(post_start, list)
    assert any(hook.get("name") == "init-branches" for hook in post_start if isinstance(hook, dict))


def test_core_service_required_placeholders_defined_in_env_vars() -> None:
    """Verify required placeholders are defined in service env var maps."""
    available_env = set()
    placeholders: set[str] = set()
    for fixture in CORE_SERVICES.values():
        env_vars = fixture.definition.get("env_vars", {})
        if isinstance(env_vars, dict):
            available_env.update(env_vars.keys())
        placeholders.update(_collect_required_service_placeholders(fixture.definition))

    allowed_extras = {"SUPERSET_ADMIN_PASSWORD"}
    missing = placeholders - available_env - allowed_extras
    assert not missing


def test_core_service_host_port_defaults_are_package_owned() -> None:
    """Verify default-stack host ports live with the package service definitions."""
    expected = {
        "postgres": {"POSTGRES_PORT": ("10000", "5432")},
        "minio": {
            "MINIO_API_PORT": ("10001", "9000"),
            "MINIO_CONSOLE_PORT": ("10002", "9001"),
        },
        "nessie": {"NESSIE_PORT": ("10003", "19120")},
        "trino": {"TRINO_PORT": ("10005", "8080")},
        "dagster": {"DAGSTER_PORT": ("10006", "3000")},
    }

    for service_name, env_specs in expected.items():
        definition = CORE_SERVICES[service_name].definition
        env_vars = definition.get("env_vars", {})
        ports = definition.get("compose", {}).get("ports", [])

        for env_name, (host_port, container_port) in env_specs.items():
            assert env_vars[env_name]["default"] == int(host_port)
            assert f"${{{env_name}:-{host_port}}}:{container_port}" in ports
