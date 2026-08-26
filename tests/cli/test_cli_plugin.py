"""Tests for plugin CLI commands.

Exercises list/info/search/install/update against an isolated plugin
registry with stub providers, and the generated-container check command in
depth: bounded tool output, exact vulnerability waivers, disposable image
lifecycle (never clobbering pre-existing tags), remote digest scanning,
and pip/uv fallbacks for installs.
"""

import json
import sys

import click
import pytest
from click.testing import CliRunner

from phlo.cli.commands.plugin import plugin_group
from phlo.plugins import PluginMetadata
from phlo.plugins.base import (
    CliCommandPlugin,
    IngestionProviderPlugin,
    QualityProviderPlugin,
    TransformationProviderPlugin,
)
from phlo.plugins.discovery import get_global_registry
from phlo.plugins.registry_client import RegistryPlugin
from tests.helpers import (
    DummyQualityPlugin as DummyQuality,
)
from tests.helpers import (
    DummyServicePlugin as DummyService,
)
from tests.helpers import (
    DummySourcePlugin as DummySource,
)
from tests.helpers import (
    DummyTransformPlugin as DummyTransform,
)


class DummyIngestionProvider(IngestionProviderPlugin):
    """Stub ingestion provider for CLI plugin tests."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy_ingestion", version="1.0.0")

    def get_decorator(self):
        return lambda fn=None, **_kwargs: fn

    def get_asset_retriever(self):
        return list


class DummyQualityProvider(QualityProviderPlugin):
    """Stub quality provider for CLI plugin tests."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy_quality_provider", version="1.0.0")

    def get_decorator(self):
        return lambda fn=None, **_kwargs: fn

    def get_check_classes(self) -> dict[str, type]:
        return {}


class DummyTransformationProvider(TransformationProviderPlugin):
    """Stub transformation provider for CLI plugin tests."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy_transformation_provider", version="1.0.0")

    def get_asset_retriever(self):
        return list


class DummyCliCommand(CliCommandPlugin):
    """Stub CLI command plugin for CLI plugin tests."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="dummy_cli", version="1.0.0")

    def get_cli_commands(self):
        return []


@pytest.fixture
def setup_registry():
    """Yield the global registry cleared and seeded with dummy plugins."""
    registry = get_global_registry()
    registry.clear()
    registry.register("source_connector", DummySource(), replace=True)
    registry.register("quality_check", DummyQuality(), replace=True)
    registry.register("transformation", DummyTransform(), replace=True)
    registry.register("service", DummyService(), replace=True)
    yield registry
    registry.clear()


def _result(returncode=0, stdout="", stderr=""):
    """Build a minimal subprocess result stand-in."""
    return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def install_fake_run(respond=None, *, record_kwargs=False):
    """Patch run_command/run to record invocations; returns the recording list.

    Returns ``(calls, fake_run)``. Every invocation appends its argv to
    ``calls`` -- or an ``(argv, kwargs)`` pair when ``record_kwargs`` is set --
    and ``fake_run(argv, **kwargs)`` replays ``respond(argv, kwargs)``,
    falling back to a successful empty result.
    """
    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs) if record_kwargs else argv)
        return respond(argv, kwargs) if respond is not None else _result()

    return calls, fake_run


def _compose_config(services):
    """Serialized docker compose config payload served by fake runners."""
    return json.dumps({"name": "test-project", "services": services})


def test_plugin_list_json_installed(setup_registry):
    """List command returns installed plugins as JSON."""
    # #given
    runner = CliRunner()

    # #when
    result = runner.invoke(plugin_group, ["list", "--json"])

    # #then
    data = json.loads(result.output)
    types = {plugin["type"] for plugin in data["installed"]}
    assert result.exit_code == 0
    assert types >= {"source", "quality", "transform", "service"}
    assert {plugin["name"] for plugin in data["installed"]} >= {
        "dummy_source",
        "dummy_quality",
        "dummy_transform",
        "dummy_service",
    }


def test_plugin_list_accepts_singular_type_alias(setup_registry):
    """List accepts the same singular aliases as plugin create."""
    result = CliRunner().invoke(plugin_group, ["list", "--type", "source", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [plugin["name"] for plugin in data["installed"]] == ["dummy_source"]


def test_plugin_list_accepts_provider_and_cli_type_aliases(setup_registry):
    """List can filter plugin categories that are shown in all-plugin output."""
    registry = setup_registry
    registry.register("ingestion_provider", DummyIngestionProvider(), replace=True)
    registry.register("quality_provider", DummyQualityProvider(), replace=True)
    registry.register("transformation_provider", DummyTransformationProvider(), replace=True)
    registry.register("cli_command", DummyCliCommand(), replace=True)

    runner = CliRunner()

    ingestion = runner.invoke(plugin_group, ["list", "--type", "ingestion", "--json"])
    quality_provider = runner.invoke(plugin_group, ["list", "--type", "quality-provider", "--json"])
    transformation_provider = runner.invoke(
        plugin_group, ["list", "--type", "transformation-provider", "--json"]
    )
    cli = runner.invoke(plugin_group, ["list", "--type", "cli", "--json"])

    assert ingestion.exit_code == 0
    assert quality_provider.exit_code == 0
    assert transformation_provider.exit_code == 0
    assert cli.exit_code == 0
    assert json.loads(ingestion.output)["installed"][0]["type"] == "ingestion_provider"
    assert json.loads(quality_provider.output)["installed"][0]["type"] == "quality_provider"
    assert (
        json.loads(transformation_provider.output)["installed"][0]["type"]
        == "transformation_provider"
    )
    assert json.loads(cli.output)["installed"][0]["type"] == "cli"


def test_plugin_info_resolves_phlo_distribution_alias(setup_registry):
    """Info resolves common package-style names such as phlo-trino."""
    result = CliRunner().invoke(plugin_group, ["info", "phlo-dummy-source", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "dummy_source"


def test_plugin_check_json_emits_only_json(setup_registry):
    """Check --json stdout is parseable JSON without prose prefixes."""
    result = CliRunner().invoke(plugin_group, ["check", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "valid" in data
    assert "invalid" in data


def test_plugin_check_containers_checks_generated_project(monkeypatch, setup_registry, tmp_path):
    """Container checks run tools against files generated in an external project."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[0] == "/bin/phlo":
            project = kwargs["cwd"]
            dockerfile = project / ".phlo" / "dagster" / "Dockerfile"
            dockerfile.parent.mkdir(parents=True, exist_ok=True)
            dockerfile.write_text("FROM python:3.11\n")
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {
                        "dagster": {"image": "example/dagster:1"},
                        "observatory": {"image": "example/observatory:1"},
                    }
                )
            )
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:test\n")
        return _result()

    calls, fake_run = install_fake_run(respond, record_kwargs=True)
    monkeypatch.setattr(check_module.subprocess, "run", fake_run)
    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(check_module, "discover_plugins", lambda **_: {"service": []})
    monkeypatch.setattr(check_module, "validate_plugins", lambda: {"valid": [], "invalid": []})

    result = check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={
            "dagster/Dockerfile": "phlo-dagster",
            "@service:dagster": "phlo-dagster",
            "@service:observatory": "phlo-observatory",
        },
        service_names=["phlo-api", "observatory"],
    )

    assert result["dockerfiles"] == ["dagster/Dockerfile"]
    assert result["owners"] == {"dagster/Dockerfile": "phlo-dagster"}
    assert calls[0][0] == ["/bin/phlo", "services", "init", "--no-dev", "--force"]
    assert calls[1][0] == [
        "/bin/phlo",
        "services",
        "add",
        "--service",
        "phlo-api",
        "--service",
        "observatory",
        "--no-start",
    ]
    project_mount = f"{calls[0][1]['cwd'].resolve()}:/workspace:ro"
    assert calls[2][0] == [
        "/bin/docker",
        "run",
        "--rm",
        "-v",
        project_mount,
        check_module.HADOLINT_IMAGE,
        "/bin/hadolint",
        "/workspace/.phlo/dagster/Dockerfile",
    ]
    assert any(
        check_module.TRIVY_IMAGE in command
        and "image" in command
        and "/var/run/docker.sock:/var/run/docker.sock" in command
        for command, _ in calls
    )
    assert calls[-1][0][-1] == "/workspace/.phlo"
    assert result["services"] == [
        {
            "service": "dagster",
            "package": "phlo-dagster",
            "image": "example/dagster:1",
            "image_id": "sha256:test",
            "status": "passed",
            "image_scan": "passed",
            "high_count": 0,
            "critical_count": 0,
            "vulnerable_components": [],
        },
        {
            "service": "observatory",
            "package": "phlo-observatory",
            "image": "example/observatory:1",
            "image_id": "sha256:test",
            "status": "passed",
            "image_scan": "passed",
            "high_count": 0,
            "critical_count": 0,
            "vulnerable_components": [],
        },
    ]


def test_service_inventory_attributes_companion_service_files(monkeypatch, tmp_path):
    """Files declared by companion service YAMLs retain package ownership."""
    from phlo.cli.commands.plugin import check as check_module

    package_root = tmp_path / "phlo_openmetadata"
    package_root.mkdir()
    plugin = type("Plugin", (), {"service_definition": {"name": "openmetadata"}})()
    plugin.get_files = list
    companion = type(
        "Definition",
        (),
        {
            "source_path": package_root / "openmetadata-elasticsearch-setup.yaml",
            "files": [
                {
                    "source": "es.Dockerfile",
                    "dest": "openmetadata-elasticsearch/Dockerfile",
                }
            ],
        },
    )()

    monkeypatch.setattr(check_module, "discover_plugins", lambda **_: {"service": [plugin]})
    monkeypatch.setattr(check_module, "_plugin_package", lambda _: "phlo-openmetadata")
    monkeypatch.setattr(check_module, "resolve_plugin_source_path", lambda _: package_root)
    monkeypatch.setattr(
        check_module,
        "ServiceDiscovery",
        lambda: type(
            "Discovery",
            (),
            {"discover": lambda self: {"openmetadata-elasticsearch": companion}},
        )(),
    )

    owners, _ = check_module._service_inventory()

    assert owners["openmetadata-elasticsearch/Dockerfile"] == "phlo-openmetadata"


def test_plugin_check_containers_reports_tool_failure(monkeypatch, tmp_path):
    """A generated-container tool failure is reported as a CLI failure."""
    from phlo.cli.commands.plugin import check as check_module

    _, fake_run = install_fake_run(lambda argv, kwargs: _result(1, "bad", "failure"))

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(check_module.subprocess, "run", fake_run)

    with pytest.raises(check_module.ContainerCheckError, match="phlo services init failed"):
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={"dagster/Dockerfile": "phlo-dagster"},
        )

    with pytest.raises(check_module.ContainerCheckError) as exc_info:
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={"dagster/Dockerfile": "phlo-dagster"},
        )
    assert "stdout: bad" in str(exc_info.value)
    assert "stderr: failure" in str(exc_info.value)


def test_plugin_check_containers_bounds_large_tool_failure_output(monkeypatch, tmp_path):
    """Large scanner reports retain both streams without exhausting memory."""
    from phlo.cli.commands.plugin import check as check_module

    stdout = "stdout-start\n" + ("x" * (check_module.MAX_TOOL_OUTPUT_CHARS + 100)) + "\nstdout-end"
    stderr = "stderr-start\n" + ("y" * (check_module.MAX_TOOL_OUTPUT_CHARS + 100)) + "\nstderr-end"

    _, fake_run = install_fake_run(lambda argv, kwargs: _result(1, stdout, stderr))

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(check_module.subprocess, "run", fake_run)

    with pytest.raises(check_module.ContainerCheckError) as exc_info:
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={"dagster/Dockerfile": "phlo-dagster"},
        )

    message = str(exc_info.value)
    assert "stdout-start" in message
    assert "stdout-end" in message
    assert "stderr-start" in message
    assert "stderr-end" in message
    assert "output truncated" in message
    assert len(message) < check_module.MAX_TOOL_OUTPUT_CHARS * 3


def test_trivy_image_scan_retains_a_parseable_bounded_json_report(monkeypatch, tmp_path):
    """Trivy JSON gets a larger bounded capture than human-readable tool failures."""
    from phlo.cli.commands.plugin import check as check_module

    capture: dict[str, int] = {}

    def fake_capture(command, **kwargs):
        capture["max_output_chars"] = kwargs["max_output_chars"]
        return _result(1, '{"Results": []}', "findings")

    monkeypatch.setattr(check_module, "_run_with_capture", fake_capture)

    failure, evidence, waivable = check_module._run_trivy_image_scan(
        ["docker", "run", "trivy", "image"],
        cwd=tmp_path,
        runner=object(),
        label="trivy image test",
    )

    assert capture["max_output_chars"] == check_module.MAX_TRIVY_JSON_CHARS
    assert capture["max_output_chars"] > check_module.MAX_TOOL_OUTPUT_CHARS
    assert failure is not None
    assert evidence == {"high_count": 0, "critical_count": 0, "vulnerable_components": []}
    assert waivable is False


def test_plugin_check_containers_keeps_large_compose_config_parseable(monkeypatch, tmp_path):
    """The generated service inventory is bounded separately from tool transcripts."""
    from phlo.cli.commands.plugin import check as check_module

    compose_config = {
        "name": "test-project",
        "services": {
            "one": {
                "image": "example/one:1",
                "labels": {"padding": "x" * check_module.MAX_TOOL_OUTPUT_CHARS},
            }
        },
    }

    def respond(argv, kwargs):
        if argv[:3] == ["/bin/docker", "compose", "--profile"]:
            return _result(stdout=_compose_config(compose_config["services"]))
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:test\n")
        return _result()

    _, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    result = check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:one": "package-one"},
        command_runner=fake_run,
    )

    assert result["services"][0]["status"] == "passed"


def test_plugin_check_containers_builds_a_shared_exact_image_once(monkeypatch, tmp_path):
    """Services sharing one exact build tag must retain the image ID that gets scanned."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {
                        "server": {"image": "example/server:1", "build": {"context": "."}},
                        "setup": {"image": "example/server:1", "build": {"context": "."}},
                    }
                )
            )
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:shared\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    result = check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={
            "@service:server": "package-server",
            "@service:setup": "package-server",
        },
        command_runner=fake_run,
    )

    build_calls = [command for command in calls if "build" in command]
    assert len(build_calls) == 1
    assert [service["status"] for service in result["services"]] == ["passed", "passed"]


def test_plugin_check_containers_scans_each_build_before_starting_the_next(monkeypatch, tmp_path):
    """Generated builds use disposable cache and get scanned before disk use accumulates."""
    from phlo.cli.commands.plugin import check as check_module

    inspect_counts: dict[str, int] = {}

    def respond(argv, kwargs):
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {
                        "one": {"image": "example/one:1", "build": {"context": "one"}},
                        "two": {"image": "example/two:1", "build": {"context": "two"}},
                    }
                )
            )
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            image = argv[-1]
            inspect_counts[image] = inspect_counts.get(image, 0) + 1
            if inspect_counts[image] == 1:
                return _result(1, "", "No such image")
            return _result(stdout=f"sha256:{image.split('/')[1][:-2]}\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={
            "@service:one": "package-one",
            "@service:two": "package-two",
        },
        command_runner=fake_run,
    )

    build_calls = [command for command in calls if "compose" in command and "build" in command]
    assert len(build_calls) == 2
    assert all("--builder" in command for command in build_calls)
    first_scan = next(
        index
        for index, command in enumerate(calls)
        if check_module.TRIVY_IMAGE in command and "sha256:one" in command
    )
    second_build = calls.index(build_calls[1])
    assert first_scan < second_build
    create_call = next(command for command in calls if command[1:3] == ["buildx", "create"])
    prune_calls = [command for command in calls if command[1:3] == ["buildx", "prune"]]
    remove_call = next(command for command in calls if command[1:3] == ["buildx", "rm"])
    assert len(prune_calls) == 2
    assert first_scan < calls.index(prune_calls[0]) < second_build
    assert all(
        command
        == [
            "/bin/docker",
            "buildx",
            "prune",
            "--builder",
            create_call[create_call.index("--name") + 1],
            "--force",
        ]
        for command in prune_calls
    )
    assert create_call[create_call.index("--name") + 1] == remove_call[-1]


def test_plugin_check_containers_remote_mode_never_builds_or_pulls(monkeypatch, tmp_path) -> None:
    """Published images resolve to immutable digests and scan without local image storage."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[:3] == ["/bin/docker", "compose", "--profile"]:
            return _result(
                stdout=_compose_config(
                    {
                        "one": {
                            "image": "ghcr.io/phlohouse/phlo-one:1.2.3",
                            "build": {"context": "one"},
                        }
                    }
                )
            )
        if argv[1:4] == ["buildx", "imagetools", "inspect"]:
            return _result(stdout=f'"sha256:{"a" * 64}"\n')
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    result = check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:one": "package-one"},
        command_runner=fake_run,
        remote_images=True,
    )

    assert not any("build" in command for command in calls if "compose" in command)
    assert not any(command[1:2] == ["pull"] for command in calls)
    assert not any(command[1:3] == ["image", "inspect"] for command in calls)
    remote_scan = next(
        command
        for command in calls
        if check_module.TRIVY_IMAGE in command and "--image-src" in command
    )
    assert remote_scan[remote_scan.index("--image-src") + 1] == "remote"
    assert remote_scan[-1] == f"ghcr.io/phlohouse/phlo-one@sha256:{'a' * 64}"
    assert result["services"][0]["image_id"] == remote_scan[-1]


def test_plugin_check_containers_removes_only_images_created_for_the_check(monkeypatch, tmp_path):
    """Temporary validation images are removed without touching pre-existing tags."""
    from phlo.cli.commands.plugin import check as check_module

    new_image_inspects = 0

    def respond(argv, kwargs):
        nonlocal new_image_inspects
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {
                        "new": {"image": "example/new:1", "build": {"context": "."}},
                        "existing": {"image": "example/existing:1"},
                    }
                )
            )
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            image = argv[-1]
            if image == "example/new:1":
                new_image_inspects += 1
                if new_image_inspects == 1:
                    return _result(1, "", "No such image")
                image_id = "sha256:new"
            else:
                image_id = "sha256:existing"
            return _result(stdout=f"{image_id}\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={
            "@service:new": "package-new",
            "@service:existing": "package-existing",
        },
        command_runner=fake_run,
    )

    remove_calls = [command for command in calls if command[1:3] == ["image", "rm"]]
    assert ["/bin/docker", "image", "rm", "example/new:1"] in remove_calls
    assert ["/bin/docker", "image", "rm", "example/existing:1"] not in remove_calls


def test_plugin_check_containers_restores_preexisting_local_image_tag(monkeypatch, tmp_path):
    """A validation build cannot replace an image tag that the operator already had."""
    from phlo.cli.commands.plugin import check as check_module

    inspect_count = 0

    def respond(argv, kwargs):
        nonlocal inspect_count
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {"existing": {"image": "example/existing:1", "build": {"context": "."}}}
                )
            )
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            inspect_count += 1
            image_id = "sha256:original" if inspect_count == 1 else "sha256:validation"
            return _result(stdout=f"{image_id}\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:existing": "package-existing"},
        command_runner=fake_run,
    )

    assert [
        "/bin/docker",
        "image",
        "tag",
        "sha256:original",
        "example/existing:1",
    ] in calls
    assert ["/bin/docker", "image", "rm", "sha256:validation"] in calls


def test_plugin_check_containers_restores_preexisting_pulled_image_tag(monkeypatch, tmp_path):
    """A validation pull cannot refresh an image tag that the operator already had."""
    from phlo.cli.commands.plugin import check as check_module

    inspect_count = 0

    def respond(argv, kwargs):
        nonlocal inspect_count
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(stdout=_compose_config({"remote": {"image": "example/remote:latest"}}))
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            inspect_count += 1
            image_id = "sha256:original" if inspect_count == 1 else "sha256:pulled"
            return _result(stdout=f"{image_id}\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:remote": "package-remote"},
        command_runner=fake_run,
    )

    assert [
        "/bin/docker",
        "image",
        "tag",
        "sha256:original",
        "example/remote:latest",
    ] in calls
    assert ["/bin/docker", "image", "rm", "sha256:pulled"] in calls


def test_existing_image_lookup_fails_closed_on_inspect_error(tmp_path) -> None:
    """A Docker inspect outage cannot be mistaken for an absent operator image."""
    from phlo.cli.commands.plugin import check as check_module

    _, fake_run = install_fake_run(lambda argv, kwargs: _result(1, "", "daemon unavailable"))

    with pytest.raises(check_module.ContainerCheckError, match="daemon unavailable"):
        check_module._existing_image_id(
            "/bin/docker",
            "example/operator:1",
            cwd=tmp_path,
            runner=fake_run,
        )


def test_plugin_check_containers_reuses_configured_trivy_cache(monkeypatch, tmp_path):
    """A configured cache survives the generated project cleanup for reuse."""
    from phlo.cli.commands.plugin import check as check_module

    cache_dir = tmp_path / "trivy-cache"

    def respond(argv, kwargs):
        if argv[:3] == ["/bin/docker", "compose", "--profile"]:
            return _result(stdout=_compose_config({"one": {"image": "example/one:1"}}))
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:test\n")
        return _result()

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setenv("PHLO_TRIVY_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:one": "package-one"},
        command_runner=fake_run,
    )

    assert cache_dir.is_dir()
    assert any(
        f"{cache_dir.resolve()}:/root/.cache/trivy" in command
        for command in calls
        if command[0] == "/bin/docker"
    )
    trivy_image_command = next(
        command for command in calls if check_module.TRIVY_IMAGE in command and "image" in command
    )
    assert trivy_image_command[
        trivy_image_command.index("--timeout") : trivy_image_command.index("--timeout") + 2
    ] == ["--timeout", "15m"]


def test_plugin_check_containers_requires_installed_cli(monkeypatch, tmp_path):
    from phlo.cli.commands.plugin import check as check_module

    monkeypatch.setattr(
        check_module.shutil, "which", lambda name: None if name == "phlo" else f"/bin/{name}"
    )

    with pytest.raises(check_module.ContainerCheckError, match="installed CLI 'phlo'"):
        check_module.check_generated_containers(project_parent=tmp_path, service_files={})


def test_plugin_check_containers_rejects_unowned_dockerfile(monkeypatch, tmp_path):
    """Generated Dockerfiles without discovered package ownership fail closed."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        dockerfile = kwargs["cwd"] / ".phlo" / "unknown" / "Dockerfile"
        dockerfile.parent.mkdir(parents=True, exist_ok=True)
        dockerfile.write_text("FROM python:3.11\n")
        return _result()

    _, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(check_module.ContainerCheckError, match="no package owner"):
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={},
            command_runner=fake_run,
        )


def test_plugin_check_containers_reports_all_package_failures(monkeypatch, tmp_path):
    """All generated package failures are reported after every scanner runs."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[0] == "/bin/phlo":
            for name in ("one", "two"):
                dockerfile = kwargs["cwd"] / ".phlo" / name / "Dockerfile"
                dockerfile.parent.mkdir(parents=True, exist_ok=True)
                dockerfile.write_text("FROM python:3.11\n")
            return _result()
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {
                        "one": {"image": "example/one:1"},
                        "two": {"image": "example/two:1"},
                    }
                )
            )
        return _result(1, "", "failed")

    calls, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(check_module.ContainerCheckError) as exc_info:
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={
                "one/Dockerfile": "package-one",
                "two/Dockerfile": "package-two",
                "@service:one": "package-one",
                "@service:two": "package-two",
            },
            command_runner=fake_run,
        )

    message = str(exc_info.value)
    assert "package-one" in message
    assert "package-two" in message
    assert "trivy [project]" in message
    assert "stdout:" not in message
    assert [command[0] for command in calls].count("/bin/phlo") == 1
    assert [command[0] for command in calls].count("/bin/docker") >= 5


def test_plugin_check_containers_scans_original_after_wrapper_build_failure(monkeypatch, tmp_path):
    """A wrapper failure still gets an attributed scan of the resolved base image."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[0] == "/bin/phlo":
            dockerfile = kwargs["cwd"] / ".phlo" / "one" / "Dockerfile"
            dockerfile.parent.mkdir(parents=True, exist_ok=True)
            dockerfile.write_text("FROM python:3.11\n")
            return _result()
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(
                stdout=_compose_config(
                    {"one": {"image": "example/one:1", "build": {"context": "."}}}
                )
            )
        if "build" in argv and argv[-1] == "one":
            return _result(1, "build output", "build failed")
        if argv[:2] == ["/bin/docker", "pull"]:
            return _result()
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:base\n")
        if "image" in argv:
            return _result(1, "trivy output", "trivy error")
        return _result()

    _, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(check_module.ContainerCheckError) as exc_info:
        check_module.check_generated_containers(
            project_parent=tmp_path,
            service_files={
                "one/Dockerfile": "package-one",
                "@service:one": "package-one",
            },
            command_runner=fake_run,
        )

    message = str(exc_info.value)
    assert "service [package-one] one: example/one:1 -> failed (image scan: failed)" in message
    assert "build output" in message
    assert "trivy error" in message
    assert "no image-scan result" not in message


def test_plugin_check_containers_reports_exact_image_vulnerability_waiver(monkeypatch, tmp_path):
    """An exact service/image waiver is visible and does not hide other failures."""
    from phlo.cli.commands.plugin import check as check_module

    def respond(argv, kwargs):
        if argv[0] == "/bin/phlo":
            (kwargs["cwd"] / ".phlo").mkdir(parents=True, exist_ok=True)
            return _result()
        if argv[:3] == ["/bin/docker", "compose", "--profile"] and argv[-2:] == [
            "--format",
            "json",
        ]:
            return _result(stdout=_compose_config({"one": {"image": "example/one:1"}}))
        if argv[:2] == ["/bin/docker", "pull"]:
            return _result()
        if argv[:3] == ["/bin/docker", "image", "inspect"]:
            return _result(stdout="sha256:one\n")
        if "image" in argv:
            return _result(
                1,
                json.dumps(
                    {
                        "Results": [
                            {
                                "Target": "one-binary",
                                "Class": "lang-pkgs",
                                "Type": "gobinary",
                                "Vulnerabilities": [
                                    {
                                        "VulnerabilityID": "CVE-TEST",
                                        "PkgName": "example/component",
                                        "InstalledVersion": "1.0.0",
                                        "FixedVersion": "1.0.1",
                                        "Severity": "HIGH",
                                    }
                                ],
                            }
                        ]
                    }
                ),
            )
        return _result()

    _, fake_run = install_fake_run(respond)

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/bin/{name}")

    result = check_module.check_generated_containers(
        project_parent=tmp_path,
        service_files={"@service:one": "package-one"},
        vulnerability_waivers={
            ("one", "example/one:1"): check_module.VulnerabilityWaiver(
                evidence_sha256="8f315d5e0ddd6c0d0c830665f6b519de3c1ace3cc7386651ebb0bee566fcad61",
                reason="No patched upstream release is available",
            )
        },
        command_runner=fake_run,
    )

    assert result["trivy"] == "passed with explicit vulnerability waiver(s)"
    assert result["services"] == [
        {
            "service": "one",
            "package": "package-one",
            "image": "example/one:1",
            "image_id": "sha256:one",
            "status": "waived",
            "image_scan": "waived",
            "high_count": 1,
            "critical_count": 0,
            "vulnerable_components": [
                {
                    "target": "one-binary",
                    "class": "lang-pkgs",
                    "type": "gobinary",
                    "component": "example/component",
                    "installed_version": "1.0.0",
                    "fixed_version": "1.0.1",
                    "vulnerability_id": "CVE-TEST",
                    "severity": "HIGH",
                }
            ],
            "vulnerability_waiver": "No patched upstream release is available",
            "vulnerability_evidence_sha256": (
                "8f315d5e0ddd6c0d0c830665f6b519de3c1ace3cc7386651ebb0bee566fcad61"
            ),
            "detail": (
                "trivy image sha256:one failed with exit code 1: "
                'stdout: {"Results": [{"Target": "one-binary", "Class": "lang-pkgs", '
                '"Type": "gobinary", "Vulnerabilities": [{"VulnerabilityID": "CVE-TEST", '
                '"PkgName": "example/component", "InstalledVersion": "1.0.0", '
                '"FixedVersion": "1.0.1", "Severity": "HIGH"}]}]}'
            ),
        }
    ]


def test_plugin_check_rejects_ambiguous_vulnerability_waiver() -> None:
    """Waivers must bind a service and image to exact vulnerability evidence."""
    from phlo.cli.commands.plugin import check as check_module

    with pytest.raises(click.BadParameter, match="SERVICE=IMAGE=EVIDENCE_SHA256=REASON"):
        check_module._parse_vulnerability_waivers(("one=example/one:1",))


def test_plugin_check_containers_is_available_at_public_cli_seam(monkeypatch, setup_registry):
    """The public check command exposes generated-container results as JSON."""
    from phlo.cli.commands.plugin import check as check_module

    monkeypatch.setattr(
        check_module,
        "check_generated_containers",
        lambda **_: {
            "dockerfiles": ["dagster/Dockerfile"],
            "owners": {"dagster/Dockerfile": "phlo-dagster"},
        },
    )

    result = CliRunner().invoke(plugin_group, ["check", "--containers", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["containers"]["owners"] == {
        "dagster/Dockerfile": "phlo-dagster"
    }


def test_plugin_check_containers_forwards_exact_vulnerability_waiver(
    monkeypatch, setup_registry
) -> None:
    """The public CLI must pass an exact image waiver to the container checker."""
    from phlo.cli.commands.plugin import check as check_module

    received: dict[str, object] = {}

    def fake_check_generated_containers(**kwargs):
        received.update(kwargs)
        return {"dockerfiles": [], "owners": {}, "services": []}

    monkeypatch.setattr(check_module, "check_generated_containers", fake_check_generated_containers)

    result = CliRunner().invoke(
        plugin_group,
        [
            "check",
            "--containers",
            "--json",
            "--allow-vulnerable-image",
            "alloy=phlo/alloy:v1.18.0-go1.26.5="
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa="
            "no compatible upstream fix",
        ],
    )

    assert result.exit_code == 0
    assert received["vulnerability_waivers"] == {
        ("alloy", "phlo/alloy:v1.18.0-go1.26.5"): check_module.VulnerabilityWaiver(
            evidence_sha256="a" * 64,
            reason="no compatible upstream fix",
        )
    }


def test_plugin_check_containers_forwards_remote_image_mode(monkeypatch, setup_registry) -> None:
    """CI can scan published image digests without rebuilding generated services."""
    from phlo.cli.commands.plugin import check as check_module

    received: dict[str, object] = {}

    def fake_check_generated_containers(**kwargs):
        received.update(kwargs)
        return {"dockerfiles": [], "owners": {}, "services": []}

    monkeypatch.setattr(check_module, "check_generated_containers", fake_check_generated_containers)

    result = CliRunner().invoke(
        plugin_group,
        ["check", "--containers", "--remote-images", "--json"],
    )

    assert result.exit_code == 0
    assert received["remote_images"] is True


def test_plugin_check_containers_preserves_package_owner_in_failure_output(
    monkeypatch, setup_registry
) -> None:
    """Rich output must not consume bracketed package ownership as markup."""
    from phlo.cli.commands.plugin import check as check_module

    monkeypatch.setattr(
        check_module,
        "check_generated_containers",
        lambda **_: (_ for _ in ()).throw(
            check_module.ContainerCheckError(
                "Generated container checks failed:\n"
                "- service [package-one] one: example/one:1 -> failed"
            )
        ),
    )

    result = CliRunner().invoke(plugin_group, ["check", "--containers"])

    assert result.exit_code == 1
    assert "[package-one]" in result.output


def test_plugin_check_containers_prints_waived_scanner_detail_without_markup(
    monkeypatch, setup_registry
) -> None:
    """Scanner paths that resemble Rich closing tags must print literally."""
    from phlo.cli.commands.plugin import check as check_module

    monkeypatch.setattr(
        check_module,
        "check_generated_containers",
        lambda **_: {
            "dockerfiles": [],
            "owners": {},
            "services": [
                {
                    "status": "waived",
                    "package": "phlo-clickstack",
                    "service": "clickstack",
                    "image": "example/clickstack:1",
                    "vulnerability_waiver": "No compatible upstream fix",
                    "detail": "scanner target [/var/lib/clickhouse]",
                }
            ],
        },
    )

    result = CliRunner().invoke(plugin_group, ["check", "--containers"])

    assert result.exit_code == 0
    assert "scanner target [/var/lib/clickhouse]" in result.output


def test_plugin_list_all_json(setup_registry, monkeypatch):
    """List command includes registry plugins when --all is set."""
    registry_plugins = [
        RegistryPlugin(
            name="registry_source",
            type="source",
            package="phlo-plugin-registry",
            version="1.2.3",
            description="Registry plugin",
            author="Phlo Team",
            homepage=None,
            tags=["example"],
            verified=True,
            core=False,
        )
    ]

    def mock_collect_registry_plugins(plugin_type: str) -> list[dict]:
        """Return the mocked registry plugins serialized as CLI payload dicts."""
        from phlo.cli.commands.plugin.utils import registry_plugin_to_dict

        return [registry_plugin_to_dict(p) for p in registry_plugins]

    monkeypatch.setattr(
        "phlo.cli.commands.plugin.list.collect_registry_plugins",
        mock_collect_registry_plugins,
    )

    runner = CliRunner()
    result = runner.invoke(plugin_group, ["list", "--all", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "installed" in data
    assert "available" in data
    assert data["available"][0]["name"] == "registry_source"


def test_plugin_search(monkeypatch):
    """Search command returns registry plugins."""
    registry_plugins = [
        RegistryPlugin(
            name="registry_service",
            type="service",
            package="phlo-plugin-service",
            version="1.0.0",
            description="Service plugin",
            author="Phlo Team",
            homepage=None,
            tags=["service"],
            verified=True,
            core=False,
        )
    ]

    monkeypatch.setattr(
        "phlo.cli.commands.plugin.search.search_plugins",
        lambda query, plugin_type, tags: registry_plugins,
    )

    runner = CliRunner()
    result = runner.invoke(plugin_group, ["search", "service", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "registry_service"


def test_plugin_search_includes_installed_plugins(monkeypatch, setup_registry):
    """Search should not hide installed plugins when registry results are sparse."""
    monkeypatch.setattr("phlo.cli.commands.plugin.search.search_plugins", lambda *_args, **_kw: [])

    result = CliRunner().invoke(plugin_group, ["search", "dummy", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {item["name"] for item in data} >= {
        "dummy_source",
        "dummy_quality",
        "dummy_transform",
        "dummy_service",
    }


def test_plugin_install(monkeypatch):
    """Install command resolves registry name and calls pip."""
    registry_plugin = RegistryPlugin(
        name="registry_source",
        type="source",
        package="phlo-plugin-registry",
        version="1.0.0",
        description="Registry plugin",
        author="Phlo Team",
        homepage=None,
        tags=["example"],
        verified=True,
        core=False,
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "phlo.cli.commands.plugin.install.get_registry_plugin",
        lambda name: registry_plugin,
    )
    monkeypatch.setattr("phlo.cli.commands.plugin.install.run_pip", lambda args: calls.append(args))

    runner = CliRunner()
    result = runner.invoke(plugin_group, ["install", "registry_source"])

    assert result.exit_code == 0
    assert calls == [["install", "phlo-plugin-registry==1.0.0"]]


def test_plugin_update(monkeypatch):
    """Update command upgrades installed plugins."""
    registry_plugins = [
        RegistryPlugin(
            name="registry_source",
            type="source",
            package="phlo-plugin-registry",
            version="2.0.0",
            description="Registry plugin",
            author="Phlo Team",
            homepage=None,
            tags=["example"],
            verified=True,
            core=False,
        )
    ]
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "phlo.cli.commands.plugin.update.list_registry_plugins",
        lambda: registry_plugins,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.get_installed_version",
        lambda package: "1.0.0",
    )
    monkeypatch.setattr("phlo.cli.commands.plugin.update.run_pip", lambda args: calls.append(args))

    runner = CliRunner()
    result = runner.invoke(plugin_group, ["update"])

    assert result.exit_code == 0
    assert calls == [["install", "--upgrade", "phlo-plugin-registry==2.0.0"]]


def test_run_pip_prefers_uv_when_available(monkeypatch):
    """Use `uv pip` when uv is available."""
    from phlo.cli.commands.plugin.utils import run_pip

    calls: list[tuple[list[str], bool, float]] = []

    monkeypatch.setattr("phlo.cli.commands.plugin.utils.shutil.which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.importlib.util.find_spec", lambda _: object()
    )
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.subprocess.run",
        lambda cmd, check, timeout: calls.append((cmd, check, timeout)),
    )

    run_pip(["install", "demo-plugin"], timeout=12)

    assert calls == [(["uv", "pip", "install", "demo-plugin"], True, 12)]


def test_run_pip_uses_python_pip_when_uv_missing(monkeypatch):
    """Use `python -m pip` when uv is unavailable and pip is importable."""
    from phlo.cli.commands.plugin.utils import run_pip

    calls: list[tuple[list[str], bool, float]] = []

    monkeypatch.setattr("phlo.cli.commands.plugin.utils.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.importlib.util.find_spec", lambda _: object()
    )
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.subprocess.run",
        lambda cmd, check, timeout: calls.append((cmd, check, timeout)),
    )

    run_pip(["install", "demo-plugin"], timeout=12)

    assert calls == [([sys.executable, "-m", "pip", "install", "demo-plugin"], True, 12)]


def test_run_pip_uses_uv_when_pip_module_missing(monkeypatch):
    """Use `uv pip` when pip module is unavailable but `uv` exists."""
    from phlo.cli.commands.plugin.utils import run_pip

    calls: list[tuple[list[str], bool, float]] = []

    monkeypatch.setattr("phlo.cli.commands.plugin.utils.importlib.util.find_spec", lambda _: None)
    monkeypatch.setattr("phlo.cli.commands.plugin.utils.shutil.which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(
        "phlo.cli.commands.plugin.utils.subprocess.run",
        lambda cmd, check, timeout: calls.append((cmd, check, timeout)),
    )

    run_pip(["install", "demo-plugin"], timeout=9)

    assert calls == [(["uv", "pip", "install", "demo-plugin"], True, 9)]


def test_run_pip_errors_when_no_pip_and_no_uv(monkeypatch):
    """Raise runtime error when neither pip module nor `uv` is available."""
    from phlo.cli.commands.plugin.utils import run_pip

    monkeypatch.setattr("phlo.cli.commands.plugin.utils.importlib.util.find_spec", lambda _: None)
    monkeypatch.setattr("phlo.cli.commands.plugin.utils.shutil.which", lambda _: None)

    with pytest.raises(RuntimeError, match="pip module is unavailable"):
        run_pip(["install", "demo-plugin"])


def test_normalize_plugin_type_reports_unknown_type() -> None:
    """Internal callers get a clear error for unmapped plugin types."""
    from phlo.cli.commands.plugin.utils import normalize_plugin_type

    with pytest.raises(ValueError, match="Unknown plugin type: nope"):
        normalize_plugin_type("nope")
