"""Focused contracts for the installed-provider artifact harness.

Loads scripts/verify_installed_provider_artifacts.py directly via importlib
and checks its workspace inventory, external-environment scrubbing, batch
rendering evidence, and complete build/health evidence across image families.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "installed_provider_artifacts", REPO_ROOT / "scripts" / "verify_installed_provider_artifacts.py"
)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


def test_workspace_inventory_contains_root_and_every_provider() -> None:
    packages = HARNESS.workspace_packages(REPO_ROOT)
    package_dirs = [path.parent for path in (REPO_ROOT / "packages").glob("*/pyproject.toml")]

    assert packages[0].name == "phlo"
    assert len(packages) == len(package_dirs) + 1
    assert {package.name for package in packages} == {"phlo"} | {
        package_dir.name for package_dir in package_dirs
    }


def test_external_environment_removes_workspace_import_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/workspace/source")
    monkeypatch.setenv("PHLO_DEV_SOURCE", "/workspace/source")

    environment = HARNESS.external_environment()

    assert "PYTHONPATH" not in environment
    assert "PHLO_DEV_SOURCE" not in environment


def test_missing_inventory_entries_are_reported(tmp_path: Path) -> None:
    packages = [HARNESS.WorkspacePackage("phlo-example", tmp_path, (), {})]

    checks = HARNESS.assert_installed_artifacts(
        packages=packages, wheelhouse={}, installed={}, repo_root=tmp_path
    )

    assert checks["missing_packages"] == ["phlo-example"]
    assert checks["missing_wheels"] == ["phlo-example"]


def test_external_constraints_select_versions_from_real_wheels(tmp_path, monkeypatch):
    import json
    import subprocess
    import venv
    import zipfile

    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    for name, version in (("example", "1.0.0"), ("example", "2.0.0"), ("phlo", "1.0.0")):
        dist_info = f"{name}-{version}.dist-info"
        with zipfile.ZipFile(wheelhouse / f"{name}-{version}-py3-none-any.whl", "w") as wheel:
            wheel.writestr(f"{name}.py", "")
            wheel.writestr(
                f"{dist_info}/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            )
            wheel.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            wheel.writestr(f"{dist_info}/RECORD", "")
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("example==1.0.0\n")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_OFFLINE", "true")
    monkeypatch.setenv("UV_FIND_LINKS", str(wheelhouse))
    for constraint, expected in ((constraints, "1.0.0"), (None, "2.0.0")):
        environment = tmp_path / expected
        venv.EnvBuilder(symlinks=True).create(environment)
        HARNESS.install_dependencies(
            environment=environment,
            consumer=tmp_path,
            packages=[HARNESS.WorkspacePackage("phlo", tmp_path, ("example>=1",), {})],
            wheelhouse=wheelhouse,
            constraints=constraint,
        )
        inventory = json.loads(
            subprocess.check_output(
                [
                    str(HARNESS.executable(environment, "python")),
                    "-c",
                    "import json; from importlib.metadata import version; "
                    "print(json.dumps({name:version(name) for name in ('example','phlo')}))",
                ]
            )
        )
        assert inventory == {"example": expected, "phlo": "1.0.0"}


def test_health_shard_marks_a_service_without_a_healthcheck_not_applicable(tmp_path: Path) -> None:
    results = HARNESS.health_shard(
        {"services": {"generated": {"build": {"context": "."}}}},
        consumer=tmp_path,
        environment=tmp_path / "environment",
        shard_index=0,
        shard_count=1,
        env={},
    )

    assert results == [
        {"service": "generated", "status": "not_applicable", "detail": "no healthcheck"}
    ]


@pytest.mark.parametrize(
    ("requested", "missing"),
    [(["api", "worker"], None), (["api", "worker"], "worker"), ([], None)],
)
def test_batch_render_preserves_service_entries_and_scopes_new_files(
    tmp_path, monkeypatch, requested, missing
):
    import yaml

    phlo = tmp_path / "bin/phlo"
    state = tmp_path / ".phlo"
    batches = []
    original_run = HARNESS._run

    def run_installed_cli(command, *, cwd, env):
        if command[0] != str(phlo):
            return original_run(command, cwd=cwd, env=env)
        if command[2] == "init":
            state.mkdir()
            (state / "initial.txt").write_text("initial artifact")
            (state / "docker-compose.yml").write_text("services: {}\n")
            return ""
        if command[2] != "add" or "--no-start" not in command:
            raise AssertionError("The renderer must add services without starting containers")
        names = [command[index + 1] for index, token in enumerate(command) if token == "--service"]
        batches.append(names)
        entries = {}
        for name in names:
            if name != missing:
                (state / f"{name}.txt").write_text(name)
                entries[name] = {"image": f"example/{name}:1"}
        (state / "docker-compose.yml").write_text(yaml.safe_dump({"services": entries}))
        return ""

    monkeypatch.setattr(HARNESS, "_run", run_installed_cli)
    rendered, compose, rendering = HARNESS.render_services(
        phlo, Path(sys.prefix), tmp_path, [{"name": name} for name in requested], {}
    )

    assert batches == ([requested] if requested else [])
    assert rendered == [
        {
            "service": name,
            "compose_service": None if name == missing else {"image": f"example/{name}:1"},
        }
        for name in requested
    ]
    assert set(compose["services"]) == set(requested) - {missing}
    assert rendering == {
        "scope": "batch_service_add",
        "generated_files": sorted(f".phlo/{name}.txt" for name in requested if name != missing),
    }


@pytest.fixture
def family_compose():
    """Provide interleaved shared images and a dependency without a local build."""
    minio_build = {"context": "./minio", "dockerfile": "Dockerfile"}
    dagster_build = {"context": ".", "dockerfile": "dagster/Dockerfile"}
    healthcheck = {"test": ["CMD", "true"]}
    return {
        "services": {
            "minio": {"image": "example/minio:1", "build": minio_build, "healthcheck": healthcheck},
            "api": {"image": "example/api:1", "build": "./api", "healthcheck": healthcheck},
            "minio-setup": {
                "image": "example/minio:1",
                "build": dict(reversed(list(minio_build.items()))),
                "command": ["setup"],
            },
            "observatory": {"image": "example/ui:1", "build": "./ui", "healthcheck": healthcheck},
            "dagster": {
                "image": "example/dagster:1",
                "build": dagster_build,
                "healthcheck": healthcheck,
            },
            "dagster-daemon": {"image": "example/dagster:1", "build": dagster_build},
            "postgres": {"image": "postgres:16", "healthcheck": healthcheck},
        }
    }


@pytest.mark.parametrize(
    "shard_count,expected",
    [
        (1, [["minio", "minio-setup", "api", "observatory", "dagster", "dagster-daemon"]]),
        (2, [["minio", "minio-setup", "api"], ["observatory", "dagster", "dagster-daemon"]]),
        (3, [["minio", "minio-setup", "api"], ["observatory"], ["dagster", "dagster-daemon"]]),
        (4, [["minio", "minio-setup"], ["api"], ["observatory"], ["dagster", "dagster-daemon"]]),
        (
            5,
            [["minio", "minio-setup"], ["api"], ["observatory"], ["dagster", "dagster-daemon"], []],
        ),
    ],
)
def test_image_families_form_complete_disjoint_shards(family_compose, shard_count, expected):
    selected = [
        HARNESS.select_buildable_services(
            family_compose, shard_index=index, shard_count=shard_count
        )
        for index in range(shard_count)
    ]
    names = [[name for name, _config in shard] for shard in selected]

    assert names == expected
    flattened = [name for shard in names for name in shard]
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == {
        name for name, config in family_compose["services"].items() if config.get("build")
    }


@pytest.mark.parametrize("shard_index,shard_count", [(-1, 2), (2, 2), (0, 0), (0, -1)])
def test_image_family_selection_rejects_invalid_shards(family_compose, shard_index, shard_count):
    with pytest.raises(ValueError, match="docker shard index must be within docker shard count"):
        HARNESS.select_buildable_services(
            family_compose, shard_index=shard_index, shard_count=shard_count
        )


@pytest.mark.parametrize(
    "difference",
    [
        {"context": "./other"},
        {"dockerfile": "Other.Dockerfile"},
        {"args": {"VERSION": "2"}},
        {"target": "development"},
    ],
)
def test_same_image_with_different_build_inputs_keeps_separate_families(difference):
    build = {
        "context": ".",
        "dockerfile": "Dockerfile",
        "args": {"VERSION": "1"},
        "target": "runtime",
    }
    config = {"image": "example/app:1", "build": build}
    compose = {
        "services": {
            "first": config,
            "second": {"image": "example/app:1", "build": {**build, **difference}},
            "alias": {"image": "example/app:1", "build": dict(reversed(list(build.items())))},
            "other-image": {"image": "example/other:1", "build": build},
        }
    }

    selected = [
        [
            name
            for name, _config in HARNESS.select_buildable_services(
                compose, shard_index=index, shard_count=3
            )
        ]
        for index in range(3)
    ]

    assert selected == [["first", "alias"], ["second"], ["other-image"]]


@pytest.mark.parametrize("shard_count", [1, 2, 4, 5])
def test_family_shards_retain_every_build_and_health_result_after_failures(
    tmp_path, monkeypatch, family_compose, shard_count
):
    import json
    import subprocess
    from collections import Counter

    operations = []
    monkeypatch.setattr(
        HARNESS, "_run", lambda *_args, **_kwargs: json.dumps(["docker", "compose"])
    )

    def run_container(command, **_kwargs):
        operation = command[2]
        service = command[-1] if operation != "down" else None
        operations.append((operation, service))
        failed = (operation, service) in {("build", "minio"), ("up", "dagster")}
        return subprocess.CompletedProcess(
            command, int(failed), f"{service} logs", "failure" if failed else ""
        )

    monkeypatch.setattr(HARNESS.subprocess, "run", run_container)
    builds = []
    health = []
    for index in range(shard_count):
        arguments = {
            "consumer": tmp_path,
            "environment": tmp_path / "environment",
            "shard_index": index,
            "shard_count": shard_count,
            "env": {},
        }
        builds.extend(HARNESS.build_shard(family_compose, **arguments))
        health.extend(HARNESS.health_shard(family_compose, **arguments))

    buildable = {
        name: config for name, config in family_compose["services"].items() if config.get("build")
    }
    assert Counter(entry["service"] for entry in builds) == Counter(dict.fromkeys(buildable, 1))
    assert Counter(entry["service"] for entry in health) == Counter(dict.fromkeys(buildable, 1))
    assert {entry["service"]: entry["status"] for entry in builds} == {
        name: "failed" if name == "minio" else "passed" for name in buildable
    }
    assert {entry["service"]: entry["status"] for entry in health} == {
        name: "failed"
        if name == "dagster"
        else "passed"
        if config.get("healthcheck")
        else "not_applicable"
        for name, config in buildable.items()
    }
    expected_probes = Counter(
        {name: 1 for name, config in buildable.items() if config.get("healthcheck")}
    )
    assert (
        Counter(service for operation, service in operations if operation == "up")
        == expected_probes
    )
    assert (
        Counter(service for operation, service in operations if operation == "logs")
        == expected_probes
    )
    assert operations.count(("down", None)) == sum(expected_probes.values())


def test_build_evidence_preserves_early_errors_and_failed_gate(
    tmp_path, monkeypatch, family_compose
):
    """Keep the original cause even when a later build of the same image succeeds."""
    import json
    import subprocess

    services = [{"name": name} for name in family_compose["services"]]
    rendered = [
        {"service": name, "compose_service": config}
        for name, config in family_compose["services"].items()
    ]
    stand_ins = {
        "workspace_packages": [],
        "build_wheelhouse": {},
        "clean_environment": (tmp_path / "environment", tmp_path / "consumer"),
        "install_dependencies": None,
        "installed_distributions": {},
        "_run": "",
        "render_services": (rendered, family_compose, {}),
        "prepare_container_wheelhouse": {},
        "module_locations": {},
        "installed_compose_command": ["docker", "compose"],
    }
    for name, value in stand_ins.items():
        monkeypatch.setattr(HARNESS, name, lambda *args, value=value, **kwargs: value)
    monkeypatch.setattr(
        HARNESS,
        "parse_phlo_json_command",
        lambda command, **kwargs: services if command[1:3] == ["services", "list"] else {},
    )
    timestamps = iter([1000.0, 1300.0, 2000.0, 2007.0, 3000.0, 3002.0])
    monkeypatch.setattr(HARNESS, "monotonic", lambda: next(timestamps))
    stdout = "early stdout root cause\n" + "build progress\n" * 1000
    stderr = "Dockerfile:21\n" + "build diagnostic\n" * 1000

    def run_container(command, **kwargs):
        failed = command[-1] == "minio"
        return subprocess.CompletedProcess(
            command, int(failed), stdout if failed else "built", stderr if failed else ""
        )

    monkeypatch.setattr(HARNESS.subprocess, "run", run_container)
    output = tmp_path / "provider-artifacts.json"
    status = HARNESS.main(
        ["--output", str(output), "--include-docker-builds", "--docker-shard-count", "2"]
    )
    report = json.loads(output.read_text())

    assert status == 1
    assert report["checks"]["failed_builds"] == ["minio"]
    assert [entry["status"] for entry in report["builds"]] == ["failed", "passed", "passed"]
    assert [entry["elapsed_seconds"] for entry in report["builds"]] == [300.0, 7.0, 2.0]
    failed_build = report["builds"][0]
    assert failed_build["stdout"] == stdout
    assert failed_build["stderr"] == stderr
    assert failed_build["detail"] == stderr[-4000:]
    assert "root cause" not in failed_build["detail"]
    assert "Dockerfile:21" not in failed_build["detail"]


def test_phlo_service_inventory_unwraps_result_before_rendering(tmp_path, monkeypatch):
    """The artifact lane must iterate service records, not the envelope's field names."""
    import json

    from phlo.cli.output import json_envelope

    services = [{"name": "postgres", "description": "Database", "core": False}]
    envelope = json.loads(json_envelope(data=services))
    envelope["exit_code"] = 0
    monkeypatch.setattr(HARNESS, "_run", lambda *_args, **_kwargs: json.dumps(envelope))

    records = HARNESS.parse_phlo_json_command(
        ["phlo", "services", "list", "--all", "--json"], cwd=tmp_path, env={}
    )
    assert [service["name"] for service in records] == ["postgres"]
    assert records == services


def test_native_json_document_is_not_unwrapped(tmp_path, monkeypatch):
    """Compose YAML loaded through Python is a document, not a Phlo CLI response."""
    import json

    document = {"services": {"postgres": {"image": "postgres:16"}}}
    monkeypatch.setattr(HARNESS, "_run", lambda *_args, **_kwargs: json.dumps(document))
    assert HARNESS.parse_json_command(["python", "-c", "..."], cwd=tmp_path, env={}) == document


def test_all_container_operations_use_installed_compose_layers(tmp_path, monkeypatch):
    """Host identity and env interpolation must survive build and health operations."""
    import json
    import subprocess

    import yaml

    from phlo.infrastructure.container_backend import _compose_base_cmd

    consumer = tmp_path / "external consumer"
    state = consumer / ".phlo"
    (state / "overrides").mkdir(parents=True)
    (state / "secrets").mkdir()
    (state / "overrides/.env").write_text("PHLO_VERSION=9.8.7\n")
    (state / "secrets/.env").write_text("PASSWORD=test-only\n")
    (state / "overrides/compose.host.yaml").write_text(
        'services:\n  phlo-api:\n    user: "1234:5678"\n'
    )
    compose = {"services": {"phlo-api": {"build": ".", "healthcheck": {"test": ["CMD", "true"]}}}}
    (state / "docker-compose.yml").write_text(yaml.safe_dump(compose))
    environment = tmp_path / "installed environment"
    operations = []
    child_calls = []
    clean_env = {"PHLO_ENVIRONMENT": "development"}

    def run_child(command, *, cwd, env):
        child_calls.append(command)
        assert command[0] == str(HARNESS.executable(environment, "python"))
        assert cwd == consumer
        assert env == clean_env
        # Stand in for the installed interpreter using the same runtime builder;
        # the harness must not construct a separate, incomplete Compose command.
        return json.dumps(
            _compose_base_cmd(binary="docker", phlo_dir=state, project_name="installed-artifacts")
        )

    def run_container(command, **kwargs):
        files = [Path(command[i + 1]) for i, token in enumerate(command) if token == "-f"]
        effective = {}
        for file in files:
            effective.update(yaml.safe_load(file.read_text())["services"]["phlo-api"])
        assert effective["user"] == "1234:5678"
        env_files = [
            Path(command[i + 1]) for i, token in enumerate(command) if token == "--env-file"
        ]
        assert [path.read_text() for path in env_files] == [
            "PHLO_VERSION=9.8.7\n",
            "PASSWORD=test-only\n",
        ]
        assert kwargs["env"] == clean_env
        operations.append(
            next(token for token in command if token in {"build", "up", "logs", "down"})
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(HARNESS, "_run", run_child)
    monkeypatch.setattr(HARNESS.subprocess, "run", run_container)
    for run_shard in (HARNESS.build_shard, HARNESS.health_shard):
        results = run_shard(
            compose,
            environment=environment,
            consumer=consumer,
            shard_index=0,
            shard_count=1,
            env=clean_env,
        )
        assert results[0]["status"] == "passed"
    assert operations == ["build", "up", "logs", "down"]
    assert child_calls


def test_compose_builder_runs_in_isolated_installed_interpreter(tmp_path, monkeypatch):
    """A consumer/source shadow cannot replace the installed builder subprocess."""
    import subprocess
    import venv

    environment = tmp_path / "environment"
    venv.EnvBuilder(symlinks=True).create(environment)
    python = HARNESS.executable(environment, "python")
    site = Path(
        subprocess.check_output(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
    )
    package = site / "phlo/cli/infrastructure"
    package.mkdir(parents=True)
    for parent in (package, package.parent, package.parent.parent):
        (parent / "__init__.py").write_text("")
    (package / "container_backend.py").write_text(
        "def _compose_base_cmd(**kwargs):\n"
        "    return ['docker', 'compose', '-p', kwargs['project_name'], '-f', str(kwargs['phlo_dir'] / 'installed.yaml')]\n"
    )
    (package / "utils.py").write_text("def get_project_name():\n    return 'installed-consumer'\n")
    services = package.parent / "commands/services"
    services.mkdir(parents=True)
    for parent in (services, services.parent):
        (parent / "__init__.py").write_text("")
    (services / "utils.py").write_text(
        "def prepare_api_runtime_directories(phlo_dir, service_names):\n"
        "    assert service_names == ['phlo-api']\n"
        "    phlo_dir.mkdir()\n"
        "    (phlo_dir / 'prepared').touch()\n"
    )
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "phlo.py").write_text("raise RuntimeError('source fallback must not import')\n")
    monkeypatch.setenv("PYTHONPATH", str(consumer))
    monkeypatch.setenv("PHLO_DEV_SOURCE", str(consumer))
    command = HARNESS.installed_compose_command(
        environment=environment, consumer=consumer, env=HARNESS.external_environment()
    )
    assert command == [
        "docker",
        "compose",
        "-p",
        "installed-consumer",
        "-f",
        str(consumer / ".phlo/installed.yaml"),
    ]
    assert (consumer / ".phlo/prepared").is_file()
