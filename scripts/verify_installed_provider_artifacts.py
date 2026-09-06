#!/usr/bin/env python3
"""Verify provider wheels work only from an installed external consumer
environment.

Builds all workspace wheels, installs them into a clean uv environment with
repository sources stripped from the path, renders services from the
installed CLI, and checks that declared entry points and modules resolve
from installed distributions only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkspacePackage:
    """Workspace package metadata: name, path, dependencies, and entry points."""

    name: str
    path: Path
    dependencies: tuple[str, ...]
    entry_points: dict[str, dict[str, str]]


def canonicalize_name(name: str) -> str:
    """Return the canonical (lowercase, hyphen-normalized) distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """Return the bare distribution name from a requirement string."""
    return re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0]


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.run(
        command, cwd=cwd, env=env, check=True, text=True, capture_output=True
    ).stdout


def workspace_packages(repo_root: Path) -> list[WorkspacePackage]:
    """Collect WorkspacePackage metadata for the root and every packages/* member."""
    paths = [
        repo_root / "pyproject.toml",
        *sorted((repo_root / "packages").glob("*/pyproject.toml")),
    ]
    packages: list[WorkspacePackage] = []
    for path in paths:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        project = document["project"]
        packages.append(
            WorkspacePackage(
                name=project["name"],
                path=path.parent,
                dependencies=tuple(project.get("dependencies", [])),
                entry_points={
                    group: dict(entries)
                    for group, entries in project.get("entry-points", {}).items()
                },
            )
        )
    return packages


def wheel_inventory(wheelhouse: Path) -> dict[str, Path]:
    """Map canonical wheel names to paths, rejecting duplicate wheels."""
    wheels: dict[str, Path] = {}
    for wheel in wheelhouse.glob("*.whl"):
        normalized = canonicalize_name(wheel.name.split("-", maxsplit=1)[0])
        if normalized in wheels:
            raise ValueError(f"duplicate wheel for {normalized}: {wheels[normalized]} and {wheel}")
        wheels[normalized] = wheel
    return wheels


def build_wheelhouse(repo_root: Path, wheelhouse: Path) -> dict[str, Path]:
    """Build all workspace wheels with uv into ``wheelhouse`` and return the inventory."""
    wheelhouse.mkdir(parents=True, exist_ok=True)
    _run(["uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheelhouse)], cwd=repo_root)
    return wheel_inventory(wheelhouse)


def clean_environment(base: Path) -> tuple[Path, Path]:
    """Create a fresh Python 3.11 venv and consumer project directory under ``base``."""
    environment = base / "environment"
    consumer = base / "consumer"
    _run(["uv", "venv", str(environment), "--python", "3.11"], cwd=base)
    consumer.mkdir()
    return environment, consumer


def executable(environment: Path, name: str) -> Path:
    """Return the platform-correct path to an executable inside the venv."""
    return (
        environment
        / ("Scripts" if os.name == "nt" else "bin")
        / (f"{name}.exe" if os.name == "nt" else name)
    )


# Strip any variable that could let the consumer import repository sources;
# everything must resolve from installed distributions.
def external_environment() -> dict[str, str]:
    """Drop PYTHONPATH and PHLO_DEV_SOURCE so imports resolve to installed wheels only."""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PHLO_DEV_SOURCE", None)
    return environment


def install_dependencies(
    *,
    environment: Path,
    consumer: Path,
    packages: list[WorkspacePackage],
    wheelhouse: Path,
    constraints: Path | None = None,
) -> None:
    """Install every workspace wheel plus external dependencies into the clean venv."""
    workspace_names = {canonicalize_name(package.name) for package in packages}
    requirements = sorted(
        {
            dependency
            for package in packages
            for dependency in package.dependencies
            if canonicalize_name(requirement_name(dependency)) not in workspace_names
        }
    )
    python = executable(environment, "python")
    if requirements:
        constraint_args = ["--constraint", str(constraints)] if constraints else []
        _run(
            ["uv", "pip", "install", "--python", str(python), *constraint_args, *requirements],
            cwd=consumer,
        )
    # Third-party dependencies first, from the normal index. Workspace packages
    # then install with --no-index/--no-deps so they can only come from the
    # built wheels and cannot pull a same-named package from PyPI.
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-index",
            "--no-deps",
            "--reinstall",
            "--find-links",
            str(wheelhouse),
            *(package.name for package in packages),
        ],
        cwd=consumer,
    )


def installed_distributions(environment: Path) -> dict[str, str]:
    """Map installed distribution names to install locations per the child interpreter."""
    script = """import json
from importlib.metadata import distributions
print(json.dumps({d.metadata['Name']: str(d.locate_file('')) for d in distributions() if d.metadata.get('Name')}))
"""
    result = _run([str(executable(environment, "python")), "-c", script], cwd=environment)
    # This function intentionally returns paths instead of host metadata objects: the child interpreter
    # is the authority for the clean environment.
    return json.loads(result)


def assert_installed_artifacts(
    *,
    packages: list[WorkspacePackage],
    wheelhouse: dict[str, Path],
    installed: dict[str, str],
    repo_root: Path,
) -> dict[str, list[str]]:
    """Report missing packages, missing wheels, and any editable installs leaking repo sources."""
    missing_packages = [
        package.name
        for package in packages
        if canonicalize_name(package.name) not in {canonicalize_name(x) for x in installed}
    ]
    missing_wheels = [
        package.name for package in packages if canonicalize_name(package.name) not in wheelhouse
    ]
    editable = [name for name, location in installed.items() if str(repo_root) in location]
    return {
        "missing_packages": missing_packages,
        "missing_wheels": missing_wheels,
        "editable_installs": editable,
    }


def parse_json_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> Any:
    """Run a command in the clean environment and parse its stdout as JSON."""
    return json.loads(_run(command, cwd=cwd, env=env))


def read_yaml(environment: Path, path: Path, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    """Load a YAML file via the clean environment's Python interpreter."""
    script = "import json, pathlib, yaml; print(json.dumps(yaml.safe_load(pathlib.Path(__import__('sys').argv[1]).read_text()) or {}))"
    return parse_json_command(
        [str(executable(environment, "python")), "-c", script, str(path)], cwd=cwd, env=env
    )


def files_changed(root: Path, before: set[Path]) -> list[str]:
    """Return files created under ``root`` after the snapshot ``before``, relative to it."""
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path not in before
    )


def render_services(
    phlo: Path,
    environment: Path,
    consumer: Path,
    services: list[dict[str, Any]],
    env: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Render each service into the consumer project, capturing files and compose output."""
    _run([str(phlo), "services", "init", "--no-dev"], cwd=consumer, env=env)
    rendered: list[dict[str, Any]] = []
    for service in services:
        before = set(consumer.rglob("*"))
        _run(
            [str(phlo), "services", "add", "--service", service["name"], "--no-start"],
            cwd=consumer,
            env=env,
        )
        compose = read_yaml(
            environment, consumer / ".phlo" / "docker-compose.yml", cwd=consumer, env=env
        )
        rendered.append(
            {
                "service": service["name"],
                "generated_files": files_changed(consumer, before),
                "compose_service": (compose.get("services") or {}).get(service["name"]),
            }
        )
    compose_path = consumer / ".phlo" / "docker-compose.yml"
    compose = (
        read_yaml(environment, compose_path, cwd=consumer, env=env) if compose_path.exists() else {}
    )
    return rendered, compose or {}


def module_locations(environment: Path) -> dict[str, str]:
    """Map top-level module names to their defining file, as seen by the venv interpreter."""
    script = """import importlib.metadata as m, importlib.util, json
result = {}
for distribution in m.distributions():
    name = distribution.metadata.get('Name')
    if not name: continue
    top = (distribution.read_text('top_level.txt') or '').splitlines()
    for module in top:
        spec = importlib.util.find_spec(module)
        if spec and spec.origin: result[module] = spec.origin
print(json.dumps(result))
"""
    return json.loads(_run([str(executable(environment, "python")), "-c", script], cwd=environment))


def prepare_container_wheelhouse(wheelhouse: Path, consumer: Path) -> dict[str, str]:
    """Make the built wheels available to generated Dockerfiles, never repository sources."""
    destination = consumer / ".phlo" / "wheelhouse"
    shutil.copytree(wheelhouse, destination, dirs_exist_ok=True)
    # The generated API container runs as an unprivileged user and bind-mounts this
    # externally owned diagnostic directory. It must be writable for the health probe.
    logs = consumer / ".phlo" / "logs"
    logs.mkdir(exist_ok=True)
    logs.chmod(0o777)
    for log in logs.rglob("*"):
        if log.is_file():
            log.chmod(0o666)
    environment = external_environment()
    environment["PHLO_WHEELHOUSE"] = "installed-artifacts"
    return environment


def build_shard(
    compose: dict[str, Any],
    *,
    consumer: Path,
    shard_index: int,
    shard_count: int,
    env: dict[str, str],
) -> list[dict[str, Any]]:
    """Docker-compose-build this shard's buildable services and report pass/fail per service."""
    buildable = [
        (name, config)
        for name, config in (compose.get("services") or {}).items()
        if config.get("build")
    ]
    selected = buildable[shard_index::shard_count]
    results = []
    for name, config in selected:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(consumer / ".phlo" / "docker-compose.yml"),
                "build",
                name,
            ],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
        )
        results.append(
            {
                "service": name,
                "build": config["build"],
                "status": "passed" if completed.returncode == 0 else "failed",
                "detail": completed.stderr[-4000:],
            }
        )
    return results


def health_shard(
    compose: dict[str, Any],
    *,
    consumer: Path,
    shard_index: int,
    shard_count: int,
    env: dict[str, str],
) -> list[dict[str, Any]]:
    """Exercise declared container health checks without treating them as runtime acceptance."""
    buildable = [
        (name, config)
        for name, config in (compose.get("services") or {}).items()
        if config.get("build")
    ]
    results = []
    compose_file = consumer / ".phlo" / "docker-compose.yml"
    for name, config in buildable[shard_index::shard_count]:
        if not config.get("healthcheck"):
            results.append(
                {"service": name, "status": "not_applicable", "detail": "no healthcheck"}
            )
            continue
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "180",
                name,
            ],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
        )
        logs = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "logs", "--no-color", name],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down", "--volumes"],
            cwd=consumer,
            env=env,
            text=True,
            capture_output=True,
        )
        results.append(
            {
                "service": name,
                "status": "passed" if completed.returncode == 0 else "failed",
                "detail": f"{completed.stderr}\n{logs.stdout}\n{logs.stderr}"[-8000:],
            }
        )
    return results


def verify(args: argparse.Namespace) -> dict[str, Any]:
    """Run the full installed-artifact verification flow and return the summary report."""
    repo_root = Path(args.repo_root).resolve()
    packages = workspace_packages(repo_root)
    temporary = Path(tempfile.mkdtemp(prefix="phlo-installed-artifacts-"))
    try:
        wheelhouse_path = temporary / "wheelhouse"
        if getattr(args, "wheelhouse", None):
            shutil.copytree(Path(args.wheelhouse).resolve(), wheelhouse_path)
            wheelhouse = wheel_inventory(wheelhouse_path)
        else:
            wheelhouse = build_wheelhouse(repo_root, wheelhouse_path)
        environment, consumer = clean_environment(temporary)
        install_dependencies(
            environment=environment,
            consumer=consumer,
            packages=packages,
            wheelhouse=wheelhouse_path,
            constraints=Path(args.constraints).resolve()
            if getattr(args, "constraints", None)
            else None,
        )
        installed = installed_distributions(environment)
        checks = assert_installed_artifacts(
            packages=packages, wheelhouse=wheelhouse, installed=installed, repo_root=repo_root
        )
        env = external_environment()
        phlo = executable(environment, "phlo")
        _run([str(phlo), "init", ".", "--template", "csv-batch", "--force"], cwd=consumer, env=env)
        plugin_check = parse_json_command(
            [str(phlo), "plugin", "check", "--json"], cwd=consumer, env=env
        )
        services = parse_json_command(
            [str(phlo), "services", "list", "--all", "--json"], cwd=consumer, env=env
        )
        rendered, compose = render_services(phlo, environment, consumer, services, env)
        container_env = prepare_container_wheelhouse(temporary / "wheelhouse", consumer)
        expected_services = {entry["name"] for entry in services}
        rendered_services = {entry["service"] for entry in rendered}
        checks["missing_services"] = sorted(expected_services - rendered_services)
        checks["duplicate_services"] = sorted(
            name for name in expected_services if sum(x["service"] == name for x in rendered) != 1
        )
        checks["repo_source_imports"] = sorted(
            module
            for module, path in module_locations(environment).items()
            if str(repo_root) in path
        )
        checks["unattributed_artifacts"] = [
            entry["service"] for entry in rendered if entry["compose_service"] is None
        ]
        images = [
            {"service": name, "image": value.get("image"), "build": value.get("build")}
            for name, value in (compose.get("services") or {}).items()
        ]
        builds = (
            build_shard(
                compose,
                consumer=consumer,
                shard_index=args.docker_shard_index,
                shard_count=args.docker_shard_count,
                env=container_env,
            )
            if args.include_docker_builds
            else []
        )
        checks["failed_builds"] = [
            entry["service"] for entry in builds if entry["status"] != "passed"
        ]
        health = (
            health_shard(
                compose,
                consumer=consumer,
                shard_index=args.docker_shard_index,
                shard_count=args.docker_shard_count,
                env=container_env,
            )
            if args.include_docker_health
            else []
        )
        checks["failed_health_checks"] = [
            entry["service"] for entry in health if entry["status"] == "failed"
        ]
        return {
            "packages": [
                {
                    "name": package.name,
                    "wheel": wheelhouse.get(canonicalize_name(package.name), Path()).name,
                    "entry_points": package.entry_points,
                }
                for package in packages
            ],
            "services": services,
            "plugin_check": plugin_check,
            "rendered_services": rendered,
            "images": images,
            "builds": builds,
            "health": health,
            "checks": checks,
            "acceptance": {
                "runtime": "not asserted; container health evidence is not runtime acceptance",
                "security": "not asserted; this lane does not run a pinned scanner",
            },
        }
    finally:
        if not args.keep_temp:
            shutil.rmtree(temporary, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run verification; return the process exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-docker-builds", action="store_true")
    parser.add_argument("--include-docker-health", action="store_true")
    parser.add_argument("--wheelhouse", type=Path, help="Reuse wheels built for this exact CI run")
    parser.add_argument("--constraints", type=Path, help="Lock external dependencies for CI checks")
    parser.add_argument("--docker-shard-index", type=int, default=0)
    parser.add_argument("--docker-shard-count", type=int, default=1)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.docker_shard_index < args.docker_shard_count:
        parser.error("docker shard index must be within docker shard count")
    report: dict[str, Any]
    try:
        report = verify(args)
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as error:
        report = {"error": str(error), "checks": {"harness_error": [str(error)]}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checks = report["checks"]
    failed = {name: value for name, value in checks.items() if value}
    if failed:
        print(json.dumps(failed, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
