"""Plugin validation command.

Validates installed plugins by generating a disposable project and checking
only its generated files: hadolint and Trivy run as digest-pinned container
images so results stay reproducible, remote images are resolved to immutable
manifest digests before scanning, and tool output is captured at bounded
size. Trivy HIGH/CRITICAL findings can be waived only per exact finding
fingerprint.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click
from rich.text import Text

from phlo.cli.commands.plugin.utils import console
from phlo.logging import get_logger
from phlo.plugins import discover_plugins, validate_plugins
from phlo.plugins.base.service import ServicePlugin
from phlo.plugins.discovery import ServiceDiscovery
from phlo.plugins.discovery._service_loading import resolve_plugin_source_path

logger = get_logger(__name__)

# Scanner tools are pinned by digest, not tag: results stay reproducible and
# a moved tag can never change what these checks run.
HADOLINT_IMAGE = (
    "hadolint/hadolint@sha256:27086352fd5e1907ea2b934eb1023f217c5ae087992eb59fde121dce9c9ff21e"
)
TRIVY_IMAGE = (
    "aquasec/trivy@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
MAX_TOOL_OUTPUT_CHARS = 64 * 1024
MAX_TRIVY_JSON_CHARS = 8 * 1024 * 1024
MAX_COMPOSE_CONFIG_CHARS = 4 * 1024 * 1024
_REAL_SUBPROCESS_RUN = subprocess.run


class ContainerCheckError(RuntimeError):
    """Raised when generated container checks cannot complete."""


@dataclass(frozen=True)
class VulnerabilityWaiver:
    """A waiver bound to one exact normalized Trivy finding set."""

    evidence_sha256: str
    reason: str


def _plugin_package(plugin: Any) -> str:
    """Resolve the installed distribution owning a discovered service plugin."""
    top_level = plugin.__class__.__module__.split(".", 1)[0]
    distributions = importlib.metadata.packages_distributions().get(top_level, [])
    return sorted(distributions)[0] if distributions else plugin.metadata.name


def _service_inventory() -> tuple[dict[str, str], list[str]]:
    """Return generated service-file owners and all currently installed service names."""
    owners: dict[str, str] = {}
    service_names: list[str] = []
    package_roots: dict[Path, str] = {}
    discovered = discover_plugins(plugin_type="service", auto_register=True)
    for discovered_plugin in discovered.get("service", []):
        if isinstance(discovered_plugin, ServicePlugin):
            plugin = discovered_plugin
        elif not hasattr(discovered_plugin, "service_definition") or not callable(
            getattr(discovered_plugin, "get_files", None)
        ):
            continue
        else:
            plugin = cast(ServicePlugin, discovered_plugin)
        package = _plugin_package(plugin)
        source_path = resolve_plugin_source_path(plugin)
        if source_path:
            package_roots[source_path.resolve()] = package
        service_definition = plugin.service_definition
        service_name = service_definition.get("name")
        if service_name:
            service_names.append(service_name)
        for file_spec in plugin.get_files():
            destination = file_spec.get("dest")
            if destination:
                owners[destination] = package
    for service_name, definition in ServiceDiscovery().discover().items():
        source_path = definition.source_path
        if not source_path:
            continue
        resolved_source = source_path.resolve()
        matching_roots = [
            (len(root.parts), package)
            for root, package in package_roots.items()
            if resolved_source == root or root in resolved_source.parents
        ]
        if matching_roots:
            package = max(matching_roots)[1]
            owners.setdefault(f"@service:{service_name}", package)
            for file_spec in definition.files:
                destination = file_spec.get("dest")
                if destination:
                    owners.setdefault(destination, package)
    return owners, list(dict.fromkeys(service_names))


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    runner: Callable[..., Any],
    label: str,
) -> str | None:
    """Run one external command and return a failure detail, if any."""
    try:
        result = _run_with_capture(command, cwd=cwd, runner=runner)
    except OSError as exc:
        return f"{label} could not start: {exc}"
    if result.returncode:
        return _run_command_result_failure(result, label)
    return None


def _run_output_command(
    command: list[str],
    *,
    cwd: Path,
    runner: Callable[..., Any],
    label: str,
    max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> tuple[str, str]:
    """Run a command and return stdout, raising with both output streams on failure."""
    try:
        result = _run_with_capture(
            command,
            cwd=cwd,
            runner=runner,
            max_output_chars=max_output_chars,
        )
    except OSError as exc:
        raise ContainerCheckError(f"{label} could not start: {exc}") from exc
    if result.returncode:
        failure = _run_command_result_failure(result, label)
        raise ContainerCheckError(failure)
    return result.stdout or "", result.stderr or ""


def _run_command_result_failure(result: Any, label: str) -> str:
    """Format a failed command result without losing either output stream."""
    output = []
    if result.stdout:
        output.append(f"stdout: {_limit_tool_output(result.stdout).strip()}")
    if result.stderr:
        output.append(f"stderr: {_limit_tool_output(result.stderr).strip()}")
    detail = "\n".join(output) or "no output"
    return f"{label} failed with exit code {result.returncode}: {detail}"


def _join_failure_details(*details: str | None) -> str:
    """Keep the context from multiple failed steps in the final report."""
    return "\n".join(detail for detail in details if detail) or "no output"


def _parse_vulnerability_waivers(
    values: tuple[str, ...],
) -> dict[tuple[str, str], VulnerabilityWaiver]:
    """Parse waivers bound to an exact service, image, and finding fingerprint."""
    waivers: dict[tuple[str, str], VulnerabilityWaiver] = {}
    for value in values:
        parts = [part.strip() for part in value.split("=", 3)]
        if len(parts) != 4 or not all(parts):
            raise click.BadParameter(
                "expected SERVICE=IMAGE=EVIDENCE_SHA256=REASON",
                param_hint="--allow-vulnerable-image",
            )
        service, image, evidence_sha256, reason = parts
        if len(evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in evidence_sha256.lower()
        ):
            raise click.BadParameter(
                "EVIDENCE_SHA256 must be a 64-character hexadecimal digest",
                param_hint="--allow-vulnerable-image",
            )
        key = (service, image)
        if key in waivers:
            raise click.BadParameter(
                f"duplicate waiver for {service} using {image}",
                param_hint="--allow-vulnerable-image",
            )
        waivers[key] = VulnerabilityWaiver(evidence_sha256.lower(), reason)
    return waivers


def _run_with_capture(
    command: list[str],
    *,
    cwd: Path,
    runner: Callable[..., Any],
    max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> Any:
    """Run a real command without retaining an unbounded scanner transcript."""
    if runner is _REAL_SUBPROCESS_RUN:
        return _run_bounded_subprocess(
            command,
            cwd=cwd,
            max_output_chars=max_output_chars,
        )
    return runner(command, cwd=cwd, capture_output=True, text=True, check=False)


def _run_bounded_subprocess(
    command: list[str],
    *,
    cwd: Path,
    max_output_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> subprocess.CompletedProcess[str]:
    """Run a command through spooled files and retain useful output at bounded size."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        completed = _REAL_SUBPROCESS_RUN(
            command,
            cwd=cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            check=False,
        )
        return subprocess.CompletedProcess(
            command,
            completed.returncode,
            stdout=_read_bounded_file(stdout_file, max_output_chars=max_output_chars),
            stderr=_read_bounded_file(stderr_file, max_output_chars=max_output_chars),
        )


def _read_bounded_file(file_handle: Any, *, max_output_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Read a file's head and tail without loading a large tool report."""
    file_handle.seek(0, 2)
    size = file_handle.tell()
    if size <= max_output_chars:
        file_handle.seek(0)
        return file_handle.read().decode("utf-8", errors="replace")

    half_limit = max_output_chars // 2
    file_handle.seek(0)
    head = file_handle.read(half_limit)
    file_handle.seek(-half_limit, 2)
    tail = file_handle.read(half_limit)
    return (
        head.decode("utf-8", errors="replace")
        + f"\n... [output truncated; {size} bytes total] ...\n"
        + tail.decode("utf-8", errors="replace")
    )


def _limit_tool_output(value: str) -> str:
    """Bound output from injected runners as well as real subprocesses."""
    if len(value) <= MAX_TOOL_OUTPUT_CHARS:
        return value
    half_limit = MAX_TOOL_OUTPUT_CHARS // 2
    return (
        value[:half_limit]
        + f"\n... [output truncated; {len(value)} characters total] ...\n"
        + value[-half_limit:]
    )


def _trivy_vulnerability_evidence(stdout: str) -> dict[str, Any] | None:
    """Extract exact HIGH/CRITICAL findings from Trivy JSON output."""
    if not stdout.strip():
        return None
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    vulnerable_components: list[dict[str, str]] = []
    for result in report.get("Results") or []:
        for finding in result.get("Vulnerabilities") or []:
            severity = str(finding.get("Severity") or "").upper()
            if severity not in {"HIGH", "CRITICAL"}:
                continue
            vulnerable_components.append(
                {
                    "target": str(result.get("Target") or ""),
                    "class": str(result.get("Class") or ""),
                    "type": str(result.get("Type") or ""),
                    "component": str(finding.get("PkgName") or ""),
                    "installed_version": str(finding.get("InstalledVersion") or ""),
                    "fixed_version": str(finding.get("FixedVersion") or ""),
                    "vulnerability_id": str(finding.get("VulnerabilityID") or ""),
                    "severity": severity,
                }
            )
    return {
        "high_count": sum(finding["severity"] == "HIGH" for finding in vulnerable_components),
        "critical_count": sum(
            finding["severity"] == "CRITICAL" for finding in vulnerable_components
        ),
        "vulnerable_components": vulnerable_components,
    }


def _vulnerability_evidence_sha256(evidence: dict[str, Any]) -> str:
    """Fingerprint the exact vulnerability IDs and components, including duplicates."""
    normalized = sorted(
        [finding["vulnerability_id"], finding["component"]]
        for finding in evidence["vulnerable_components"]
    )
    payload = json.dumps(normalized, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_trivy_image_scan(
    command: list[str],
    *,
    cwd: Path,
    runner: Callable[..., Any],
    label: str,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    """Run Trivy once, preserving failure streams and returning structured findings."""
    try:
        result = _run_with_capture(
            command,
            cwd=cwd,
            runner=runner,
            max_output_chars=MAX_TRIVY_JSON_CHARS,
        )
    except OSError as exc:
        return f"{label} could not start: {exc}", None, False

    evidence = _trivy_vulnerability_evidence(result.stdout or "")
    if result.returncode:
        waivable = bool(
            result.returncode == 1
            and evidence
            and evidence["vulnerable_components"]
            and not (result.stderr or "").strip()
        )
        return _run_command_result_failure(result, label), evidence, waivable
    return (
        None,
        evidence
        or {
            "high_count": 0,
            "critical_count": 0,
            "vulnerable_components": [],
        },
        False,
    )


def _run_checked_command(
    command: list[str],
    *,
    cwd: Path,
    runner: Callable[..., Any],
    label: str,
) -> None:
    """Run one required setup command and raise on failure."""
    failure = _run_command(command, cwd=cwd, runner=runner, label=label)
    if failure:
        raise ContainerCheckError(failure)


def _existing_image_id(
    docker: str,
    image: str,
    *,
    cwd: Path,
    runner: Callable[..., Any],
) -> str | None:
    """Return a local image ID without treating an absent tag as a check failure."""
    try:
        result = _run_with_capture(
            [docker, "image", "inspect", "--format", "{{.Id}}", image],
            cwd=cwd,
            runner=runner,
        )
    except OSError as exc:
        raise ContainerCheckError(f"docker image inspect {image} could not start: {exc}") from exc
    if result.returncode:
        detail = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        if "no such image" in detail:
            return None
        raise ContainerCheckError(
            _run_command_result_failure(result, f"docker image inspect {image}")
        )
    return (result.stdout or "").strip() or None


def _remote_image_reference(
    docker: str,
    image: str,
    *,
    cwd: Path,
    runner: Callable[..., Any],
) -> str:
    """Resolve a registry image tag to an immutable manifest digest without pulling it."""
    if "@sha256:" in image:
        return image
    stdout, _ = _run_output_command(
        [
            docker,
            "buildx",
            "imagetools",
            "inspect",
            "--format",
            "{{json .Manifest.Digest}}",
            image,
        ],
        cwd=cwd,
        runner=runner,
        label=f"docker buildx imagetools inspect {image}",
    )
    try:
        digest = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ContainerCheckError(f"registry returned an invalid digest for {image}") from exc
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ContainerCheckError(f"registry returned an invalid digest for {image}: {digest!r}")
    registry_path, separator, image_name = image.rpartition("/")
    repository_name = image_name.rsplit(":", 1)[0]
    repository = f"{registry_path}/{repository_name}" if separator else repository_name
    return f"{repository}@{digest}"


def _check_remote_service_images(
    *,
    compose_services: dict[str, Any],
    service_owners: dict[str, str],
    docker: str,
    project: Path,
    trivy_cache: Path,
    vulnerability_waivers: dict[tuple[str, str], VulnerabilityWaiver],
    runner: Callable[..., Any],
) -> list[dict[str, Any]]:
    """Resolve and scan every rendered image remotely without building or pulling it."""
    service_results: list[dict[str, Any]] = []
    scan_results: dict[str, tuple[str | None, dict[str, Any] | None, bool]] = {}
    docker_config = Path.home() / ".docker" / "config.json"
    auth_mount = (
        ["-v", f"{docker_config.resolve()}:/root/.docker/config.json:ro"]
        if docker_config.is_file()
        else []
    )
    for service_name, service in compose_services.items():
        package = service_owners.get(service_name)
        if not package:
            raise ContainerCheckError(
                f"generated Compose service '{service_name}' has no package owner"
            )
        image = service.get("image")
        if not image:
            service_results.append(
                {
                    "service": service_name,
                    "package": package,
                    "image": "",
                    "status": "failed",
                    "image_scan": "unavailable",
                    "detail": "remote image scan requires an explicit published image",
                }
            )
            continue
        try:
            image_id = _remote_image_reference(
                docker,
                image,
                cwd=project,
                runner=runner,
            )
        except ContainerCheckError as exc:
            service_results.append(
                {
                    "service": service_name,
                    "package": package,
                    "image": image,
                    "status": "failed",
                    "image_scan": "unavailable",
                    "detail": str(exc),
                }
            )
            continue

        result: dict[str, Any] = {
            "service": service_name,
            "package": package,
            "image": image,
            "image_id": image_id,
            "status": "pending",
            "image_scan": "pending",
        }
        service_results.append(result)
        if image_id not in scan_results:
            scan_results[image_id] = _run_trivy_image_scan(
                [
                    docker,
                    "run",
                    "--rm",
                    *auth_mount,
                    "-v",
                    f"{trivy_cache.resolve()}:/root/.cache/trivy",
                    TRIVY_IMAGE,
                    "image",
                    "--image-src",
                    "remote",
                    "--quiet",
                    "--timeout",
                    "15m",
                    "--exit-code",
                    "1",
                    "--scanners",
                    "vuln",
                    "--severity",
                    "HIGH,CRITICAL",
                    "--format",
                    "json",
                    image_id,
                ],
                cwd=project,
                runner=runner,
                label=f"trivy image {image_id}",
            )
        trivy_failure, evidence, waiver_eligible = scan_results[image_id]
        if evidence is not None:
            result.update(evidence)
            if evidence["vulnerable_components"]:
                result["vulnerability_evidence_sha256"] = _vulnerability_evidence_sha256(evidence)
        waiver = vulnerability_waivers.get((service_name, image))
        waiver_matches = bool(
            waiver and result.get("vulnerability_evidence_sha256") == waiver.evidence_sha256
        )
        if trivy_failure and waiver and waiver_eligible and waiver_matches:
            result["status"] = "waived"
            result["image_scan"] = "waived"
            result["vulnerability_waiver"] = waiver.reason
            result["detail"] = trivy_failure
        else:
            result["status"] = "failed" if trivy_failure else "passed"
            result["image_scan"] = "failed" if trivy_failure else "passed"
            if trivy_failure:
                result["detail"] = trivy_failure
                if waiver and waiver_eligible and not waiver_matches:
                    result["detail"] = _join_failure_details(
                        result["detail"],
                        "vulnerability waiver evidence does not match: "
                        f"expected {waiver.evidence_sha256}, observed "
                        f"{result.get('vulnerability_evidence_sha256', '<none>')}",
                    )
    return service_results


def check_generated_containers(
    *,
    project_parent: Path | None = None,
    service_files: dict[str, str] | None = None,
    service_names: list[str] | None = None,
    vulnerability_waivers: dict[tuple[str, str], VulnerabilityWaiver] | None = None,
    command_runner: Callable[..., Any] | None = None,
    remote_images: bool = False,
) -> dict[str, Any]:
    """Generate a disposable user project and check only its generated files."""
    command_runner = command_runner or subprocess.run
    vulnerability_waivers = vulnerability_waivers or {}
    phlo = shutil.which("phlo")
    docker = shutil.which("docker")
    if not phlo:
        raise ContainerCheckError("required installed CLI 'phlo' is not installed or not on PATH")
    if not docker:
        raise ContainerCheckError("required tool 'docker' is not installed or not on PATH")

    if service_files is None:
        owners, discovered_service_names = _service_inventory()
    else:
        owners = service_files
        discovered_service_names = service_names or []
    with tempfile.TemporaryDirectory(prefix="phlo-container-check-", dir=project_parent) as raw:
        project = Path(raw)
        project.mkdir(exist_ok=True)
        configured_trivy_cache = os.environ.get("PHLO_TRIVY_CACHE_DIR")
        trivy_cache = (
            Path(configured_trivy_cache).expanduser().resolve()
            if configured_trivy_cache
            else project / ".trivy-cache"
        )
        trivy_cache.mkdir(parents=True, exist_ok=True)
        init_command = [phlo, "services", "init", "--no-dev", "--force"]
        _run_checked_command(
            init_command,
            cwd=project,
            runner=command_runner,
            label="phlo services init",
        )
        if discovered_service_names:
            add_command = [phlo, "services", "add"]
            for service_name in discovered_service_names:
                add_command.extend(["--service", service_name])
            add_command.append("--no-start")
            _run_checked_command(
                add_command,
                cwd=project,
                runner=command_runner,
                label="phlo services add",
            )

        generated_root = project / ".phlo"
        dockerfiles = (
            sorted(path for path in generated_root.rglob("Dockerfile") if path.is_file())
            if generated_root.exists()
            else []
        )
        relative_dockerfiles = [str(path.relative_to(generated_root)) for path in dockerfiles]
        unowned = [relative for relative in relative_dockerfiles if relative not in owners]
        if unowned:
            raise ContainerCheckError(
                "generated Dockerfile(s) have no package owner: " + ", ".join(unowned)
            )
        dockerfile_owners = {
            relative: owners[relative] for relative in relative_dockerfiles if relative in owners
        }

        failures: list[dict[str, str]] = []
        if dockerfiles:
            for dockerfile in dockerfiles:
                relative = str(dockerfile.relative_to(generated_root))
                failure = _run_command(
                    [
                        docker,
                        "run",
                        "--rm",
                        "-v",
                        f"{project.resolve()}:/workspace:ro",
                        HADOLINT_IMAGE,
                        "/bin/hadolint",
                        f"/workspace/.phlo/{relative}",
                    ],
                    cwd=project,
                    runner=command_runner,
                    label=f"hadolint {relative}",
                )
                if failure:
                    failures.append(
                        {
                            "tool": "hadolint",
                            "package": dockerfile_owners[relative],
                            "target": relative,
                            "detail": failure,
                        }
                    )

        compose_file = generated_root / "docker-compose.yml"
        compose_command = [
            docker,
            "compose",
            "--profile",
            "*",
            "-f",
            str(compose_file),
            "--project-directory",
            str(generated_root),
            "config",
            "--format",
            "json",
        ]
        compose_stdout, _ = _run_output_command(
            compose_command,
            cwd=project,
            runner=command_runner,
            label="docker compose config",
            max_output_chars=MAX_COMPOSE_CONFIG_CHARS,
        )
        try:
            compose_config = json.loads(compose_stdout)
            compose_services = compose_config["services"]
            compose_project = compose_config["name"]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ContainerCheckError(
                "docker compose config returned invalid service JSON"
            ) from exc

        service_owners = {
            name.removeprefix("@service:"): package
            for name, package in owners.items()
            if name.startswith("@service:")
        }
        service_results: list[dict[str, Any]] = []
        resolved_image_ids: dict[str, str] = {}
        previous_image_ids: dict[str, str | None] = {}
        image_scan_results: dict[str, tuple[str | None, dict[str, Any] | None, bool]] = {}
        builder_name = f"phlo-check-{project.name.rsplit('-', 1)[-1]}"
        uses_local_builds = not remote_images and any(
            service.get("build") for service in compose_services.values()
        )
        builder_created = False
        if uses_local_builds:
            _run_checked_command(
                [
                    docker,
                    "buildx",
                    "create",
                    "--driver",
                    "docker-container",
                    "--name",
                    builder_name,
                ],
                cwd=project,
                runner=command_runner,
                label="docker buildx create",
            )
            builder_created = True

        builder_cleanup_failure: str | None = None
        builder_cache_owner: tuple[str, str] | None = None

        def prune_builder_cache() -> None:
            """Prune the buildx cache and record any cleanup failure for this service."""
            nonlocal builder_cache_owner
            if builder_cache_owner is None:
                return
            package, image = builder_cache_owner
            cache_cleanup_failure = _run_command(
                [
                    docker,
                    "buildx",
                    "prune",
                    "--builder",
                    builder_name,
                    "--force",
                ],
                cwd=project,
                runner=command_runner,
                label="docker buildx prune",
            )
            if cache_cleanup_failure:
                failures.append(
                    {
                        "tool": "docker cleanup",
                        "package": package,
                        "target": image,
                        "detail": cache_cleanup_failure,
                    }
                )
            builder_cache_owner = None

        if remote_images:
            service_results = _check_remote_service_images(
                compose_services=compose_services,
                service_owners=service_owners,
                docker=docker,
                project=project,
                trivy_cache=trivy_cache,
                vulnerability_waivers=vulnerability_waivers,
                runner=command_runner,
            )
        try:
            services_to_build = () if remote_images else compose_services.items()
            for service_name, service in services_to_build:
                prune_builder_cache()
                package = service_owners.get(service_name)
                if not package:
                    raise ContainerCheckError(
                        f"generated Compose service '{service_name}' has no package owner"
                    )
                image = service.get("image")
                locally_built = bool(service.get("build"))
                build_failure: str | None = None
                if locally_built and not image:
                    image = f"{compose_project}-{service_name}"
                if not image:
                    service_results.append(
                        {
                            "service": service_name,
                            "package": package,
                            "image": "",
                            "status": "failed",
                            "image_scan": "unavailable",
                            "detail": "generated service has neither image nor build",
                        }
                    )
                    continue

                image_id = resolved_image_ids.get(image)
                first_resolution = image_id is None
                if first_resolution:
                    try:
                        previous_image_ids[image] = _existing_image_id(
                            docker,
                            image,
                            cwd=project,
                            runner=command_runner,
                        )
                    except ContainerCheckError as exc:
                        service_results.append(
                            {
                                "service": service_name,
                                "package": package,
                                "image": image,
                                "status": "failed",
                                "image_scan": "unavailable",
                                "detail": str(exc),
                            }
                        )
                        continue
                if locally_built and first_resolution:
                    build_failure = _run_command(
                        [
                            docker,
                            "compose",
                            "--profile",
                            "*",
                            "-f",
                            str(compose_file),
                            "--project-directory",
                            str(generated_root),
                            "build",
                            "--builder",
                            builder_name,
                            "--quiet",
                            service_name,
                        ],
                        cwd=project,
                        runner=command_runner,
                        label=f"docker compose build {service_name}",
                    )
                    builder_cache_owner = (package, image)
                if first_resolution:
                    pull_failure = None
                    if not locally_built or build_failure:
                        pull_failure = _run_command(
                            [docker, "pull", image],
                            cwd=project,
                            runner=command_runner,
                            label=f"docker pull {service_name}",
                        )
                    if pull_failure:
                        service_results.append(
                            {
                                "service": service_name,
                                "package": package,
                                "image": image,
                                "status": "failed",
                                "image_scan": "unavailable",
                                "detail": _join_failure_details(build_failure, pull_failure),
                            }
                        )
                        continue
                    try:
                        inspect_stdout, _ = _run_output_command(
                            [docker, "image", "inspect", "--format", "{{.Id}}", image],
                            cwd=project,
                            runner=command_runner,
                            label=f"docker image inspect {service_name}",
                        )
                    except ContainerCheckError as exc:
                        service_results.append(
                            {
                                "service": service_name,
                                "package": package,
                                "image": image,
                                "status": "failed",
                                "image_scan": "unavailable",
                                "detail": _join_failure_details(build_failure, str(exc)),
                            }
                        )
                        continue
                    image_id = inspect_stdout.strip()
                if not image_id:
                    service_results.append(
                        {
                            "service": service_name,
                            "package": package,
                            "image": image,
                            "status": "failed",
                            "image_scan": "unavailable",
                            "detail": _join_failure_details(
                                build_failure, "docker image inspect returned no image ID"
                            ),
                        }
                    )
                    continue

                resolved_image_ids[image] = image_id
                result: dict[str, Any] = {
                    "service": service_name,
                    "package": package,
                    "image": image,
                    "image_id": image_id,
                    "status": "failed" if build_failure else "pending",
                    "image_scan": "pending",
                    **({"detail": build_failure} if build_failure else {}),
                }
                service_results.append(result)
                if image_id not in image_scan_results:
                    image_scan_results[image_id] = _run_trivy_image_scan(
                        [
                            docker,
                            "run",
                            "--rm",
                            "-v",
                            "/var/run/docker.sock:/var/run/docker.sock",
                            "-v",
                            f"{trivy_cache.resolve()}:/root/.cache/trivy",
                            TRIVY_IMAGE,
                            "image",
                            "--quiet",
                            "--timeout",
                            "15m",
                            "--exit-code",
                            "1",
                            "--scanners",
                            "vuln",
                            "--severity",
                            "HIGH,CRITICAL",
                            "--format",
                            "json",
                            image_id,
                        ],
                        cwd=project,
                        runner=command_runner,
                        label=f"trivy image {image_id}",
                    )
                trivy_image_failure, vulnerability_evidence, waiver_eligible = image_scan_results[
                    image_id
                ]
                if vulnerability_evidence is not None:
                    result.update(vulnerability_evidence)
                    if vulnerability_evidence["vulnerable_components"]:
                        result["vulnerability_evidence_sha256"] = _vulnerability_evidence_sha256(
                            vulnerability_evidence
                        )
                waiver = vulnerability_waivers.get((service_name, image))
                build_failed = result["status"] == "failed"
                waiver_matches = bool(
                    waiver and result.get("vulnerability_evidence_sha256") == waiver.evidence_sha256
                )
                if trivy_image_failure and waiver and waiver_eligible and waiver_matches:
                    result["image_scan"] = "waived"
                    result["vulnerability_waiver"] = waiver.reason
                    result["detail"] = _join_failure_details(
                        result.get("detail"), trivy_image_failure
                    )
                    result["status"] = "failed" if build_failed else "waived"
                else:
                    result["status"] = "failed" if trivy_image_failure or build_failed else "passed"
                    result["image_scan"] = "failed" if trivy_image_failure else "passed"
                    if trivy_image_failure:
                        result["detail"] = _join_failure_details(
                            result.get("detail"), trivy_image_failure
                        )
                        if waiver and waiver_eligible and not waiver_matches:
                            result["detail"] = _join_failure_details(
                                result.get("detail"),
                                "vulnerability waiver evidence does not match: "
                                f"expected {waiver.evidence_sha256}, observed "
                                f"{result.get('vulnerability_evidence_sha256', '<none>')}",
                            )

                if first_resolution:
                    previous_image_id = previous_image_ids[image]
                    cleanup_commands: list[tuple[list[str], str]] = []
                    if previous_image_id and previous_image_id != image_id:
                        cleanup_commands.extend(
                            [
                                (
                                    [docker, "image", "tag", previous_image_id, image],
                                    f"docker image restore {service_name}",
                                ),
                                (
                                    [docker, "image", "rm", image_id],
                                    f"docker image rm {service_name}",
                                ),
                            ]
                        )
                    elif previous_image_id is None:
                        cleanup_commands.append(
                            ([docker, "image", "rm", image], f"docker image rm {service_name}")
                        )
                    for cleanup_command, cleanup_label in cleanup_commands:
                        image_cleanup_failure = _run_command(
                            cleanup_command,
                            cwd=project,
                            runner=command_runner,
                            label=cleanup_label,
                        )
                        if not image_cleanup_failure:
                            continue
                        failures.append(
                            {
                                "tool": "docker cleanup",
                                "package": package,
                                "target": image,
                                "detail": image_cleanup_failure,
                            }
                        )
                        break
            prune_builder_cache()
        finally:
            if builder_created:
                builder_cleanup_failure = _run_command(
                    [docker, "buildx", "rm", "--force", builder_name],
                    cwd=project,
                    runner=command_runner,
                    label="docker buildx rm",
                )
        if builder_cleanup_failure:
            failures.append(
                {
                    "tool": "docker cleanup",
                    "package": "project",
                    "target": builder_name,
                    "detail": builder_cleanup_failure,
                }
            )

        trivy_failure = _run_command(
            [
                docker,
                "run",
                "--rm",
                "-v",
                f"{project.resolve()}:/workspace:ro",
                "-v",
                f"{trivy_cache.resolve()}:/root/.cache/trivy",
                TRIVY_IMAGE,
                "config",
                "--exit-code",
                "1",
                "--severity",
                "HIGH,CRITICAL",
                "/workspace/.phlo",
            ],
            cwd=project,
            runner=command_runner,
            label="trivy config",
        )
        if trivy_failure:
            failures.append(
                {
                    "tool": "trivy",
                    "package": "project",
                    "target": ".phlo",
                    "detail": trivy_failure,
                }
            )
        reported_image_failures: set[str] = set()
        for result in service_results:
            if result["status"] in {"passed", "waived"}:
                continue
            failure_key = result.get("image_id", result["service"])
            if failure_key in reported_image_failures:
                continue
            reported_image_failures.add(failure_key)
            failures.append(
                {
                    "tool": "trivy image",
                    "package": result["package"],
                    "target": result["service"],
                    "detail": result.get("detail", "image scan failed"),
                }
            )
        missing_results = [
            result["service"]
            for result in service_results
            if result.get("image_scan") not in {"passed", "failed", "unavailable", "waived"}
        ]
        if missing_results:
            failures.append(
                {
                    "tool": "trivy image",
                    "package": "unknown",
                    "target": ", ".join(missing_results),
                    "detail": "generated service has no image-scan result",
                }
            )
        if failures:
            lines = ["Generated container checks failed:"]
            lines.extend(
                f"- service [{result['package']}] {result['service']}: "
                f"{result['image'] or '<no image>'} -> {result['status']} "
                f"(image scan: {result.get('image_scan', 'missing')})"
                for result in service_results
            )
            lines.extend(
                f"- {failure['tool']} [{failure['package']}] {failure['target']}: "
                f"{failure['detail']}"
                for failure in failures
            )
            raise ContainerCheckError("\n".join(lines))

    return {
        "dockerfiles": relative_dockerfiles,
        "owners": dockerfile_owners,
        "hadolint": "passed" if dockerfiles else "skipped (no generated Dockerfiles)",
        "trivy": (
            "passed with explicit vulnerability waiver(s)"
            if any(result.get("image_scan") == "waived" for result in service_results)
            else "passed"
        ),
        "services": service_results,
    }


@click.command(name="check")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
@click.option(
    "--containers",
    is_flag=True,
    help="Generate a temporary user project and check its generated container files.",
)
@click.option(
    "--remote-images",
    is_flag=True,
    help="Resolve and scan rendered registry images without building or pulling them.",
)
@click.option(
    "--allow-vulnerable-image",
    "vulnerability_waiver_values",
    multiple=True,
    metavar="SERVICE=IMAGE=EVIDENCE_SHA256=REASON",
    help="Waive one exact HIGH/CRITICAL finding set for one generated service image.",
)
def check_cmd(
    output_json: bool,
    containers: bool,
    remote_images: bool,
    vulnerability_waiver_values: tuple[str, ...],
):
    """Validate installed plugins.

    Checks that all plugins comply with their interface requirements
    and reports any issues.

    Examples:
        phlo plugin check           # Check all plugins
        phlo plugin check --json    # Output as JSON
        phlo plugin check --containers  # Check generated container files
        phlo plugin check --containers \\
          --allow-vulnerable-image SERVICE=IMAGE=EVIDENCE_SHA256=REASON
    """
    try:
        if remote_images and not containers:
            raise click.UsageError("--remote-images requires --containers")
        if not output_json:
            console.print("Validating plugins...")

        # First discover plugins
        discover_plugins(auto_register=True)

        # Then validate
        validation_results: dict[str, Any] = {**validate_plugins()}

        if containers:
            validation_results["containers"] = check_generated_containers(
                vulnerability_waivers=_parse_vulnerability_waivers(vulnerability_waiver_values),
                remote_images=remote_images,
            )

        if output_json:
            click.echo(json.dumps(validation_results, indent=2))
            return

        # Rich formatted output
        valid = validation_results.get("valid", [])
        invalid = validation_results.get("invalid", [])
        logger.info(
            "plugin_check_completed",
            valid_count=len(valid),
            invalid_count=len(invalid),
            output_json=output_json,
        )

        console.print(f"\n[green]✓ Valid Plugins: {len(valid)}[/green]")
        if valid:
            for plugin_id in valid:
                console.print(f"  [green]✓[/green] {plugin_id}")

        if invalid:
            logger.warning("plugin_check_validation_failed", invalid_count=len(invalid))
            console.print(f"\n[red]✗ Invalid Plugins: {len(invalid)}[/red]")
            for plugin_id in invalid:
                console.print(f"  [red]✗[/red] {plugin_id}")
            sys.exit(1)
        else:
            console.print("\n[green]All plugins are valid![/green]")
            if containers:
                checked = validation_results.get("containers")
                if not isinstance(checked, dict):
                    raise RuntimeError("Container validation did not return a result mapping")
                console.print(
                    f"\n[green]Generated container checks passed:[/green] "
                    f"{len(checked['dockerfiles'])} Dockerfile(s)"
                )
                for service in checked["services"]:
                    if service["status"] == "waived":
                        waiver_line = Text("  ")
                        waiver_line.append("⚠ WAIVED", style="yellow")
                        waiver_line.append(
                            f" {service['package']} / {service['service']} → "
                            f"{service['image']}: {service['vulnerability_waiver']}"
                        )
                        console.print(waiver_line)
                        console.print(Text(f"    {service['detail']}", style="yellow"))
                        continue
                    console.print(
                        f"  [green]✓[/green] {service['package']} / {service['service']} "
                        f"→ {service['image']} ({service['status']})"
                    )

    except SystemExit:
        raise
    except Exception as e:
        logger.exception("plugin_check_failed", output_json=output_json)
        console.print(f"Error validating plugins: {e}", style="red", markup=False)
        sys.exit(1)
