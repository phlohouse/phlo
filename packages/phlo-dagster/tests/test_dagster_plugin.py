"""Tests for the Dagster service plugin.

Checks the exposed service metadata and asserts the packaged service
definitions (service.yaml, dagster-daemon.yaml) structurally: project
discovery mounts, container policy, and run-evidence database wiring.
"""

from importlib import resources
from typing import Any

import yaml

from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDiscovery
from phlo_dagster.plugin import DagsterCodeServicePlugin, DagsterServicePlugin


def _load_packaged_yaml(filename: str) -> dict[str, Any]:
    raw = resources.files("phlo_dagster").joinpath(filename).read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), f"{filename} must parse to a mapping"
    return parsed


def test_dagster_service_definition():
    """Verify the Dagster plugin exposes expected service metadata."""
    plugin = DagsterServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "dagster"
    assert service_definition["category"] == "orchestration"
    assert "profile" not in service_definition


def test_webserver_compose_discovers_the_host_project() -> None:
    compose = _load_packaged_yaml("service.yaml")["compose"]

    environment = compose["environment"]
    assert environment["PHLO_WORKFLOWS_PATH"] == "/app/workflows"
    assert "DBT_PROJECT_DIR" not in environment
    assert "../:/app" in compose["volumes"]


def test_webserver_compose_pins_container_policy() -> None:
    compose = _load_packaged_yaml("service.yaml")["compose"]

    assert compose["restart"] == "unless-stopped"

    (port_mapping,) = compose["ports"]
    host_ref, _, container_port = port_mapping.rpartition(":")
    assert container_port == "3000"
    assert "DAGSTER_PORT" in host_ref

    healthcheck = compose["healthcheck"]
    assert {"test", "interval", "timeout", "retries", "start_period"} <= healthcheck.keys()


def test_daemon_shares_the_webserver_project_discovery_contract() -> None:
    compose = _load_packaged_yaml("dagster-daemon.yaml")["compose"]

    environment = compose["environment"]
    assert environment["PHLO_WORKFLOWS_PATH"] == "/app/workflows"
    assert "DBT_PROJECT_DIR" not in environment
    assert "../:/app" in compose["volumes"]
    assert compose["restart"] == "unless-stopped"


def test_webserver_and_daemon_share_the_postgres_run_evidence_store() -> None:
    for filename in ("service.yaml", "dagster-daemon.yaml", "dagster-code.yaml"):
        definition = _load_packaged_yaml(filename)

        build_args = definition["build"]["args"]
        assert "PHLO_DBT_VERSION" in build_args, filename

        environment = definition["compose"]["environment"]
        assert "PHLO_RUN_EVIDENCE_DB_URL" in environment, filename

        dsn = environment["PHLO_RUN_EVIDENCE_DB_URL"]
        assert isinstance(dsn, str), filename
        assert dsn.startswith("postgresql://"), filename
        assert "postgres:5432" in dsn, filename
        for placeholder in ("${POSTGRES_USER", "${POSTGRES_PASSWORD", "${POSTGRES_DB"):
            assert placeholder in dsn, f"{filename}: {placeholder} missing from DSN"


def test_generated_code_service_is_private_and_uses_the_orchestration_identity(tmp_path):
    discovery = ServiceDiscovery()
    discovery.discover()
    service = discovery.get_service("dagster")
    assert service is not None
    document = yaml.safe_load(
        ComposeGenerator(discovery, host_identity=(1234, 2345)).generate_compose(
            [
                service,
                discovery.get_service("dagster-daemon"),
                discovery.get_service("dagster-code"),
            ],
            output_dir=tmp_path,
        )
    )
    code = document["services"]["dagster-code"]
    assert DagsterCodeServicePlugin().metadata.name == "dagster-code"
    assert "ports" not in code
    assert document["networks"]["dagster-code"]["internal"] is True
    assert code["networks"]["dagster-code"]["aliases"] == ["dagster-code-private"]
    assert code["command"][code["command"].index("-h") + 1] == "dagster-code-private"
    assert code["environment"]["PHLO_RUNTIME_UID"] == "1234"
    assert code["environment"]["POSTGRES_USER"].startswith("${DAGSTER_POSTGRES_USER")
    assert code["environment"]["TRINO_USER"] == "${DAGSTER_TRINO_USER:-phlo-dagster}"
    assert code["environment"]["AWS_ACCESS_KEY_ID"].startswith("${DAGSTER_MINIO_ACCESS_KEY")
    for name in ("dagster", "dagster-daemon"):
        assert "dagster-code" in document["services"][name]["networks"]
    assert (
        document["services"]["dagster"]["depends_on"]["dagster-code"]["condition"]
        == "service_healthy"
    )
