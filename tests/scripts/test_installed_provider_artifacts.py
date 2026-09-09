"""Focused contracts for the installed-provider artifact harness.

Loads scripts/verify_installed_provider_artifacts.py directly via importlib
and locks its workspace inventory, external-environment scrubbing, missing
artifact reporting, and healthcheck shard behavior.
"""

import importlib.util
import sys
from pathlib import Path

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

    from phlo.cli.infrastructure.container_backend import _compose_base_cmd

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
