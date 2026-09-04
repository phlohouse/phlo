"""Shared utilities for services commands.

Holds the phlo.yaml project template, native-process state tracking, and
compose/config plumbing shared across the services command group.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import cast
from uuid import uuid4

import click

from phlo.cli.infrastructure.command import run_command
from phlo.cli.infrastructure.container_backend import select_project_container_backend
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.output import missing_compose_file_error, missing_phlo_project_error, user_error
from phlo.infrastructure.containers import resolve_container_name as _resolve_container_name
from phlo.logging import get_logger
from phlo.plugins.compose.generator import UV_LOCK_METADATA_FILES
from phlo.plugins.discovery._service_definition import ServiceDefinition
from phlo.plugins.discovery.service_manifest import ServiceManifestResolver
from phlo.plugins.discovery.services import ServiceDiscovery
from phlo.utils import dedupe_preserve_order

logger = get_logger(__name__)

PHLO_CONFIG_FILE = "phlo.yaml"
NATIVE_STATE_FILE = "native-processes.json"

UV_LOCKED_ENV_VAR = "PHLO_UV_LOCKED"

PHLO_CONFIG_TEMPLATE = """# Phlo Project Configuration
name: {name}
description: "{description}"

# Configure infrastructure overrides as needed. For example:
#
# infrastructure:
#   services:
#     postgres:
#       host: custom-host  # Override postgres host
#   container_naming_pattern: "{{project}}_{{service}}"  # Custom naming
#
# phlo-api authorization policy:
#
# api:
#   authorization:
#     backend: opa
#     mode: required
#
# services:
#   phlo-api:
#     authorization:
#       backend: opa
#       mode: required
#
# Project capability defaults:
capabilities:
  defaults:
    table_store: iceberg
    query_engine: trino
#
# Secrets belong in .phlo/.env.local (not committed).
"""


def get_phlo_dir() -> Path:
    """Get the .phlo directory path in current project."""
    return Path.cwd() / ".phlo"


def ensure_phlo_dir() -> Path:
    """Ensure .phlo directory exists with required files."""
    phlo_dir = get_phlo_dir()

    if not phlo_dir.exists():
        raise missing_phlo_project_error()

    return phlo_dir


def ensure_compose_project() -> Path:
    """Ensure generated service configuration exists before compose-backed commands run."""
    phlo_dir = ensure_phlo_dir()
    compose_file = phlo_dir / "docker-compose.yml"
    env_file = phlo_dir / ".env"

    if not compose_file.exists():
        raise missing_compose_file_error(".phlo/docker-compose.yml")
    if not env_file.exists():
        raise user_error(
            "Phlo services have not been initialized",
            missing=".phlo/.env",
            run="phlo services init",
        )
    return phlo_dir


def stage_uv_lock_metadata(project_root: Path, phlo_dir: Path) -> bool:
    """Stage the project's uv lock metadata into the generated build context.

    Generated service images build with ``.phlo`` as the Docker build context,
    so the root ``pyproject.toml``/``uv.lock`` consumed by lock-aware image
    builds are copied into it. Returns True when the project is uv-managed
    (both files present). Staging refreshes the copies on every
    ``phlo services init``/``start`` so an image rebuild consumes the project's
    current lockfile; a lockfile out of sync with its ``pyproject.toml`` fails
    the image build instead of silently resolving a fresh dependency graph.
    """
    sources = [project_root / name for name in UV_LOCK_METADATA_FILES]
    if not all(source.is_file() for source in sources):
        return False
    phlo_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copy2(source, phlo_dir / source.name)
    return True


def apply_uv_lock_env_override(
    phlo_dir: Path, env_overrides: dict[str, object]
) -> dict[str, object]:
    """Flag generated service image builds as lock-aware for uv-managed projects.

    Stages lock metadata from the project root and sets ``PHLO_UV_LOCKED=true``
    unless the project already overrides that variable in phlo.yaml. With the
    flag set but no staged lockfile, the image build fails clearly rather than
    silently resolving an alternative dependency graph.
    """
    staged = stage_uv_lock_metadata(phlo_dir.parent, phlo_dir)
    if staged:
        env_overrides.setdefault(UV_LOCKED_ENV_VAR, "true")
    return env_overrides


def check_docker_available() -> bool:
    """Check if the Docker CLI is available.

    Docker daemon round-trips like `docker info`/`docker version` can time out under
    heavy local load even when subsequent compose commands succeed. For services
    commands, a CLI-availability check avoids false negatives and lets the real
    compose invocation surface daemon errors directly.
    """
    try:
        return shutil.which("docker") is not None
    except Exception:
        logger.debug("docker_check_failed")
        return False


def require_docker():
    """Exit with helpful message if the Docker CLI is unavailable."""
    require_container_backend("docker")


def require_container_backend(backend_name: str | None = None) -> None:
    """Exit with helpful message if the selected container backend is unavailable."""
    try:
        backend = select_project_container_backend(cli_backend=backend_name)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        available, fix = backend.check_available()
    except subprocess.TimeoutExpired:
        click.echo(f"Error: {backend.name} availability check timed out.", err=True)
        sys.exit(1)
    if available:
        return
    click.echo(f"Error: {backend.name} backend is not available.", err=True)
    if fix:
        click.echo("", err=True)
        click.echo(fix, err=True)
    sys.exit(1)


def resolve_phlo_package_dir(path: Path) -> Path | None:
    """Resolve a path to the `src/phlo` package directory (must contain `__init__.py`).

    Accepts either:
    - the package directory itself (`.../src/phlo`)
    - a repo root containing `src/phlo`
    - a `src/` directory containing `phlo/`
    """
    candidates = (path, path / "src" / "phlo", path / "phlo")
    return next(
        (c for c in candidates if c.is_dir() and (c / "__init__.py").is_file()),
        None,
    )


def relpath_from_phlo_dir(path: Path) -> str:
    """Return a `.phlo/`-relative path string for docker-compose volume mounts."""
    try:
        return str(os.path.relpath(path, Path.cwd() / ".phlo"))
    except ValueError:
        # On Windows, relpath can fail across drives
        return str(path)


def detect_phlo_source_path() -> str | None:
    """Detect phlo source path using multiple strategies.

    Tries in order:
    1. PHLO_DEV_SOURCE environment variable
    2. Common directory patterns relative to CWD
    3. Returns None if not found
    """
    # Strategy 1: Environment variable
    env_path = os.environ.get("PHLO_DEV_SOURCE")
    if env_path and (resolved := resolve_phlo_package_dir(Path(env_path))):
        return relpath_from_phlo_dir(resolved)

    # Strategy 2: Common directory patterns
    candidates: list[Path] = []

    # Current working tree (handles running from the phlo repo itself)
    candidates.extend(
        [
            Path.cwd() / "src" / "phlo",
            Path.cwd(),
            Path.cwd() / "src",
        ]
    )

    # Sibling `phlo/` repo (common: `~/Developer/{phlo,project}`) - walk up a few levels.
    for parent in list(Path.cwd().parents)[:4]:
        candidates.append(parent / "phlo")

    for candidate in candidates:
        if resolved := resolve_phlo_package_dir(candidate):
            return relpath_from_phlo_dir(resolved)

    return None


def _get_env_overrides(config: dict) -> dict[str, object]:
    """Return the service environment override mapping from loaded configuration."""
    env_overrides = config.get("env", {})
    return env_overrides if isinstance(env_overrides, dict) else {}


def get_enabled_disabled_service_names(config: dict | None) -> tuple[set[str], set[str]]:
    """Return enabled/disabled service names from top-level service config.

    Supports both state formats:
    - list form: ``services.enabled`` / ``services.disabled``
    - mapping form: ``services.<name>.enabled: true|false``
    """
    if not isinstance(config, dict):
        return set(), set()

    services_config = config.get("services", {})
    if not isinstance(services_config, dict):
        return set(), set()

    def _clean_name(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    enabled_names: set[str] = set()
    disabled_names: set[str] = set()

    enabled_list = services_config.get("enabled")
    if isinstance(enabled_list, list):
        for name in enabled_list:
            if normalized := _clean_name(name):
                enabled_names.add(normalized)

    disabled_list = services_config.get("disabled")
    if isinstance(disabled_list, list):
        for name in disabled_list:
            if normalized := _clean_name(name):
                disabled_names.add(normalized)

    for name, service_config in services_config.items():
        if not isinstance(service_config, dict):
            continue
        normalized_name = _clean_name(name)
        if not normalized_name:
            continue
        if service_config.get("enabled") is False:
            disabled_names.add(normalized_name)
        elif service_config.get("enabled") is True:
            enabled_names.add(normalized_name)

    disabled_names.difference_update(enabled_names)
    return enabled_names, disabled_names


def _normalize_service_name_list(names: object) -> list[str]:
    """Normalize a service name list to unique lowercase names."""
    if not isinstance(names, list):
        return []

    normalized = [name.strip().lower() for name in names if isinstance(name, str) and name.strip()]
    return dedupe_preserve_order(normalized)


def normalize_services_enabled_disabled_config(
    config: dict[str, object],
) -> tuple[list[str], list[str]]:
    """Normalize services enabled/disabled lists and resolve contradictions.

    Rules:
        - Missing/non-list values become empty lists.
        - Service names are stripped, lowercased, deduplicated.
        - Deterministic ordering via sort.
        - If a name appears in both lists, disabled takes precedence.
    """
    services_config_value = config.get("services")
    if isinstance(services_config_value, dict):
        services_config = cast(dict[str, object], services_config_value)
    else:
        services_config = {}
        config["services"] = services_config

    enabled_names = _normalize_service_name_list(services_config.get("enabled"))
    disabled_names = _normalize_service_name_list(services_config.get("disabled"))
    conflicted_names = set(enabled_names).intersection(disabled_names)
    if conflicted_names:
        enabled_names = [name for name in enabled_names if name not in conflicted_names]

    enabled_names = sorted(enabled_names)
    disabled_names = sorted(disabled_names)
    services_config["enabled"] = enabled_names
    services_config["disabled"] = disabled_names
    return enabled_names, disabled_names


def _warn_secret_env_overrides(env_overrides: dict[str, object], services: list) -> None:
    """Warn when config env overrides include secret-backed service variables."""
    if not env_overrides:
        return
    secret_keys = {
        name for service in services for name, cfg in service.env_vars.items() if cfg.get("secret")
    }
    overlapping = sorted(set(env_overrides).intersection(secret_keys))
    if overlapping:
        click.echo(
            "Warning: phlo.yaml env overrides include secret keys. "
            "Move these to .phlo/.env.local instead:",
            err=True,
        )
        click.echo(f"  {', '.join(overlapping)}", err=True)


def _normalize_hook_entries(hooks: object) -> list[dict[str, object]]:
    """Normalize hook configuration to a list of mapping entries."""
    if hooks is None:
        return []
    if isinstance(hooks, dict):
        return [{str(k): v for k, v in hooks.items()}]
    if not isinstance(hooks, list):
        return []
    entries: list[dict[str, object]] = []
    for item in hooks:
        if isinstance(item, dict):
            entries.append({str(k): v for k, v in item.items()})
        elif isinstance(item, list):
            entries.append({"command": item})
        elif isinstance(item, str):
            entries.append({"command": [item]})
    return entries


def _format_hook_command(command: object, substitutions: dict[str, str]) -> list[str]:
    """Format hook command tokens with safe placeholder substitution."""
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list):
        return []

    # Values are brace-escaped before substitution so user-controlled data
    # cannot inject additional format placeholders into the command.
    safe_substitutions = {
        k: v.replace("{", "{{").replace("}", "}}") for k, v in substitutions.items()
    }

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            """Return an empty string for unknown placeholders."""
            return ""

    formatted: list[str] = []
    for item in command:
        if not isinstance(item, str):
            continue
        formatted.append(item.format_map(_SafeDict(safe_substitutions)))
    return formatted


def _run_service_hooks(
    hook_name: str,
    service_names: list[str],
    project_name: str,
    project_root: Path,
) -> None:
    """Execute configured service hooks for a lifecycle event."""
    if not service_names:
        return

    from phlo.plugins.discovery.services import ServiceDiscovery

    logger.debug(
        "service_hook_execution_started",
        hook_name=hook_name,
        service_count=len(service_names),
    )
    discovery = ServiceDiscovery()
    executed_count = 0
    failed_count = 0
    for name in service_names:
        service = discovery.get_service(name)
        if not service:
            continue
        hook_entries = _normalize_hook_entries(service.hooks.get(hook_name))
        if not hook_entries:
            continue
        substitutions = {
            "project_name": project_name,
            "service_name": service.name,
            "container_name": _resolve_container_name(service.name, project_name),
            "project_root": str(project_root),
        }
        for hook in hook_entries:
            required_module = hook.get("requires")
            if isinstance(required_module, str):
                import importlib.util

                if importlib.util.find_spec(required_module) is None:
                    logger.debug(
                        "service_hook_skipped_missing_dependency",
                        hook_name=hook_name,
                        service_name=service.name,
                        required_module=required_module,
                    )
                    continue

            # Respect delay setting
            delay = hook.get("delay")
            if isinstance(delay, (int, float)) and delay > 0:
                time.sleep(delay)

            command = _format_hook_command(hook.get("command"), substitutions)
            if not command:
                continue

            # Use the Phlo interpreter for package hook modules. Generated project
            # venvs do not necessarily install every optional service package.
            if command and command[0] in ("python", "python3"):
                command = [sys.executable, *command[1:]]

            timeout = hook.get("timeout_seconds")
            if isinstance(timeout, str) and timeout.isdigit():
                timeout = int(timeout)
            elif not isinstance(timeout, int):
                timeout = None
            command_name = command[0] if command else ""
            command_started = time.perf_counter()
            logger.debug(
                "service_hook_command_started",
                hook_name=hook_name,
                service_name=service.name,
                command_name=command_name,
                arg_count=max(len(command) - 1, 0),
                timeout_seconds=timeout,
            )
            try:
                result = run_command(command, timeout_seconds=timeout, check=False)
            except Exception as exc:
                failed_count += 1
                click.echo(
                    f"Warning: hook '{hook_name}' for {service.name} failed: {exc}",
                    err=True,
                )
                logger.warning(
                    "service_hook_command_failed",
                    hook_name=hook_name,
                    service_name=service.name,
                    command_name=command_name,
                    error=str(exc),
                    elapsed_ms=round((time.perf_counter() - command_started) * 1000, 2),
                )
                continue
            executed_count += 1
            logger.debug(
                "service_hook_command_completed",
                hook_name=hook_name,
                service_name=service.name,
                command_name=command_name,
                returncode=result.returncode,
                elapsed_ms=round((time.perf_counter() - command_started) * 1000, 2),
            )
            if result.returncode != 0:
                failed_count += 1
                click.echo(
                    f"Warning: hook '{hook_name}' for {service.name} failed: {result.stderr}",
                    err=True,
                )
                logger.warning(
                    "service_hook_command_nonzero_exit",
                    hook_name=hook_name,
                    service_name=service.name,
                    command_name=command_name,
                    returncode=result.returncode,
                )
    logger.debug(
        "service_hook_execution_completed",
        hook_name=hook_name,
        executed_count=executed_count,
        failed_count=failed_count,
    )


def _emit_service_lifecycle_events(
    phase: str,
    service_names: list[str],
    project_name: str,
    project_root: Path,
    *,
    request_id: str | None = None,
    status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Emit lifecycle hook events for each service in scope.

    request_id correlates every event emitted for one lifecycle operation.
    """
    if not service_names:
        return
    from phlo.hooks import (
        HookCorrelation,
        ServiceLifecycleEventContext,
        ServiceLifecycleEventEmitter,
    )

    operation_request_id = request_id or uuid4().hex

    for name in service_names:
        emitter = ServiceLifecycleEventEmitter(
            ServiceLifecycleEventContext(
                service_name=name,
                project_name=project_name,
                project_root=str(project_root),
                container_name=_resolve_container_name(name, project_name),
                correlation=HookCorrelation(request_id=operation_request_id),
            )
        )
        emitter.emit(phase=phase, status=status, metadata=metadata)


def _native_state_path(project_root: Path) -> Path:
    """Return the filesystem path for persisted native service state."""
    return project_root / ".phlo" / NATIVE_STATE_FILE


def _load_native_state(project_root: Path) -> dict[str, dict]:
    """Load persisted native process state; empty mapping when missing or corrupt."""
    path = _native_state_path(project_root)
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError) as e:
        click.echo(f"Warning: Failed to read native state file {path}: {e}", err=True)
        return {}


def _save_native_state(project_root: Path, state: dict[str, dict]) -> None:
    """Atomically save native process state for a project."""
    path = _native_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def _stop_native_processes(project_root: Path, service_names: list[str] | None = None) -> None:
    """Stop tracked native service processes and update persisted state."""
    state = _load_native_state(project_root)
    if not state:
        return

    target_names = service_names or list(state.keys())
    for name in target_names:
        entry = state.get(name)
        if not entry:
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int):
            state.pop(name, None)
            continue

        # Termination protocol: SIGTERM the process group first (falling back
        # to the bare pid if groups are unavailable), allow up to 10 seconds
        # for the whole group to exit, then SIGKILL that same scope. A leader
        # exit alone does not prove that a descendant released its port.
        process_group = True
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            state.pop(name, None)
            continue
        except Exception:
            process_group = False
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                state.pop(name, None)
                continue

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not _native_process_scope_exists(pid, process_group=process_group):
                state.pop(name, None)
                break
            time.sleep(0.25)

        if name not in state:
            continue
        if not _native_process_scope_exists(pid, process_group=process_group):
            state.pop(name, None)
            continue

        try:
            if process_group:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            state.pop(name, None)
            continue
        except PermissionError:
            continue
        except Exception:
            logger.warning("process_kill_failed", name=name, pid=pid)
            continue

        kill_deadline = time.monotonic() + 5
        while time.monotonic() < kill_deadline:
            if not _native_process_scope_exists(pid, process_group=process_group):
                state.pop(name, None)
                break
            time.sleep(0.25)

    if state:
        _save_native_state(project_root, state)
    else:
        _native_state_path(project_root).unlink(missing_ok=True)


def _native_process_scope_exists(pid: int, *, process_group: bool) -> bool:
    """Return whether the native process group or bare process remains alive."""
    with suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    try:
        if process_group:
            os.killpg(pid, 0)
        else:
            os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def get_profile_service_names(profile_names: tuple[str, ...]) -> list[str]:
    """Return service names belonging to the specified profiles."""
    if not profile_names:
        return []

    from phlo.plugins.discovery.services import ServiceDiscovery

    discovery = ServiceDiscovery()
    service_names: list[str] = []

    for profile in profile_names:
        services = discovery.get_services_by_profile(profile)
        service_names.extend(s.name for s in services)

    return service_names


def expand_service_dependencies(
    discovery: ServiceDiscovery,
    services: list[ServiceDefinition],
) -> list[ServiceDefinition]:
    """Expand a list of services with their transitive dependencies and setup companions.

    Returns services in topological (dependency) order via manifest resolution.
    """
    if not services:
        return []

    all_services = list(discovery.discover().values())
    return ServiceManifestResolver.expand_dependencies(
        all_services,
        [service.name for service in services],
    )


def _regenerate_compose(discovery, config: dict, phlo_dir: Path):
    """Regenerate docker-compose.yml based on current config."""
    from phlo.cli.infrastructure.selection import select_services_to_install
    from phlo.cli.infrastructure.utils import parse_env_file
    from phlo.plugins.compose import ComposeGenerator

    all_services = discovery.discover()

    # Get default services
    default_services = discovery.get_default_services()

    # Get enabled/disabled services from normalized config
    enabled_names, disabled_names = normalize_services_enabled_disabled_config(config)

    services_to_install = select_services_to_install(
        all_services=all_services,
        default_services=default_services,
        enabled_names=enabled_names,
        disabled_names=disabled_names,
    )
    services_to_install = expand_service_dependencies(discovery, services_to_install)

    # Get user service overrides from config
    user_overrides = config.get("services", {})
    env_overrides = _get_env_overrides(config)
    # Keep the lock-aware build flag and staged lock metadata in sync with the
    # project root across regeneration.
    env_overrides = apply_uv_lock_env_override(phlo_dir, env_overrides)
    env_local_file = phlo_dir / ".env.local"
    existing_env_local = parse_env_file(env_local_file)

    # Generate docker-compose.yml
    composer = ComposeGenerator(discovery)
    compose_content = composer.generate_compose(
        services_to_install,
        phlo_dir,
        user_overrides=user_overrides,
        env_values={**os.environ, **env_overrides, **existing_env_local},
    )

    compose_file = phlo_dir / "docker-compose.yml"
    compose_file.write_text(compose_content)
    click.echo("Updated: .phlo/docker-compose.yml")

    _warn_secret_env_overrides(env_overrides, services_to_install)

    # Regenerate .env + .env.local
    env_file = phlo_dir / ".env"
    env_content = composer.generate_env(services_to_install, env_overrides=env_overrides)
    env_local_content = composer.generate_env_local(
        services_to_install,
        env_overrides=env_overrides,
        existing_values=existing_env_local,
    )
    env_file.write_text(env_content)
    click.echo("Updated: .phlo/.env")
    write_sensitive_file(env_local_file, env_local_content)
    click.echo("Updated: .phlo/.env.local")

    # Copy any new service files
    copied_files = composer.copy_service_files(services_to_install, phlo_dir)
    for f in copied_files:
        click.echo(f"Updated: .phlo/{f}")
