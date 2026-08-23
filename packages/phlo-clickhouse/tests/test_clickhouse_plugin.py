"""Tests for the ClickHouse service plugin.

Covers service definition metadata, pinned image digests, and CLI guards that
reject commands without an initialized .phlo directory or authorized SQL.
"""

from click.testing import CliRunner
from subprocess import CompletedProcess

from phlo_clickhouse.cli import clickhouse_group
from phlo_clickhouse.plugin import ClickHouseServicePlugin


def test_clickhouse_service_definition():
    """Validate ClickHouse service definition fields."""

    plugin = ClickHouseServicePlugin()
    service_definition = plugin.service_definition

    assert service_definition["name"] == "clickhouse"
    assert service_definition["category"] == "data"
    assert "clickhouse-data:/var/lib/clickhouse" in service_definition["compose"]["volumes"]
    assert "clickhouse-logs:/var/log/clickhouse-server" in service_definition["compose"]["volumes"]


def test_clickhouse_service_pins_generated_image_digest() -> None:
    """The generated environment defaults to an immutable image reference."""
    service_definition = ClickHouseServicePlugin().service_definition

    assert service_definition["image"] == (
        "${CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:26.5.6.64-alpine@"
        "sha256:446c9d82443b926a5aacb952448dd632672606acc691ce1b3c2292b68a1197c2}"
    )
    assert service_definition["env_vars"]["CLICKHOUSE_IMAGE"]["default"] == (
        "clickhouse/clickhouse-server:26.5.6.64-alpine@"
        "sha256:446c9d82443b926a5aacb952448dd632672606acc691ce1b3c2292b68a1197c2"
    )


def test_clickhouse_service_metadata():
    """Validate ClickHouse service plugin metadata."""

    plugin = ClickHouseServicePlugin()
    metadata = plugin.metadata

    assert metadata.name == "clickhouse"
    assert metadata.version == "0.1.0"
    assert "data" in metadata.tags
    assert "query" in metadata.tags
    assert "storage" in metadata.tags


def test_clickhouse_query_rejects_missing_input(monkeypatch):
    """Query command should fail with the shared SQL input contract."""
    monkeypatch.setattr("phlo_clickhouse.cli._ensure_phlo_dir", lambda: None)
    monkeypatch.setattr("phlo_clickhouse.cli._require_container_backend", lambda: None)
    monkeypatch.setattr("phlo_clickhouse.cli.get_project_name", lambda: "demo")

    result = CliRunner().invoke(clickhouse_group, ["query"])

    assert result.exit_code != 0
    assert "Error: no SQL query provided" in result.output
    assert "Provide an inline query argument or pass --file." in result.output
    assert 'Run: phlo clickhouse query "SELECT 1"' in result.output


def test_clickhouse_query_rejects_partial_phlo_directory(monkeypatch, tmp_path):
    """Logging-created .phlo directories are not initialized service projects."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo" / "logs").mkdir(parents=True)
    monkeypatch.setattr("phlo_clickhouse.cli._require_container_backend", lambda: None)

    result = CliRunner().invoke(clickhouse_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output


def test_clickhouse_status_rejects_partial_phlo_directory(monkeypatch, tmp_path):
    """Status should preflight the local project before running compose."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo" / "logs").mkdir(parents=True)
    monkeypatch.setattr("phlo_clickhouse.cli._require_container_backend", lambda: None)

    result = CliRunner().invoke(clickhouse_group, ["status"])

    assert result.exit_code != 0
    assert "Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output


def test_clickhouse_query_authorizes_only_mutating_sql(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr("phlo_clickhouse.cli._ensure_phlo_dir", lambda: None)
    monkeypatch.setattr(
        "phlo_clickhouse.cli._require_container_backend",
        lambda: calls.append("backend"),
    )
    monkeypatch.setattr("phlo_clickhouse.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickhouse.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_clickhouse.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="ok\n", stderr=""),
    )
    monkeypatch.setattr(
        "phlo_clickhouse.cli.enforce_surface_mutation_authorization",
        lambda *_args, **_kwargs: calls.append("auth"),
    )

    select_result = CliRunner().invoke(clickhouse_group, ["query", "SELECT 1"])
    insert_result = CliRunner().invoke(clickhouse_group, ["query", "INSERT INTO t VALUES (1)"])

    assert select_result.exit_code == 0
    assert insert_result.exit_code == 0
    assert calls == ["backend", "auth", "backend"]
