"""Sharing migration preserves custom artifacts without exporting runtime state."""

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.commands.services.migrate import migrate_cmd
from phlo.plugins.compose.generator import ComposeGenerator
from tests.helpers import FakeDiscovery, _service


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_REGULATED", "false")
    monkeypatch.setenv("PHLO_ENVIRONMENT", "development")
    state = tmp_path / ".phlo"
    state.mkdir()
    (state / ".env").write_text("")
    (state / ".env.local").write_text("PASSWORD=private\n")
    (state / "docker-compose.yml").write_text(
        "services:\n  demo:\n    image: alpine:3\n    environment:\n      PASSWORD: ${PASSWORD}\n"
    )
    (state / "demo").mkdir()
    (state / "demo/Dockerfile").write_text("FROM alpine:3\n# custom\n")
    service = _service("demo")
    service.files = [{"source": "Dockerfile", "dest": "demo/Dockerfile"}]
    discovery = FakeDiscovery({"demo": service})
    monkeypatch.setattr("phlo.cli.commands.services.migrate.ServiceDiscovery", lambda: discovery)
    return tmp_path, state, discovery


def test_dry_run_and_export_restore_shared_artifacts(project):
    root, state, discovery = project
    original = (state / "docker-compose.yml").read_bytes()
    result = CliRunner().invoke(migrate_cmd, ["--dry-run"])
    assert result.exit_code == 0, result.output
    assert not (root / "compose.phlo.yaml").exists()
    assert not (root / "phlo-runtime").exists()
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    assert (state / "docker-compose.yml").read_bytes() == original
    assert not (root / "phlo-runtime/.env.local").exists()
    assert (
        yaml.safe_load((root / "compose.phlo.yaml").read_text())["services"]["demo"]["image"]
        == "alpine:3"
    )
    shared = root / "phlo-runtime/demo/Dockerfile"
    shared.write_text("FROM alpine:3\n# team edit\n")
    ComposeGenerator(discovery).copy_service_files([], state)
    assert (state / "demo/Dockerfile").read_bytes() == shared.read_bytes()
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output


@pytest.mark.parametrize(
    "include", [".env.local", "../outside", "volumes/data", "docker-compose.yml"]
)
def test_private_and_escaping_includes_fail_without_output(project, include):
    root, state, _ = project
    (state / "volumes").mkdir()
    (state / "volumes/data").write_text("private")
    result = CliRunner().invoke(migrate_cmd, ["--include", include])
    assert result.exit_code != 0
    assert not (root / "compose.phlo.yaml").exists()
    assert not (root / "phlo-runtime").exists()


def test_inline_secret_rejected_without_revealing_value(project):
    root, state, _ = project
    path = state / "docker-compose.yml"
    path.write_text(path.read_text().replace("${PASSWORD}", "do-not-export-me"))
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "Inline credential" in result.output
    assert "do-not-export-me" not in result.output
    assert not (root / "compose.phlo.yaml").exists()


def test_explicit_artifact_is_shared_and_restored(project):
    root, state, discovery = project
    (state / "custom.conf").write_text("setting=custom\n")
    result = CliRunner().invoke(migrate_cmd, ["--include", "custom.conf"])
    assert result.exit_code == 0, result.output
    (state / "custom.conf").unlink()
    ComposeGenerator(discovery).copy_service_files([], state)
    assert (state / "custom.conf").read_text() == "setting=custom\n"


def test_migration_rejects_production(project):
    root, state, _ = project
    (state / ".env").write_text("PHLO_ENVIRONMENT=production\n")
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "only for development" in result.output
    assert not (root / "compose.phlo.yaml").exists()


def test_symlink_artifact_rejected(project):
    root, state, _ = project
    (state / "linked").symlink_to(state / ".env.local")
    result = CliRunner().invoke(migrate_cmd, ["--include", "linked"])
    assert result.exit_code != 0
    assert "Symlink" in result.output
    assert not (root / "compose.phlo.yaml").exists()


@pytest.mark.parametrize(
    "environment",
    [
        {"PHLO_RUNTIME_UID": "1000", "PHLO_RUNTIME_GID": "1000", "TEAM": "yes"},
        ["PHLO_RUNTIME_UID=1000", "PHLO_RUNTIME_GID=1000", "TEAM=yes"],
    ],
)
def test_migration_leaves_runtime_identity_to_generating_host(project, environment):
    root, state, _ = project
    (state / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"dagster": {"image": "example", "environment": environment}}})
    )
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    content = (root / "compose.phlo.yaml").read_text()
    assert "PHLO_RUNTIME_UID" not in content
    assert "PHLO_RUNTIME_GID" not in content
    assert "TEAM" in content


def test_generated_compose_cannot_be_replayed_as_artifact(project):
    root, state, discovery = project
    shared = root / "phlo-runtime"
    shared.mkdir()
    (shared / "docker-compose.yml").write_text("services: {}")
    original = (state / "docker-compose.yml").read_bytes()
    with pytest.raises(ValueError, match="Private/runtime"):
        ComposeGenerator(discovery).copy_service_files([], state)
    assert (state / "docker-compose.yml").read_bytes() == original


def test_service_without_declared_files_can_migrate(project):
    root, _, discovery = project
    discovery.get_service("demo").files = None
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    assert (root / "compose.phlo.yaml").exists()
