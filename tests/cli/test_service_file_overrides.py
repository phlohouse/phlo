"""Exercise service-file overrides through the shared generator and public CLI.

Real provider manifests verify ownership, default preservation, and shared Dagster
mounts. Invalid overrides must leave every previously generated file intact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.main import cli
from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

pytestmark = pytest.mark.core_regression


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _init() -> None:
    result = CliRunner().invoke(cli, ["services", "init", "--no-dev", "--force"])
    assert result.exit_code == 0, result.output


def _project(root: Path, overrides: dict) -> None:
    (root / "phlo.yaml").write_text(
        yaml.safe_dump({"name": "library", "infrastructure": {"services": overrides}}),
        encoding="utf-8",
    )


def test_public_cli_regenerates_shared_dagster_config_and_replaces_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "do-not-copy-this-secret")
    monkeypatch.delenv("QUEUE_LIMIT", raising=False)
    limits = [{"key": "workflow", "value": "library_catalogue", "limit": 1}]
    overlay = {
        "run_coordinator": {
            "config": {
                "tag_concurrency_limits": limits,
                "max_concurrent_runs": {"env": "QUEUE_LIMIT"},
            }
        },
        "sensors": {"num_workers": 7},
    }
    source = tmp_path / "dagster-instance.yaml"
    source.write_text(yaml.safe_dump(overlay), encoding="utf-8")
    _project(tmp_path, {"dagster": {"files": {"dagster/dagster.yaml": {"source": source.name}}}})
    _init()
    output = tmp_path / ".phlo"
    config = yaml.safe_load((output / "dagster/dagster.yaml").read_text(encoding="utf-8"))
    assert config["run_coordinator"] == {
        "module": "phlo_dagster.daemon_identity",
        "class": "PhloQueuedRunCoordinator",
        "config": {"tag_concurrency_limits": limits, "max_concurrent_runs": {"env": "QUEUE_LIMIT"}},
    }
    assert config["run_launcher"] == {
        "module": "dagster.core.launcher",
        "class": "DefaultRunLauncher",
    }
    assert config["storage"]["postgres"]["postgres_db"]["password"] == {"env": "POSTGRES_PASSWORD"}
    assert config["sensors"] == {"use_threads": True, "num_workers": 7}
    assert "do-not-copy-this-secret" not in json.dumps(config)
    compose = yaml.safe_load((output / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in ("dagster", "dagster-daemon"):
        assert "./dagster:/opt/dagster" in compose["services"][name]["volumes"]
        assert compose["services"][name]["environment"]["DAGSTER_HOME"] == "/opt/dagster"
    before = _snapshot(output)
    _init()
    assert _snapshot(output) == before
    overlay["run_coordinator"]["config"]["tag_concurrency_limits"] = [
        {"key": "workflow", "value": "visitor_count", "limit": 3}
    ]
    source.write_text(yaml.safe_dump(overlay), encoding="utf-8")
    # Shared-layout init without --force still reapplies explicit project overrides.
    result = CliRunner().invoke(cli, ["services", "init", "--no-dev"])
    assert result.exit_code == 0, result.output
    changed = yaml.safe_load((output / "dagster/dagster.yaml").read_text(encoding="utf-8"))
    assert changed["run_coordinator"]["config"]["tag_concurrency_limits"] == [
        {"key": "workflow", "value": "visitor_count", "limit": 3}
    ]
    # services add exercises the other generation path with the same canonical overrides.
    result = CliRunner().invoke(cli, ["services", "add", "alloy", "--no-start"])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load((output / "dagster/dagster.yaml").read_text(encoding="utf-8")) == changed


@pytest.mark.parametrize(
    ("overlay", "field"),
    [
        ("sensors:\n  num_workers: secret-invalid-value\n", "sensors.num_workers"),
        ("run_coordinator:\n  config:\n    typo: 1\n", "run_coordinator.config.typo"),
        (
            "run_coordinator:\n  config:\n    tag_concurrency_limits:\n      - key: workflow\n        limit: wrong\n",
            "tag_concurrency_limits[0].limit",
        ),
        ("run_coordinator:\n  class: QueuedRunCoordinator\n", "requires $replace"),
        ("run_coordinator: null\n", "requires $replace"),
        ("run_queue: {}\n", "incompatible with run_coordinator"),
        ("sensors: [broken\n", "invalid YAML/JSON syntax"),
    ],
)
def test_invalid_cli_overlay_preserves_all_generated_outputs(tmp_path, monkeypatch, overlay, field):
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, {})
    _init()
    before = _snapshot(tmp_path / ".phlo")
    (tmp_path / "instance.yaml").write_text(overlay, encoding="utf-8")
    _project(
        tmp_path, {"dagster": {"files": {"dagster/dagster.yaml": {"source": "instance.yaml"}}}}
    )
    result = CliRunner().invoke(cli, ["services", "init", "--force", "--no-dev"])
    assert result.exit_code == 1, result.output
    assert field in result.output
    assert "secret-invalid-value" not in result.output
    assert "Traceback" not in result.output
    assert _snapshot(tmp_path / ".phlo") == before
    result = CliRunner().invoke(cli, ["services", "add", "alloy", "--no-start"])
    assert result.exit_code == 1, result.output
    assert field in result.output
    assert _snapshot(tmp_path / ".phlo") == before


def test_public_cli_supports_text_replacement_and_directory_yaml_json_leaves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    replacements = {
        "trino.properties": "coordinator=true\nhttp-server.http.port=8080\ncustom.token=${ENV:TRINO_TOKEN}\n",
        "config.alloy": 'logging { level = sys.env("ALLOY_LOG_LEVEL") }\n',
        "datasources.yaml": "datasources:\n  - name: Project\n    type: prometheus\n    url: http://prometheus:9090\n",
        "dashboard.json": json.dumps({"title": "Project metrics", "panels": []}),
    }
    for name, content in replacements.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    _project(
        tmp_path,
        {
            "trino": {
                "files": {
                    "trino/config.properties": {"source": "trino.properties", "mode": "replace"}
                }
            },
            "alloy": {
                "enabled": True,
                "files": {"alloy/config.alloy": {"source": "config.alloy", "mode": "replace"}},
            },
            "grafana": {
                "enabled": True,
                "files": {
                    "grafana/provisioning/datasources/datasources.yml": {
                        "source": "datasources.yaml"
                    },
                    "grafana/dashboards/infrastructure.json": {"source": "dashboard.json"},
                },
            },
        },
    )
    _init()
    output = tmp_path / ".phlo"
    assert (output / "trino/config.properties").read_text(encoding="utf-8") == replacements[
        "trino.properties"
    ]
    assert (output / "alloy/config.alloy").read_text(encoding="utf-8") == replacements[
        "config.alloy"
    ]
    datasource = yaml.safe_load(
        (output / "grafana/provisioning/datasources/datasources.yml").read_text(encoding="utf-8")
    )
    assert datasource == {
        "apiVersion": 1,
        "datasources": [{"name": "Project", "type": "prometheus", "url": "http://prometheus:9090"}],
    }
    dashboard = json.loads(
        (output / "grafana/dashboards/infrastructure.json").read_text(encoding="utf-8")
    )
    assert dashboard["title"] == "Project metrics"
    assert dashboard["panels"] == []
    assert "uid" in dashboard
    before = _snapshot(output)
    _init()
    assert _snapshot(output) == before


def test_explicit_class_replacement_drops_old_config_only_at_that_node(tmp_path):
    discovery = ServiceDiscovery()
    dagster = discovery.get_service("dagster")
    assert dagster is not None
    (tmp_path / "instance.yaml").write_text(
        "run_coordinator:\n  $replace:\n    module: dagster\n    class: QueuedRunCoordinator\n"
        "    config:\n      max_concurrent_runs: 2\n",
        encoding="utf-8",
    )
    ComposeGenerator(discovery).copy_service_files(
        [dagster],
        tmp_path / ".phlo",
        user_overrides={
            "dagster": {"files": {"dagster/dagster.yaml": {"source": "instance.yaml"}}}
        },
    )
    config = yaml.safe_load((tmp_path / ".phlo/dagster/dagster.yaml").read_text(encoding="utf-8"))
    assert config["run_coordinator"] == {
        "module": "dagster",
        "class": "QueuedRunCoordinator",
        "config": {"max_concurrent_runs": 2},
    }
    assert config["storage"]["postgres"]["postgres_db"]["password"] == {"env": "POSTGRES_PASSWORD"}
    assert config["run_launcher"]["class"] == "DefaultRunLauncher"


@pytest.mark.parametrize(
    "destination",
    [
        "grafana/provisioning",
        "grafana/provisioning/new.yaml",
        "../escape.yaml",
        "/absolute.yaml",
        "grafana/../escape.yaml",
        "grafana\\escape.yaml",
    ],
)
def test_only_existing_bundled_directory_leaves_are_targets(tmp_path, destination):
    discovery = ServiceDiscovery()
    grafana = discovery.get_service("grafana")
    assert grafana is not None
    composer = ComposeGenerator(discovery)
    composer.copy_service_files([grafana], tmp_path / ".phlo")
    before = _snapshot(tmp_path / ".phlo")
    (tmp_path / "overlay.yaml").write_text("apiVersion: 2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        composer.copy_service_files(
            [grafana],
            tmp_path / ".phlo",
            user_overrides={
                "grafana": {"files": {destination: {"source": "overlay.yaml", "mode": "replace"}}}
            },
        )
    assert _snapshot(tmp_path / ".phlo") == before


def test_generator_preflights_all_services_and_rejects_symlink_escapes(tmp_path):
    discovery = ServiceDiscovery()
    services = [discovery.get_service(name) for name in ("dagster", "grafana")]
    assert all(service is not None for service in services)
    composer = ComposeGenerator(discovery)
    composer.copy_service_files(services, tmp_path / ".phlo")
    before = _snapshot(tmp_path / ".phlo")
    (tmp_path / "valid.yaml").write_text("sensors:\n  num_workers: 9\n", encoding="utf-8")
    (tmp_path / "broken.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML/JSON syntax"):
        composer.copy_service_files(
            services,
            tmp_path / ".phlo",
            user_overrides={
                "dagster": {"files": {"dagster/dagster.yaml": {"source": "valid.yaml"}}},
                "grafana": {
                    "files": {"grafana/dashboards/infrastructure.json": {"source": "broken.json"}}
                },
            },
        )
    assert _snapshot(tmp_path / ".phlo") == before
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".phlo").mkdir()
    (project / ".phlo/grafana").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes output directory"):
        composer.copy_service_files([services[1]], project / ".phlo")
    assert list(outside.iterdir()) == []
    (outside / "instance.yaml").write_text("sensors:\n  num_workers: 9\n", encoding="utf-8")
    (project / "instance.yaml").symlink_to(outside / "instance.yaml")
    with pytest.raises(ValueError, match="within the project root"):
        composer.copy_service_files(
            [services[0]],
            project / ".phlo",
            user_overrides={
                "dagster": {"files": {"dagster/dagster.yaml": {"source": "instance.yaml"}}}
            },
        )


def test_no_overrides_preserve_bundled_files_across_every_provider(tmp_path):
    discovery = ServiceDiscovery()
    services = list(discovery.discover().values())
    composer = ComposeGenerator(discovery)
    copied = composer.copy_service_files(services, tmp_path / ".phlo")
    expected = {}
    for service in services:
        for spec in service.files or []:
            source = service.source_path / spec["source"]
            if source.is_dir():
                expected.update(
                    {
                        (
                            Path(spec["dest"]) / leaf.relative_to(source)
                        ).as_posix(): leaf.read_bytes()
                        for leaf in source.rglob("*")
                        if leaf.is_file()
                    }
                )
            else:
                expected[spec["dest"]] = source.read_bytes()
    assert expected
    assert _snapshot(tmp_path / ".phlo") == expected
    assert set(copied) == set(expected)
    composer.copy_service_files(services, tmp_path / ".phlo")
    assert _snapshot(tmp_path / ".phlo") == expected

    overrides = {}
    for service in services:
        files = {}
        for spec in service.files or []:
            source = service.source_path / spec["source"]
            leaves = list(source.rglob("*")) if source.is_dir() else [source]
            for leaf in leaves:
                if not leaf.is_file():
                    continue
                destination = (
                    (Path(spec["dest"]) / leaf.relative_to(source)).as_posix()
                    if source.is_dir()
                    else spec["dest"]
                )
                project_source = tmp_path / "sources" / destination
                project_source.parent.mkdir(parents=True, exist_ok=True)
                project_source.write_bytes(leaf.read_bytes())
                files[destination] = {
                    "source": project_source.relative_to(tmp_path).as_posix(),
                    "mode": "replace",
                }
        overrides[service.name] = {"files": files}
    composer.copy_service_files(services, tmp_path / ".phlo", user_overrides=overrides)
    assert _snapshot(tmp_path / ".phlo") == expected


def test_preserved_directory_defaults_stay_untouched_but_explicit_overlay_regenerates(tmp_path):
    discovery = ServiceDiscovery()
    grafana = discovery.get_service("grafana")
    assert grafana is not None
    composer = ComposeGenerator(discovery)
    output = tmp_path / ".phlo"
    composer.copy_service_files([grafana], output)
    (output / "grafana/dashboards/infrastructure.json").unlink()
    (tmp_path / "overlay.yaml").write_text("apiVersion: 2\n", encoding="utf-8")
    composer.copy_service_files(
        [grafana],
        output,
        overwrite=False,
        user_overrides={
            "grafana": {
                "files": {
                    "grafana/provisioning/datasources/datasources.yml": {"source": "overlay.yaml"}
                }
            }
        },
    )
    assert not (output / "grafana/dashboards/infrastructure.json").exists()
    assert (
        yaml.safe_load(
            (output / "grafana/provisioning/datasources/datasources.yml").read_text(
                encoding="utf-8"
            )
        )["apiVersion"]
        == 2
    )


def test_recursive_json_merge_replaces_lists_and_explicit_nodes(tmp_path):
    provider = tmp_path / "provider"
    provider.mkdir()
    (provider / "config.json").write_text(
        json.dumps({"nested": {"keep": 3, "list": [1, 2]}, "remove": {"old": 4}}), encoding="utf-8"
    )
    service = ServiceDefinition(
        name="custom",
        description="test",
        source_path=provider,
        files=[{"source": "config.json", "dest": "custom/config.json"}],
    )
    (tmp_path / "overlay.json").write_text(
        json.dumps(
            {
                "nested": {"list": [8], "add": 5},
                "remove": {"$replace": {"new": 6}},
                "new_section": {"child": {"$replace": {"leaf": 7}}},
            }
        ),
        encoding="utf-8",
    )
    composer = ComposeGenerator(ServiceDiscovery())
    composer.copy_service_files(
        [service],
        tmp_path / ".phlo",
        user_overrides={"custom": {"files": {"custom/config.json": {"source": "overlay.json"}}}},
    )
    assert json.loads((tmp_path / ".phlo/custom/config.json").read_text(encoding="utf-8")) == {
        "nested": {"keep": 3, "list": [8], "add": 5},
        "remove": {"new": 6},
        "new_section": {"child": {"leaf": 7}},
    }


@pytest.mark.parametrize(
    ("content", "record", "error"),
    [
        (b"any text", {"source": "replacement", "mode": "merge"}, "require mode: replace"),
        (b"", {"source": "replacement", "mode": "replace"}, "nonempty text"),
        (b"token=\x00", {"source": "replacement", "mode": "replace"}, "NUL bytes"),
        (b"\xff", {"source": "replacement", "mode": "replace"}, "UTF-8"),
        (b"any text", {"source": "replacement", "mode": "typo"}, "invalid override"),
        (b"any text", {"source": "replacement", "unexpected": True}, "invalid override"),
        (b"any text", {"source": "missing", "mode": "replace"}, "existing file"),
    ],
)
def test_text_replacement_and_override_records_fail_before_writes(tmp_path, content, record, error):
    discovery = ServiceDiscovery()
    trino = discovery.get_service("trino")
    assert trino is not None
    composer = ComposeGenerator(discovery)
    composer.copy_service_files([trino], tmp_path / ".phlo")
    before = _snapshot(tmp_path / ".phlo")
    (tmp_path / "replacement").write_bytes(content)
    with pytest.raises(ValueError, match=error):
        composer.copy_service_files(
            [trino],
            tmp_path / ".phlo",
            user_overrides={"trino": {"files": {"trino/config.properties": record}}},
        )
    assert _snapshot(tmp_path / ".phlo") == before


def test_text_replacement_preserves_crlf_and_unresolved_native_references(tmp_path):
    discovery = ServiceDiscovery()
    trino = discovery.get_service("trino")
    assert trino is not None
    replacement = b"token=${ENV:TRINO_TOKEN}\r\ncoordinator=true\r\n"
    (tmp_path / "replacement").write_bytes(replacement)
    ComposeGenerator(discovery).copy_service_files(
        [trino],
        tmp_path / ".phlo",
        user_overrides={
            "trino": {
                "files": {"trino/config.properties": {"source": "replacement", "mode": "replace"}}
            }
        },
    )
    assert (tmp_path / ".phlo/trino/config.properties").read_bytes() == replacement
