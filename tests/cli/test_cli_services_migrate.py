"""Migration moves private state while keeping shared artifacts in .phlo."""

import subprocess

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.commands.services.migrate import migrate_cmd
from tests.helpers import FakeDiscovery, _service


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_REGULATED", "false")
    monkeypatch.setenv("PHLO_ENVIRONMENT", "development")
    state = tmp_path / ".phlo"
    state.mkdir()
    (tmp_path / ".gitignore").write_text(".phlo/\nkeep-ignored\n")
    (state / ".gitignore").write_text("custom-private\n")
    (state / ".env").write_text("TEAM=yes\n")
    (state / ".env.local").write_text("PASSWORD=private\n")
    (state / "docker-compose.yml").write_text(
        "services:\n  demo:\n    image: alpine:3\n    env_file: [.env, .env.local]\n"
        "    environment:\n      PASSWORD: ${PASSWORD}\n"
    )
    (state / "demo").mkdir()
    (state / "demo/Dockerfile").write_text("FROM alpine:3\n# custom\n")
    service = _service("demo")
    service.files = [{"source": "Dockerfile", "dest": "demo/Dockerfile"}]
    discovery = FakeDiscovery({"demo": service})
    monkeypatch.setattr("phlo.cli.commands.services.migrate.ServiceDiscovery", lambda: discovery)
    return tmp_path, state, discovery


def test_dry_run_then_moves_and_git_exclusions(project):
    root, state, _ = project
    original = (state / "docker-compose.yml").read_bytes()
    result = CliRunner().invoke(migrate_cmd, ["--dry-run"])
    assert result.exit_code == 0, result.output
    assert (state / "docker-compose.yml").read_bytes() == original
    assert (state / ".env.local").exists()
    assert not (state / "secrets").exists()
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    assert "PASSWORD=private" not in result.output
    assert not (state / ".env.local").exists()
    assert not (state / ".env").exists()
    assert (state / "secrets/.env").read_text() == "PASSWORD=private\n"
    assert (state / "overrides/.env").read_text() == "TEAM=yes\n"
    assert not (root / "compose.phlo.yaml").exists()
    assert not (root / "phlo-runtime").exists()
    assert yaml.safe_load((state / "docker-compose.yml").read_text())["services"]["demo"][
        "env_file"
    ] == ["overrides/.env", "secrets/.env"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for path in [
        ".phlo/secrets/.env",
        ".phlo/overrides/.env",
        ".phlo/volumes/data",
        ".phlo/demo/unselected.conf",
        ".phlo/custom-private",
        "keep-ignored",
    ]:
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=root).returncode == 0
    for path in [".phlo/docker-compose.yml", ".phlo/demo/Dockerfile", ".phlo/.gitignore"]:
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=root).returncode == 1


def test_existing_export_files_move_back_and_identical_artifacts_deduplicate(project):
    root, state, _ = project
    (root / "compose.phlo.yaml").write_text("services: {demo: {mem_limit: 1g}}\n")
    legacy = root / "phlo-runtime/demo"
    legacy.mkdir(parents=True)
    (legacy / "Dockerfile").write_bytes((state / "demo/Dockerfile").read_bytes())
    (state / "compose.local.yaml").write_text("services: {demo: {cpus: 2}}\n")
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    assert not (root / "phlo-runtime").exists()
    assert not (root / "compose.phlo.yaml").exists()
    assert not (state / "compose.local.yaml").exists()
    assert (state / "compose.shared.yaml").exists()
    assert (state / "overrides/compose.yaml").exists()


def test_conflicting_destination_leaves_everything_unchanged(project):
    root, state, _ = project
    (state / "secrets").mkdir()
    (state / "secrets/.env").write_text("different")
    original = (state / "docker-compose.yml").read_bytes()
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "Conflicting destination" in result.output
    assert (state / ".env").exists()
    assert (state / ".env.local").exists()
    assert (state / "secrets/.env").read_text() == "different"
    assert (state / "docker-compose.yml").read_bytes() == original
    assert (root / ".gitignore").read_text() == ".phlo/\nkeep-ignored\n"


@pytest.mark.parametrize(
    "include", [".env.local", "../outside", "volumes/data", "docker-compose.yml"]
)
def test_private_and_escaping_includes_fail_without_mutation(project, include):
    _, state, _ = project
    (state / "volumes").mkdir()
    (state / "volumes/data").write_text("private")
    result = CliRunner().invoke(migrate_cmd, ["--include", include])
    assert result.exit_code != 0
    assert (state / ".env.local").exists()
    assert not (state / "secrets").exists()


def test_explicit_artifact_stays_in_place_and_is_shared(project):
    root, state, _ = project
    (state / "custom.conf").write_text("setting=custom\n")
    result = CliRunner().invoke(migrate_cmd, ["--include", "custom.conf"])
    assert result.exit_code == 0, result.output
    assert (state / "custom.conf").read_text() == "setting=custom\n"
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert (
        subprocess.run(["git", "check-ignore", "-q", ".phlo/custom.conf"], cwd=root).returncode == 1
    )


def test_symlink_private_destination_rejected(project):
    root, state, _ = project
    (state / "secrets").symlink_to(root, target_is_directory=True)
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "Symlink" in result.output
    assert (state / ".env.local").exists()


@pytest.mark.parametrize(
    "environment",
    [
        {"PHLO_RUNTIME_UID": "1000", "PHLO_RUNTIME_GID": "1000", "TEAM": "yes"},
        ["PHLO_RUNTIME_UID=1000", "PHLO_RUNTIME_GID=1000", "TEAM=yes"],
    ],
)
def test_host_identity_moves_to_ignored_overlay(project, environment):
    _, state, _ = project
    (state / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"dagster": {"image": "example", "environment": environment}}})
    )
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    shared = yaml.safe_load((state / "docker-compose.yml").read_text())
    local = yaml.safe_load((state / "overrides/compose.host.yaml").read_text())
    shared_env = shared["services"]["dagster"]["environment"]
    if isinstance(shared_env, list):
        shared_env = dict(item.split("=", 1) for item in shared_env)
    assert shared_env == {"TEAM": "yes"}
    assert local["services"]["dagster"]["environment"] == {
        "PHLO_RUNTIME_UID": "1000",
        "PHLO_RUNTIME_GID": "1000",
    }


def test_write_failure_rolls_back_moved_files(project, monkeypatch):
    from pathlib import Path

    root, state, _ = project
    original = (state / "docker-compose.yml").read_bytes()
    write_bytes = Path.write_bytes
    failed = False

    def fail_once(path, content):
        nonlocal failed
        if path == root / ".gitignore" and not failed:
            failed = True
            raise OSError("disk failure")
        return write_bytes(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_once)
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code != 0
    assert "original files restored" in result.output
    assert (state / ".env.local").read_text() == "PASSWORD=private\n"
    assert (state / ".env").exists()
    assert not (state / "secrets").exists()
    assert (state / "docker-compose.yml").read_bytes() == original


def test_dev_mount_moves_to_personal_overlay(project):
    _, state, _ = project
    (state / "docker-compose.yml").write_text(
        "# Dev mode: true\n"
        + yaml.safe_dump(
            {
                "services": {
                    "demo": {
                        "build": {"context": "."},
                        "environment": {"PHLO_DEV_MODE": "true", "TEAM": "yes"},
                        "volumes": ["../:/app", "../../local-phlo:/opt/phlo-dev:rw"],
                    }
                }
            }
        )
    )
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    shared = yaml.safe_load((state / "docker-compose.yml").read_text())["services"]["demo"]
    local = yaml.safe_load((state / "overrides/compose.host.yaml").read_text())["services"]["demo"]
    assert shared["volumes"] == ["../:/app"]
    assert shared["environment"] == {"TEAM": "yes"}
    assert local["volumes"] == ["../../local-phlo:/opt/phlo-dev:rw"]
    assert local["environment"] == {"PHLO_DEV_MODE": "true"}


def test_second_migration_keeps_explicit_shared_artifact(project):
    root, state, _ = project
    (state / "custom.conf").write_text("custom=true\n")
    result = CliRunner().invoke(migrate_cmd, ["--include", "custom.conf"])
    assert result.exit_code == 0, result.output
    original = (state / ".gitignore").read_bytes()
    result = CliRunner().invoke(migrate_cmd)
    assert result.exit_code == 0, result.output
    assert (state / ".gitignore").read_bytes() == original
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert (
        subprocess.run(["git", "check-ignore", "-q", ".phlo/custom.conf"], cwd=root).returncode == 1
    )
