"""Standalone utilities for Phlo test harnesses.

Owns the process, HTTP, port, env-file, and OpenMetadata helpers used by the
bundled-stack and profile harnesses. These helpers deliberately live inside
the installed package so ``phlo-testing`` never loads repo-only scripts such
as ``scripts/run_golden_path.py`` (which only resolves inside a repository
checkout, not from a pip-installed package).
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


def log(msg: str, color: str = "") -> None:
    """Print a log message (kept for harness output parity with repo scripts)."""
    print(f"{color}{msg}")


def log_step(step: str) -> None:
    """Print a step header."""
    print()
    print("=" * 60)
    print(f"  {step}")
    print("=" * 60)


def log_success(msg: str) -> None:
    """Log a success message."""
    print(f"[OK] {msg}")


def log_error(msg: str) -> None:
    """Log an error message."""
    print(f"[FAIL] {msg}")


def log_warning(msg: str) -> None:
    """Log a warning message."""
    print(f"[WARN]  {msg}")


def log_info(msg: str) -> None:
    """Log an informational message."""
    print(f"[INFO]  {msg}")


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
    """Read a .env file into a dict."""
    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


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


def apply_env_updates(phlo_dir: Path, updates: dict[str, str]) -> None:
    """Apply env updates to both .env and .env.local."""
    for env_path in (phlo_dir / ".env", phlo_dir / ".env.local"):
        upsert_env_file(env_path, updates)


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


def write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    log_info(f"Created: {path}")
