"""Shared initialization keeps portable configuration separate from host settings."""

import subprocess

import yaml

from phlo.plugins.compose.artifacts import render_shared_gitignore, write_compose_layers


def test_host_generation_preserves_shared_compose_on_second_checkout(tmp_path):
    state = tmp_path / ".phlo"
    state.mkdir()
    (state / ".gitignore").write_text(render_shared_gitignore([]))
    first = {
        "services": {
            "dagster": {
                "environment": {
                    "TEAM": "yes",
                    "PHLO_RUNTIME_UID": "1000",
                    "PHLO_RUNTIME_GID": "1000",
                }
            }
        }
    }
    write_compose_layers(state, yaml.safe_dump(first))
    shared = state / "docker-compose.yml"
    original = shared.read_bytes()
    second = {
        "services": {
            "dagster": {
                "environment": {
                    "TEAM": "yes",
                    "PHLO_RUNTIME_UID": "2000",
                    "PHLO_RUNTIME_GID": "2000",
                }
            }
        }
    }
    write_compose_layers(state, yaml.safe_dump(second), preserve_shared=True)
    assert shared.read_bytes() == original
    assert yaml.safe_load(shared.read_text())["services"]["dagster"]["environment"] == {
        "TEAM": "yes"
    }
    assert yaml.safe_load((state / "overrides/compose.host.yaml").read_text())["services"][
        "dagster"
    ]["environment"] == {
        "PHLO_RUNTIME_UID": "2000",
        "PHLO_RUNTIME_GID": "2000",
    }


def test_portable_base_excludes_dev_paths(tmp_path):
    state = tmp_path / ".phlo"
    state.mkdir()
    (state / ".gitignore").write_text(render_shared_gitignore([]))
    portable = {"services": {"demo": {"image": "example:1", "build": {"context": "."}}}}
    local = {
        "services": {
            "demo": {
                "build": {"context": "."},
                "volumes": ["../../local-source:/opt/phlo-dev:rw"],
                "environment": {"PHLO_DEV_MODE": "true"},
            }
        }
    }
    write_compose_layers(state, yaml.safe_dump(local), portable_rendered=yaml.safe_dump(portable))
    assert yaml.safe_load((state / "docker-compose.yml").read_text()) == portable
    host = yaml.safe_load((state / "overrides/compose.host.yaml").read_text())["services"]["demo"]
    assert host["image"] == ""
    assert host["volumes"] == ["../../local-source:/opt/phlo-dev:rw"]


def test_regenerating_ignore_rules_retains_custom_artifacts(tmp_path):
    state = tmp_path / ".phlo"
    state.mkdir()
    original = render_shared_gitignore(["custom/config.yaml"])
    regenerated = render_shared_gitignore(["dagster/Dockerfile"], original)
    (state / ".gitignore").write_text(regenerated)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for path in [".phlo/custom/config.yaml", ".phlo/dagster/Dockerfile"]:
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=tmp_path).returncode == 1
    for path in [".phlo/secrets/.env", ".phlo/custom/private.env", ".phlo/overrides/compose.yaml"]:
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=tmp_path).returncode == 0
