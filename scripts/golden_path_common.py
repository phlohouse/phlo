#!/usr/bin/env python3
"""Shared harness utilities for the golden-path scripts.

Stdlib-only so both drivers run on a bare interpreter. The generic helpers
(logging, process execution, HTTP probes, ports, env-file editing) have one
implementation in ``packages/phlo-testing/src/phlo_testing/harness_core.py``,
loaded here by file path so the scripts never import an installed package.
Only the Docker checks, plugin install and env layering that the scripts need
on a bare interpreter live in this file.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

_CORE_PATH = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "phlo-testing"
    / "src"
    / "phlo_testing"
    / "harness_core.py"
)
_CORE_MODULE = "phlo_golden_path_harness_core"


def _load_core():
    loaded = sys.modules.get(_CORE_MODULE)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(_CORE_MODULE, _CORE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load harness helpers from {_CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CORE_MODULE] = module
    spec.loader.exec_module(module)
    return module


_core = _load_core()

log = _core.log
log_step = _core.log_step
log_success = _core.log_success
log_error = _core.log_error
log_warning = _core.log_warning
log_info = _core.log_info
force_remove_directory = _core.force_remove_directory
check_port_in_use = _core.check_port_in_use
find_available_port = _core.find_available_port
resolve_port = _core.resolve_port
run_command = _core.run_command
run_phlo = _core.run_phlo
setup_project_venv = _core.setup_project_venv
wait_for_http = _core.wait_for_http
wait_for_tcp = _core.wait_for_tcp
http_get = _core.http_get
http_get_basic = _core.http_get_basic
http_get_bearer = _core.http_get_bearer
http_post = _core.http_post
extract_openmetadata_token = _core.extract_openmetadata_token
openmetadata_login = _core.openmetadata_login
openmetadata_get_with_fallback = _core.openmetadata_get_with_fallback
read_env_file = _core.read_env_file
upsert_env_file = _core.upsert_env_file
write_file = _core.write_file


def cleanup_phlo_containers() -> int:
    """Stop and remove Docker containers whose names match Phlo."""
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=phlo"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    container_ids = [line for line in result.stdout.splitlines() if line]
    if not container_ids:
        return 0

    subprocess.run(
        ["docker", "stop", *container_ids],
        capture_output=True,
        timeout=60,
        check=True,
    )
    subprocess.run(
        ["docker", "rm", *container_ids],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return len(container_ids)


def verify_bind_mount_visibility(path: Path) -> tuple[bool, str]:
    """Verify Docker can see files from a host path via bind mount."""
    marker = f".phlo_bind_check_{int(time.time())}"
    target_path = path.resolve()
    marker_path = target_path / marker

    try:
        target_path.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("ok\n")
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{target_path}:/mnt:ro",
                "alpine:3.20",
                "sh",
                "-lc",
                f"test -f /mnt/{marker}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return True, ""
        detail = result.stderr.strip() or result.stdout.strip() or "unknown bind mount error"
        return False, detail
    except Exception as exc:
        return False, str(exc)
    finally:
        marker_path.unlink(missing_ok=True)


def install_plugin(
    plugin_name: str,
    *,
    project_dir: Path,
    phlo_source: Path,
    python_exe: Path,
) -> bool:
    """Install a phlo plugin into the project venv using uv.

    Returns True if successful, False otherwise.
    """
    # Check if the plugin package exists in phlo source
    plugin_pkg = phlo_source / "packages" / f"phlo-{plugin_name}"
    if not plugin_pkg.exists():
        log_warning(f"Plugin package not found: {plugin_pkg}")
        return False

    log_info(f"Installing plugin: {plugin_name}")
    try:
        run_command(
            ["uv", "pip", "install", "--python", str(python_exe), "-e", str(plugin_pkg)],
            cwd=project_dir,
            timeout=180,
        )
        return True
    except Exception as e:
        log_warning(f"Failed to install plugin {plugin_name}: {e}")
        return False


def project_env_paths(phlo_dir: Path) -> tuple[Path, ...]:
    """Return environment layers in precedence order (mirrors phlo.config.layout)."""
    return (
        phlo_dir / ".env",
        phlo_dir / ".env.local",
        phlo_dir / "overrides" / ".env",
        phlo_dir / "secrets" / ".env",
    )


def read_project_env(phlo_dir: Path) -> dict[str, str]:
    """Read and merge every existing environment layer, later layers winning."""
    values: dict[str, str] = {}
    for path in project_env_paths(phlo_dir):
        if path.is_file():
            values.update(read_env_file(path))
    return values


# Mirrors phlo.config.layout.SHARED_LAYOUT_MARKER; these scripts run on a bare
# Python interpreter and must not import from the phlo package itself.
SHARED_LAYOUT_MARKER = "# Phlo shared layout v1"


def env_destination(phlo_dir: Path, directory: str, legacy: str) -> Path:
    """Return the env destination for the project's current layout."""
    path = phlo_dir / directory / ".env"
    marker = phlo_dir / ".gitignore"
    if path.exists() or (marker.is_file() and SHARED_LAYOUT_MARKER in marker.read_text()):
        return path
    return phlo_dir / legacy


def apply_env_updates(phlo_dir: Path, updates: dict[str, str]) -> None:
    """Apply env updates to both the defaults and secrets destinations."""
    for destination in (
        env_destination(phlo_dir, "overrides", ".env"),
        env_destination(phlo_dir, "secrets", ".env.local"),
    ):
        upsert_env_file(destination, updates)
