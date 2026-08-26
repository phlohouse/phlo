"""Tests service URL resolution for the Observatory API.

Project .phlo/.env.local port overrides must win over built-in defaults and
environment variables, with DNS lookups forced to fail so resolution cannot
depend on host reachability.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from phlo.plugins.compose import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition
from phlo_api.observatory_api import dagster, loki, nessie, quality, trino
from phlo_api.observatory_api.observatory_models import ObservatoryExternalLink
from phlo_api.observatory_api.observatory_models import ObservatoryHealth
from phlo_api.observatory_api.observatory_services import (
    merge_service_links,
    native_service_override,
    resolve_env_default,
    service_links_from_compose,
)


def _write_project_env(tmp_path, content: str) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text(content)


def _raise_unresolvable(_host: str) -> str:
    raise socket.gaierror()


@pytest.fixture(autouse=True)
def _scrub_ambient_service_ports(monkeypatch):
    for port_key in ("DAGSTER_PORT", "NESSIE_PORT", "LOKI_PORT", "TRINO_PORT"):
        monkeypatch.delenv(port_key, raising=False)


def test_observatory_api_urls_use_project_port_overrides(tmp_path, monkeypatch) -> None:
    _write_project_env(
        tmp_path,
        "\n".join(
            [
                "DAGSTER_PORT=3300",
                "NESSIE_PORT=29120",
                "LOKI_PORT=13100",
            ]
        ),
    )
    monkeypatch.chdir(tmp_path)
    for key in ("DAGSTER_GRAPHQL_URL", "NESSIE_URL", "LOKI_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", _raise_unresolvable)

    assert dagster.resolve_dagster_url() == "http://localhost:3300/graphql"
    assert quality.resolve_dagster_url() == "http://localhost:3300/graphql"
    assert nessie.resolve_nessie_url() == "http://localhost:29120/api/v2"
    assert loki.resolve_loki_url() == "http://localhost:13100"


def test_resolve_trino_url_uses_project_port_for_capability_metadata(tmp_path, monkeypatch) -> None:
    _write_project_env(tmp_path, "TRINO_PORT=18080\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHLO_QUERY_ENGINE_URL", raising=False)
    monkeypatch.delenv("TRINO_URL", raising=False)
    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", _raise_unresolvable)

    with (
        patch("phlo_api.observatory_api.trino.discover_capabilities"),
        patch(
            "phlo_api.observatory_api.trino.resolve_capability",
            return_value=Mock(metadata={"host": "trino", "port": 8080}),
        ),
    ):
        assert trino.resolve_trino_url() == "http://localhost:18080"


def test_service_links_resolve_project_env_port_overrides(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("TRINO_PORT=18080\n")
    (phlo_dir / "docker-compose.yml").write_text(
        """
services:
  trino:
    ports:
      - "${TRINO_PORT:-10005}:8080"
"""
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHLO_PROJECT_PATH", raising=False)

    assert resolve_env_default("${TRINO_PORT:-10005}") == "18080"
    assert service_links_from_compose(tmp_path, "trino") == [
        ObservatoryExternalLink(
            label=":18080",
            url="http://localhost:18080",
            kind="port",
        )
    ]


def test_native_service_override_uses_running_local_port(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_API_PORT", "4000")
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory_services._local_port_status",
        lambda port: (
            (
                "running",
                ObservatoryHealth(state="ok", message=f"Listening on localhost:{port}"),
            )
            if port == "4000"
            else None
        ),
    )

    status, health, port = native_service_override(
        "phlo-api",
        "unknown",
        ObservatoryHealth(state="unknown", message="Runtime status unavailable"),
    )

    assert port == "4000"
    assert status == "running"
    assert health.state == "ok"


def test_merge_service_links_prefers_native_port() -> None:
    links = merge_service_links(
        [ObservatoryExternalLink(label=":3000", url="http://localhost:3000", kind="port")],
        [ObservatoryExternalLink(label=":3001", url="http://localhost:3001", kind="port")],
    )

    assert links[0].url == "http://localhost:3000"


def test_api_profile_uses_the_shared_run_evidence_store() -> None:
    service = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "src" / "phlo_api" / "service.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert service["profile"] == "api"
    evidence_db_url = service["compose"]["environment"]["PHLO_RUN_EVIDENCE_DB_URL"]
    assert evidence_db_url.startswith("postgresql://")
    assert "@postgres:5432/" in evidence_db_url
    assert "postgres" in service["depends_on"]


def test_non_dev_api_profile_compose_is_reachable_without_dev_mounts(tmp_path) -> None:
    api = ServiceDefinition.from_yaml(
        Path(__file__).resolve().parents[1] / "src" / "phlo_api" / "service.yaml"
    )
    postgres = ServiceDefinition(name="postgres", description="postgres")
    dagster = ServiceDefinition(name="dagster", description="dagster")

    class Discovery:
        def resolve_dependencies(self, services):
            return services

        def get_service(self, name):
            return {"postgres": postgres, "dagster": dagster, "phlo-api": api}.get(name)

    compose = yaml.safe_load(
        ComposeGenerator(Discovery()).generate_compose(
            [postgres, dagster, api], output_dir=tmp_path, dev_mode=False
        )
    )
    service = compose["services"]["phlo-api"]

    assert service["profiles"] == ["api"]

    build = service["build"]
    assert build["context"] == "."
    assert build["dockerfile"] == "phlo-api/Dockerfile"
    for arg in ("PHLO_VERSION", "PHLO_API_VERSION", "PHLO_WHEELHOUSE"):
        assert build["args"].get(arg) == f"${{{arg}:-}}"

    assert "${PHLO_API_PORT:-4000}:4000" in service["ports"]
    assert service["environment"]["PHLO_RUN_EVIDENCE_DB_URL"].endswith(
        "@postgres:5432/${POSTGRES_DB:-phlo}"
    )
    assert "postgres" in service["depends_on"]
    assert "PHLO_DEV_MODE" not in service["environment"]
    volumes = service["volumes"]
    mount_targets = {volume.split(":")[1] for volume in volumes}
    assert mount_targets >= {
        "/app",
        "/app/.phlo/logs",
        "/app/.phlo/observatory",
        "/app/.phlo/state",
    }
    app_mount = next(volume for volume in volumes if volume.split(":")[1] == "/app")
    assert app_mount.endswith(":ro")
    assert not any(volume.endswith(":rw") for volume in volumes)
