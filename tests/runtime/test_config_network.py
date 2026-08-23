"""Tests for project env file precedence (.phlo/.env.local over .env
over phlo.yaml and the OS environment) and host/URL resolution."""

from __future__ import annotations

import socket

from phlo.config.env import (
    load_project_env,
    parse_project_config_env,
    parse_project_env_file,
    project_env_value,
)
from phlo.config.network import resolve_host, resolve_url


def test_project_env_files_load_with_local_and_os_precedence(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (tmp_path / "phlo.yaml").write_text(
        "env:\n  TRINO_PORT: 7777\n  CLIENT_EXPORTS_OUTPUT: data/exports\n"
    )
    (phlo_dir / ".env").write_text("TRINO_PORT=8080\nPOSTGRES_DB=phlo\n")
    (phlo_dir / ".env.local").write_text("TRINO_PORT=18080\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_DB", "override")

    env = load_project_env()

    assert env["TRINO_PORT"] == "18080"
    assert env["POSTGRES_DB"] == "override"
    assert env["CLIENT_EXPORTS_OUTPUT"] == "data/exports"
    assert project_env_value("TRINO_PORT") == "18080"


def test_parse_project_config_env_reads_phlo_yaml_env(tmp_path) -> None:
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("env:\n  STRING_VALUE: hello\n  NUMBER_VALUE: 42\n")

    assert parse_project_config_env(config_path) == {
        "STRING_VALUE": "hello",
        "NUMBER_VALUE": "42",
    }


def test_resolve_url_uses_project_env_port_for_unresolvable_service(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("TRINO_PORT=18080\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRINO_PORT", raising=False)

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    assert resolve_url("http://trino:8080/v1/info", port_env_var="TRINO_PORT") == (
        "http://localhost:18080/v1/info"
    )


def test_resolve_url_falls_back_when_project_env_port_is_invalid(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("TRINO_PORT=not-a-port\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRINO_PORT", raising=False)

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    assert resolve_url("http://trino:8080/v1/info", port_env_var="TRINO_PORT") == (
        "http://localhost:8080/v1/info"
    )
    assert resolve_host("trino", 8080, port_env_var="TRINO_PORT") == ("localhost", 8080)


def test_project_env_parser_only_strips_balanced_quotes(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BALANCED=\"hello\"\nSINGLE='world'\nMISMATCHED=\"hello'\n")

    values = parse_project_env_file(env_file)

    assert values["BALANCED"] == "hello"
    assert values["SINGLE"] == "world"
    assert values["MISMATCHED"] == "\"hello'"
