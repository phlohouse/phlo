#!/usr/bin/env python3
"""Shared harness utilities for the golden-path scripts.

Stdlib-only so both drivers run on a bare interpreter: env layering, HTTP
probes, port handling, and process execution live here so the acceptance
driver and debug driver cannot drift on harness behavior.
"""

from __future__ import annotations

import base64
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

# ANSI colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg: str, color: str = "") -> None:
    """Print a log message with optional color."""
    print(f"{color}{msg}{RESET}")


def log_step(step: str) -> None:
    """Print a step header."""
    print()
    log(f"{'=' * 60}", BLUE)
    log(f"  {step}", BOLD + BLUE)
    log(f"{'=' * 60}", BLUE)


def log_success(msg: str) -> None:
    """Log a success message."""
    log(f"[OK] {msg}", GREEN)


def log_error(msg: str) -> None:
    """Log an error message."""
    log(f"[FAIL] {msg}", RED)


def log_warning(msg: str) -> None:
    """Log a warning message."""
    log(f"[WARN]  {msg}", YELLOW)


def log_info(msg: str) -> None:
    """Log an informational message."""
    log(f"[INFO]  {msg}", BLUE)


def force_remove_directory(path: Path) -> bool:
    """Force remove a directory even when Docker-created files resist deletion."""
    if not path.exists():
        return True

    # First try normal removal
    try:
        shutil.rmtree(path)
        return True
    except PermissionError:
        pass

    # Try with subprocess rm -rf (works for most permission issues)
    try:
        result = subprocess.run(
            ["rm", "-rf", str(path)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and not path.exists():
            return True
    except Exception:
        pass

    # Last resort: try with sudo
    try:
        result = subprocess.run(
            ["sudo", "rm", "-rf", str(path)],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0 and not path.exists()
    except Exception:
        return False


def check_port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(start_port: int, *, max_tries: int = 50) -> int | None:
    """Find the first available port starting at start_port."""
    for offset in range(max_tries):
        port = start_port + offset
        if not check_port_in_use(port):
            return port
    return None


def resolve_port(service: str, default_port: int) -> int:
    """Return a usable port for the service, falling back to the next available."""
    if not check_port_in_use(default_port):
        return default_port
    candidate = find_available_port(default_port + 1)
    if candidate is None:
        log_warning(f"Port {default_port} for {service} is in use and no alternative found")
        return default_port
    log_warning(f"Port {default_port} for {service} is in use; using {candidate}")
    return candidate


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


def run_command(
    args: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
    check: bool = True,
    stream_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command with live output streaming."""
    log_info(f"Running: {' '.join(args)}")

    if stream_output:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output_lines = []
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    print(f"    {line}", end="")
                    output_lines.append(line)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            raise

        result = subprocess.CompletedProcess(
            args=args,
            returncode=process.returncode,
            stdout="".join(output_lines),
            stderr="",
        )
    else:
        result = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    if check and result.returncode != 0:
        log_error(f"Command failed with exit code {result.returncode}")
        if result.stderr:
            print(result.stderr)
        raise RuntimeError(f"Command failed: {' '.join(args)}")

    return result


def run_phlo(
    args: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
    check: bool = True,
    stream_output: bool = True,
    python_exe: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a phlo CLI command, preferring the project venv python when given."""
    exe = str(python_exe) if python_exe else sys.executable
    return run_command(
        [exe, "-m", "phlo.cli.main", *args],
        cwd=cwd,
        timeout=timeout,
        check=check,
        stream_output=stream_output,
    )


def setup_project_venv(project_dir: Path, phlo_source: Path) -> Path:
    """Create and setup a virtual environment for the project using uv.

    Returns the path to the project's python executable.
    """
    venv_dir = project_dir / ".venv"
    python_exe = venv_dir / "bin" / "python"

    log_info("Creating project virtual environment with uv...")
    run_command(
        ["uv", "venv", str(venv_dir)],
        cwd=project_dir,
        timeout=60,
    )

    log_info("Installing phlo in project venv...")
    # Install phlo from source in dev mode
    run_command(
        ["uv", "pip", "install", "--python", str(python_exe), "-e", str(phlo_source)],
        cwd=project_dir,
        timeout=300,
    )

    # Install core service packages required for the golden path
    core_packages = [
        "phlo-dagster",
        "phlo-trino",
        "phlo-postgres",
        "phlo-minio",
        "phlo-nessie",
        "phlo-dlt",
        "phlo-dbt",
        "phlo-hasura",
        "phlo-postgrest",
        "phlo-superset",
        "phlo-api",
        "phlo-observatory",
    ]
    log_info("Installing core service packages...")
    install_args = ["uv", "pip", "install", "--python", str(python_exe)]
    for pkg in core_packages:
        pkg_path = phlo_source / "packages" / pkg
        if pkg_path.exists():
            install_args.extend(["-e", str(pkg_path)])
    run_command(install_args, cwd=project_dir, timeout=600)

    log_success("Project venv ready")
    return python_exe


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


def wait_for_http(url: str, *, timeout: int = 60, name: str = "endpoint") -> bool:
    """Wait for an HTTP endpoint to become available."""
    log_info(f"Waiting for {name} at {url}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    log_success(f"{name} is ready")
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
        print(".", end="", flush=True)
    print()
    log_warning(f"{name} not ready after {timeout}s")
    return False


def wait_for_tcp(
    host: str,
    port: int,
    *,
    timeout: int = 60,
    name: str = "service",
) -> bool:
    """Wait for a TCP endpoint to become available."""
    log_info(f"Waiting for {name} at {host}:{port}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                log_success(f"{name} is ready")
                return True
        except OSError:
            pass
        time.sleep(2)
        print(".", end="", flush=True)
    print()
    log_warning(f"{name} not ready after {timeout}s")
    return False


def http_get(
    url: str, *, headers: dict[str, str] | None = None, timeout: int = 30
) -> dict | list | str:
    """Make an HTTP GET request and return JSON or text response."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return json.loads(content)
    return content


def http_get_basic(
    url: str,
    *,
    username: str,
    password: str,
    timeout: int = 30,
) -> dict | list | str:
    """Make an HTTP GET request with basic auth."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    return http_get(url, headers=headers, timeout=timeout)


def http_get_bearer(
    url: str,
    *,
    token: str,
    timeout: int = 30,
) -> dict | list | str:
    """Make an HTTP GET request with bearer auth."""
    headers = {"Authorization": f"Bearer {token}"}
    return http_get(url, headers=headers, timeout=timeout)


def http_post(
    url: str,
    data: dict | str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict | list | str:
    """Make an HTTP POST request and return JSON or text response."""
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    body = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data.encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type:
            return json.loads(content)
        return content


def extract_openmetadata_token(payload: object) -> str | None:
    """Extract a bearer token from common OpenMetadata auth responses."""
    if isinstance(payload, dict):
        payload_dict = cast(dict[str, Any], payload)
        for key in ("accessToken", "token", "jwtToken", "idToken"):
            value = payload_dict.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("data", "result", "response", "auth"):
            nested = payload_dict.get(key)
            if nested is not None:
                token = extract_openmetadata_token(nested)
                if token:
                    return token
    elif isinstance(payload, list):
        for item in payload:
            token = extract_openmetadata_token(item)
            if token:
                return token
    return None


def openmetadata_login(base_url: str, *, username: str, password: str) -> str | None:
    """Login to OpenMetadata and return a bearer token if available."""
    endpoints = ["/api/v1/users/login", "/api/v1/auth/login"]
    encoded_password = base64.b64encode(password.encode("utf-8")).decode("ascii")
    payloads = [{"email": username, "password": encoded_password}]
    if "@" not in username:
        payloads.append({"email": f"{username}@open-metadata.org", "password": encoded_password})
    for endpoint in endpoints:
        url = f"{base_url}{endpoint}"
        for payload in payloads:
            try:
                response = http_post(url, payload, timeout=30)
            except urllib.error.HTTPError:
                continue
            token = extract_openmetadata_token(response)
            if token:
                return token
    return None


def openmetadata_get_with_fallback(
    urls: list[str],
    *,
    token: str | None,
    username: str,
    password: str,
    timeout: int = 30,
) -> dict | list | str | None:
    """GET the first available OpenMetadata endpoint."""
    last_error: urllib.error.HTTPError | None = None
    for url in urls:
        try:
            if token:
                return http_get_bearer(url, token=token, timeout=timeout)
            return http_get_basic(url, username=username, password=password, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                last_error = exc
                continue
            raise
    if last_error:
        return None
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """Read a .env file into a dict; surrounding quotes on values are stripped."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


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


def upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update or append entries in a .env file."""
    existing_lines = path.read_text().splitlines() if path.exists() else []
    rendered: list[str] = []
    seen: set[str] = set()

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rendered.append(line)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            rendered.append(line)

    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")

    path.write_text("\n".join(rendered) + "\n")


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


def write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    log_info(f"Created: {path}")
