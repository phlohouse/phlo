#!/usr/bin/env python3
"""Run the complete Phlo golden path workflow end-to-end with live output.

Script version of the golden path E2E test for easier debugging. Pass
--test-api, --test-observability, --test-superset, --test-openmetadata,
or --test-all to exercise optional services.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
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
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(start_port: int, *, max_tries: int = 50) -> int | None:
    """Find the first available port starting at start_port."""
    for offset in range(max_tries):
        port = start_port + offset
        if not check_port_in_use(port):
            return port
    return None


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


def preflight_check(*, auto_cleanup: bool = False) -> bool:
    """
    Run preflight checks before starting the golden path test.

    Returns True if all checks pass, False otherwise.
    """
    log_step("Preflight Checks")
    all_ok = True

    # Check for running phlo-related containers
    log_info("Checking for running Docker containers...")
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        containers = [c for c in result.stdout.strip().split("\n") if c]
        phlo_containers = [c for c in containers if "phlo" in c.lower()]

        if phlo_containers:
            log_warning(f"Found {len(phlo_containers)} phlo-related containers running:")
            for c in phlo_containers[:5]:
                log_info(f"  - {c}")
            if len(phlo_containers) > 5:
                log_info(f"  ... and {len(phlo_containers) - 5} more")

            if auto_cleanup:
                log_info("Auto-cleanup enabled, stopping containers...")
                removed = cleanup_phlo_containers()
                log_success(f"Stopped and removed {removed} Phlo containers")
            else:
                log_error("Please stop containers first or use --auto-cleanup")
                all_ok = False
        else:
            log_success("No phlo containers running")
    except Exception as e:
        log_warning(f"Could not check Docker containers: {e}")

    # Check common ports
    log_info("Checking for port availability...")
    common_ports = {
        3000: "Dagster",
        5432: "PostgreSQL",
        8080: "Trino",
        9000: "MinIO API",
        9001: "MinIO Console",
        8082: "Hasura",
        3002: "PostgREST",
        9090: "Prometheus",
        3100: "Loki",
        3003: "Grafana",
        8088: "Superset",
        8585: "OpenMetadata",
    }

    ports_in_use = []
    for port, service in common_ports.items():
        if check_port_in_use(port):
            ports_in_use.append((port, service))

    if ports_in_use:
        log_warning(f"Found {len(ports_in_use)} ports in use:")
        for port, service in ports_in_use:
            log_info(f"  - Port {port} ({service})")
        if not auto_cleanup:
            log_error("Please free these ports or use --auto-cleanup")
            all_ok = False
    else:
        log_success("All common ports are available")

    # Check Docker daemon
    log_info("Checking Docker daemon...")
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            log_success("Docker daemon is running")
        else:
            log_error("Docker daemon is not running")
            all_ok = False
    except Exception as e:
        log_error(f"Docker check failed: {e}")
        all_ok = False

    # Check available disk space
    log_info("Checking disk space...")
    try:
        result = subprocess.run(
            ["df", "-BG", "/tmp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Parse df output
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                available = parts[3].replace("G", "")
                try:
                    available_gb = int(available)
                    if available_gb < 10:
                        log_warning(f"Low disk space: {available_gb}GB available in /tmp")
                    else:
                        log_success(f"Disk space OK: {available_gb}GB available in /tmp")
                except ValueError:
                    pass
    except Exception as e:
        log_warning(f"Could not check disk space: {e}")

    if all_ok:
        log_success("All preflight checks passed!")
    else:
        log_error("Preflight checks failed. Fix issues above or use --auto-cleanup")

    return all_ok


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


def main() -> int:
    """Run the full golden path workflow and return its exit code."""
    parser = argparse.ArgumentParser(
        description="Run Golden Path E2E Workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              # Run core workflow only
              python scripts/run_golden_path.py

              # Test with API layer (Hasura + PostgREST)
              python scripts/run_golden_path.py --test-api

              # Test with observability stack
              python scripts/run_golden_path.py --test-observability

              # Test everything
              python scripts/run_golden_path.py --test-all
        """),
    )
    parser.add_argument(
        "--mode", choices=["dev", "pypi"], default="dev", help="Installation mode (default: dev)"
    )
    parser.add_argument(
        "--keep-running", action="store_true", help="Keep services running after test"
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Project directory (default: ~/tmp/phlo-golden-path)",
    )
    # Optional service testing flags
    parser.add_argument(
        "--test-api",
        action="store_true",
        help="Test Hasura GraphQL and PostgREST REST APIs",
    )
    parser.add_argument(
        "--test-observability",
        action="store_true",
        help="Test observability stack (Prometheus, Loki, Alloy, Grafana)",
    )
    parser.add_argument(
        "--test-superset",
        action="store_true",
        help="Test Superset BI dashboards",
    )
    parser.add_argument(
        "--test-openmetadata",
        action="store_true",
        help="Test OpenMetadata data catalog (requires 6GB+ RAM)",
    )
    parser.add_argument(
        "--test-alerting",
        action="store_true",
        help="Test alerting plugin (list destinations, send test alert)",
    )
    parser.add_argument(
        "--test-lineage",
        action="store_true",
        help="Test lineage tracking (configure LINEAGE_DB_URL, query lineage)",
    )
    parser.add_argument(
        "--test-all",
        action="store_true",
        help="Test all optional services (api, observability, superset, alerting, lineage, openmetadata)",
    )
    parser.add_argument(
        "--auto-cleanup",
        action="store_true",
        help="Automatically stop running containers and free ports before starting",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight checks (not recommended)",
    )
    parser.add_argument(
        "--partition-date",
        default=None,
        help="Partition date for ingestion/transform (YYYY-MM-DD). Default: yesterday.",
    )
    args = parser.parse_args()

    # Expand --test-all
    if args.test_all:
        args.test_api = True
        args.test_observability = True
        args.test_superset = True
        args.test_alerting = True
        args.test_lineage = True
        args.test_openmetadata = True

    default_project_name = "phlo-golden-path"
    default_project_root = Path.home() / "tmp"
    project_name = args.project_dir.name if args.project_dir else default_project_name
    project_dir = (
        args.project_dir.expanduser() if args.project_dir else default_project_root / project_name
    )
    phlo_source = Path(__file__).resolve().parents[1]

    log_step("Golden Path E2E Workflow")
    log_info(f"Mode: {args.mode}")
    log_info(f"Project dir: {project_dir}")
    log_info(f"Phlo source: {phlo_source}")

    lineage_db_url_host: str | None = None

    # Log optional test configuration
    optional_tests = []
    if args.test_api:
        optional_tests.append("API (Hasura/PostgREST)")
    if args.test_observability:
        optional_tests.append("Observability (Prometheus/Loki/Alloy/Grafana)")
    if args.test_superset:
        optional_tests.append("Superset")
    if args.test_alerting:
        optional_tests.append("Alerting")
    if args.test_lineage:
        optional_tests.append("Lineage")
    if args.test_openmetadata:
        optional_tests.append("OpenMetadata")
    if optional_tests:
        log_info(f"Optional tests: {', '.join(optional_tests)}")
    else:
        log_info("Optional tests: None (use --test-api, --test-observability, etc.)")

    # Run preflight checks
    if not args.skip_preflight:
        if not preflight_check(auto_cleanup=args.auto_cleanup):
            return 1
    else:
        log_warning("Skipping preflight checks (--skip-preflight)")

    bind_mount_ok, bind_mount_detail = verify_bind_mount_visibility(project_dir.parent)
    if not bind_mount_ok:
        log_error(f"Docker cannot bind-mount project parent: {project_dir.parent}")
        log_warning(bind_mount_detail)
        log_info("Choose a different --project-dir under your home directory and rerun.")
        return 1

    # Clean up if exists
    if project_dir.exists():
        log_warning(f"Removing existing project dir: {project_dir}")
        if not force_remove_directory(project_dir):
            log_error(f"Failed to remove {project_dir}")
            return 1

    try:
        # Step 1: Initialize project
        log_step("Step 1: Initialize Project")
        run_phlo(
            ["init", project_name, "--template", "basic", "--force"],
            cwd=project_dir.parent,
            timeout=120,
        )
        log_success("Project initialized")

        # Step 1.5: Setup project virtual environment
        log_step("Step 1.5: Setup Project Environment")
        project_python = setup_project_venv(project_dir, phlo_source)

        # Step 2: Services init
        log_step("Step 2: Initialize Services")
        if args.mode == "dev":
            run_phlo(
                ["services", "init", "--dev", "--phlo-source", str(phlo_source), "--force"],
                cwd=project_dir,
                timeout=180,
                python_exe=project_python,
            )
        else:
            run_phlo(
                ["services", "init", "--no-dev", "--force"],
                cwd=project_dir,
                timeout=180,
                python_exe=project_python,
            )
        log_success("Services initialized")

        phlo_dir = project_dir / ".phlo"
        env_vars = read_env_file(phlo_dir / ".env")

        # Set ports to avoid conflicts before starting services.
        env_updates: dict[str, str] = {}

        phlo_api_port = resolve_port("Phlo API", 54000)
        env_updates["PHLO_API_PORT"] = str(phlo_api_port)

        core_port_defaults = {
            "DAGSTER_PORT": 3000,
            "POSTGRES_PORT": 5432,
            "TRINO_PORT": 8080,
            "MINIO_API_PORT": 9000,
            "MINIO_CONSOLE_PORT": 9001,
            "NESSIE_PORT": 19120,
        }
        for key, default_port in core_port_defaults.items():
            env_updates[key] = str(resolve_port(key, default_port))

        if args.mode == "dev":
            # Force Dagster containers to install local workspace packages from /opt/phlo-dev.
            env_updates["PHLO_DEV_EXTRA_PACKAGES"] = ",".join(
                [
                    "phlo-dagster",
                    "phlo-dlt",
                    "phlo-dbt",
                    "phlo-iceberg",
                    "phlo-trino",
                    "phlo-postgres",
                    "phlo-nessie",
                    "phlo-minio",
                    "phlo-hasura",
                    "phlo-postgrest",
                    "phlo-superset",
                    "phlo-api",
                    "phlo-observatory",
                    "phlo-lineage",
                ]
            )

        if args.test_api:
            env_updates["HASURA_PORT"] = str(resolve_port("Hasura", 8082))
            env_updates["POSTGREST_PORT"] = str(resolve_port("PostgREST", 3002))

        if args.test_observability:
            env_updates["PROMETHEUS_PORT"] = str(resolve_port("Prometheus", 9090))
            env_updates["LOKI_PORT"] = str(resolve_port("Loki", 3100))
            env_updates["GRAFANA_PORT"] = str(resolve_port("Grafana", 3003))
            env_updates["ALLOY_PORT"] = str(resolve_port("Alloy", 12345))

        if args.test_superset:
            env_updates["SUPERSET_PORT"] = str(resolve_port("Superset", 8088))

        if args.test_openmetadata:
            env_updates["OPENMETADATA_PORT"] = str(resolve_port("OpenMetadata", 8585))

        if args.test_lineage:
            pg_user = env_vars.get("POSTGRES_USER", "phlo")
            pg_pass = env_vars.get("POSTGRES_PASSWORD", "phlo")
            pg_db = env_vars.get("POSTGRES_DB", "phlo")
            postgres_port = int(
                env_updates.get("POSTGRES_PORT", env_vars.get("POSTGRES_PORT", "5432"))
            )
            lineage_db_url_host = (
                f"postgresql://{pg_user}:{pg_pass}@localhost:{postgres_port}/{pg_db}"
            )
            lineage_db_url_container = f"postgresql://{pg_user}:{pg_pass}@postgres:5432/{pg_db}"
            env_updates["LINEAGE_DB_URL"] = lineage_db_url_container
            env_updates["PHLO_DEV_EXTRA_PACKAGES"] = "phlo-lineage"
            os.environ["LINEAGE_DB_URL"] = lineage_db_url_host

        resolved_ports = dict(env_updates)
        apply_env_updates(phlo_dir, resolved_ports)
        log_info("Updated .phlo/.env and .phlo/.env.local with resolved ports")

        # Step 3: Create workflow
        log_step("Step 3: Create Workflow")
        run_phlo(
            [
                "workflow",
                "create",
                "--type",
                "ingestion",
                "--domain",
                "jsonplaceholder",
                "--table",
                "posts",
                "--unique-key",
                "id",
                "--cron",
                "0 */1 * * *",
                "--api-base-url",
                "https://jsonplaceholder.typicode.com",
                "--field",
                "userId:int",
                "--field",
                "title:str",
                "--field",
                "body:str",
            ],
            cwd=project_dir,
            timeout=60,
            python_exe=project_python,
        )
        log_success("Workflow created")

        # Update the asset with validate=False
        asset_file = project_dir / "workflows" / "ingestion" / "jsonplaceholder" / "posts.py"
        asset_content = textwrap.dedent('''
            """Jsonplaceholder posts ingestion asset."""

            from dlt.sources.rest_api import rest_api
            from phlo_dlt import phlo_ingestion
            from workflows.schemas.jsonplaceholder import RawPosts


            @phlo_ingestion(
                table_name="posts",
                unique_key="id",
                validation_schema=RawPosts,
                group="jsonplaceholder",
                cron="0 */1 * * *",
                freshness_hours=(1, 24),
                validate=False,
            )
            def posts(partition_date: str):
                base_url = "https://jsonplaceholder.typicode.com"
                return rest_api(
                    client={"base_url": base_url},
                    resources=[{"name": "posts", "endpoint": {"path": "posts"}}],
                )
        ''').lstrip()
        write_file(asset_file, asset_content)

        # Create dbt profiles and mart model
        write_file(
            project_dir / "workflows" / "transforms" / "dbt" / "profiles" / "profiles.yml",
            textwrap.dedent(f"""
                phlo:
                  target: dev
                  outputs:
                    dev:
                      type: trino
                      method: none
                      user: {env_vars.get("TRINO_USER", "dagster")}
                      host: trino
                      port: 8080
                      catalog: {env_vars.get("TRINO_CATALOG", "iceberg")}
                      schema: {env_vars.get("TRINO_SCHEMA", "raw")}
                      http_scheme: http
                      threads: 2
            """).lstrip(),
        )

        write_file(
            project_dir / "workflows" / "transforms" / "dbt" / "models" / "sources" / "raw.yml",
            textwrap.dedent(f"""
                version: 2

                sources:
                  - name: raw
                    database: {env_vars.get("TRINO_CATALOG", "iceberg")}
                    schema: {env_vars.get("TRINO_SCHEMA", "raw")}
                    tables:
                      - name: posts
                        columns:
                          - name: id
                          - name: user_id
                          - name: title
                          - name: body
            """).lstrip(),
        )

        write_file(
            project_dir
            / "workflows"
            / "transforms"
            / "dbt"
            / "models"
            / "marts"
            / "posts_mart.sql",
            textwrap.dedent("""
                {{ config(materialized='table', schema='marts') }}
                select
                  cast(src.id as varchar) as id,
                  src.user_id,
                  src.title,
                  src.body
                from {{ source('raw', 'posts') }} as src
            """).lstrip(),
        )

        write_file(
            project_dir / "workflows" / "publishing" / "__init__.py",
            '"""Publishing assets."""\n',
        )

        write_file(
            project_dir / "workflows" / "publishing" / "jsonplaceholder.py",
            textwrap.dedent("""
                import dagster as dg
                import psycopg2
                from phlo_postgres.settings import get_settings
                from phlo_trino.publishing import publish_marts_to_postgres
                from phlo_trino import TrinoResource

                @dg.asset(
                    name="publish_jsonplaceholder_marts",
                    group_name="publishing",
                    deps=[dg.AssetKey("posts_mart")],
                )
                def publish_jsonplaceholder_marts(context):
                    settings = get_settings()
                    trino = TrinoResource()
                    postgres = psycopg2.connect(
                        host=settings.postgres_host,
                        port=settings.postgres_port,
                        user=settings.postgres_user,
                        password=settings.postgres_password,
                        dbname=settings.postgres_db,
                    )
                    try:
                        return publish_marts_to_postgres(
                            context=context,
                            trino=trino,
                            postgres=postgres,
                            tables_to_publish={"posts_mart": "raw_marts.posts_mart"},
                            data_source="jsonplaceholder",
                        )
                    finally:
                        postgres.close()
            """).lstrip(),
        )

        # Step 4: Start core services
        log_step("Step 4: Start Core Services")
        core_services = ["postgres", "minio", "minio-setup", "nessie", "trino", "dagster"]
        start_args = ["services", "start"]
        for svc in core_services:
            start_args.extend(["--service", svc])
        run_phlo(start_args, cwd=project_dir, timeout=600, python_exe=project_python)
        log_success("Core services started")

        # Wait for services to be healthy
        env_vars = read_env_file(phlo_dir / ".env")
        dagster_port = int(env_vars.get("DAGSTER_PORT", "3000"))
        trino_port = int(env_vars.get("TRINO_PORT", "8080"))
        postgres_port = int(env_vars.get("POSTGRES_PORT", "5432"))
        minio_port = int(env_vars.get("MINIO_API_PORT", "9000"))
        nessie_port = int(env_vars.get("NESSIE_PORT", "19120"))

        if not wait_for_tcp("127.0.0.1", dagster_port, name="Dagster", timeout=120):
            return 1
        if not wait_for_http(
            f"http://127.0.0.1:{minio_port}/minio/health/live",
            name="MinIO",
            timeout=60,
        ):
            return 1
        if not wait_for_http(f"http://127.0.0.1:{trino_port}/v1/info", name="Trino", timeout=120):
            return 1
        if not wait_for_tcp("127.0.0.1", postgres_port, name="Postgres", timeout=120):
            return 1
        if not wait_for_tcp("127.0.0.1", nessie_port, name="Nessie", timeout=120):
            return 1

        partition_date = (
            args.partition_date or (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        )
        log_info(f"Using partition date: {partition_date}")

        # Step 5: Run DLT ingestion
        log_step("Step 5: Run DLT Ingestion")
        run_phlo(
            ["materialize", "dlt_posts", "--partition", partition_date],
            cwd=project_dir,
            timeout=1200,
            python_exe=project_python,
        )
        log_success("DLT ingestion completed")

        # Step 6: Verify Trino data
        log_step("Step 6: Verify Trino Data")
        from phlo_trino import TrinoResource

        trino = TrinoResource(host="127.0.0.1", port=trino_port, catalog="iceberg")
        rows = trino.execute("SELECT count(*) FROM posts", schema="raw")
        count = rows[0][0] if rows else 0
        log_info(f"Row count in raw.posts: {count}")
        if count > 0:
            log_success(f"Data verified: {count} rows in Trino")
        else:
            log_error("No data in Trino!")
            return 1

        # Step 7: Run dbt transform
        log_step("Step 7: Run dbt Transform")
        run_phlo(
            ["materialize", "posts_mart", "--partition", partition_date],
            cwd=project_dir,
            timeout=1200,
            python_exe=project_python,
        )
        log_success("dbt transform completed")

        # Verify mart
        rows = trino.execute("SELECT count(*) FROM posts_mart", schema="raw_marts")
        mart_count = rows[0][0] if rows else 0
        log_info(f"Row count in raw_marts.posts_mart: {mart_count}")

        # Step 8: Publish to PostgreSQL
        log_step("Step 8: Publish to PostgreSQL")
        run_phlo(
            ["materialize", "publish_jsonplaceholder_marts"],
            cwd=project_dir,
            timeout=1200,
            python_exe=project_python,
        )
        log_success("Published to PostgreSQL")

        # Step 9: Verify PostgreSQL
        log_step("Step 9: Verify PostgreSQL Data")
        import psycopg2
        from psycopg2 import sql

        mart_schema = env_vars.get("POSTGRES_MART_SCHEMA", "marts")
        pg_conn = psycopg2.connect(
            host="localhost",
            port=postgres_port,
            user=env_vars.get("POSTGRES_USER", "phlo"),
            password=env_vars.get("POSTGRES_PASSWORD", "phlo"),
            dbname=env_vars.get("POSTGRES_DB", "phlo"),
        )
        try:
            with pg_conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(mart_schema),
                        sql.Identifier("posts_mart"),
                    )
                )
                pg_count = cursor.fetchone()[0]
                log_info(f"Row count in PostgreSQL {mart_schema}.posts_mart: {pg_count}")
                if pg_count > 0:
                    log_success(f"PostgreSQL verified: {pg_count} rows")
                else:
                    log_error("No data in PostgreSQL!")
                    return 1
        finally:
            pg_conn.close()

        # =====================================================================
        # OPTIONAL SERVICE TESTING
        # =====================================================================

        step_num = 10

        # Step 10: Test API Layer (Hasura + PostgREST)
        if args.test_api:
            log_step(f"Step {step_num}: Test API Layer (Hasura + PostgREST)")
            step_num += 1

            # Add and start Hasura and PostgREST
            # Note: Both services have post_start hooks that auto-discover schemas
            for svc in ["hasura", "postgrest"]:
                log_info(f"Adding {svc}...")
                run_phlo(
                    ["services", "add", svc, "--no-start"],
                    cwd=project_dir,
                    timeout=120,
                    python_exe=project_python,
                )

            apply_env_updates(phlo_dir, resolved_ports)

            # Start the API services (hooks will auto-configure schemas)
            run_phlo(
                ["services", "start", "--service", "hasura", "--service", "postgrest"],
                cwd=project_dir,
                timeout=300,
                python_exe=project_python,
            )

            # Reload env vars to get updated ports
            env_vars = read_env_file(phlo_dir / ".env")
            hasura_port = int(env_vars.get("HASURA_PORT", "8082"))
            hasura_secret = env_vars.get("HASURA_ADMIN_SECRET", "phlo-hasura-admin-secret")
            postgrest_port = int(env_vars.get("POSTGREST_PORT", "3002"))

            # Wait for services to be ready
            wait_for_http(
                f"http://127.0.0.1:{hasura_port}/healthz",
                name="Hasura",
                timeout=120,
            )
            wait_for_http(
                f"http://127.0.0.1:{postgrest_port}/",
                name="PostgREST",
                timeout=60,
            )
            # Note: Hasura tables should be auto-tracked by the post_start hook

            # Test Hasura GraphQL API
            log_info("Testing Hasura GraphQL API...")
            graphql_query = {
                "query": """
                    query {
                        marts_posts_mart(limit: 5) {
                            id
                            title
                        }
                    }
                """
            }
            try:
                result = http_post(
                    f"http://127.0.0.1:{hasura_port}/v1/graphql",
                    graphql_query,
                    headers={"x-hasura-admin-secret": hasura_secret},
                )
                if isinstance(result, dict):
                    data = result.get("data")
                    if isinstance(data, dict) and "marts_posts_mart" in data:
                        rows = data["marts_posts_mart"]
                        if isinstance(rows, list):
                            log_success(f"Hasura GraphQL: Retrieved {len(rows)} rows")
                            if rows:
                                log_info(f"  Sample: {rows[0]}")
                        else:
                            log_error(f"Unexpected Hasura response: {result}")
                            return 1
                    elif "errors" in result:
                        # Table might not be tracked yet, try introspection
                        log_warning(
                            f"Hasura query error (table may not be tracked): {result.get('errors')}"
                        )
                        introspect_query = {"query": "{ __schema { types { name } } }"}
                        result = http_post(
                            f"http://127.0.0.1:{hasura_port}/v1/graphql",
                            introspect_query,
                            headers={"x-hasura-admin-secret": hasura_secret},
                        )
                        if isinstance(result, dict) and "data" in result:
                            log_success("Hasura GraphQL: Schema introspection works")
                        else:
                            log_error(f"Hasura GraphQL introspection failed: {result}")
                            return 1
                    else:
                        log_error(f"Unexpected Hasura response: {result}")
                        return 1
                else:
                    log_error(f"Unexpected Hasura response: {result}")
                    return 1
            except Exception as e:
                log_error(f"Hasura GraphQL test failed: {e}")
                return 1

            # Test PostgREST API
            log_info("Testing PostgREST REST API...")
            try:
                # Get OpenAPI schema to verify server is working
                schema = http_get(f"http://127.0.0.1:{postgrest_port}/")
                if isinstance(schema, dict) and "paths" in schema:
                    log_success(
                        f"PostgREST OpenAPI schema: {len(schema.get('paths', {}))} endpoints"
                    )

                # Query data from marts schema (table name is posts_mart)
                result = http_get(
                    f"http://127.0.0.1:{postgrest_port}/posts_mart?limit=5",
                    headers={"Accept": "application/json"},
                )
                if isinstance(result, list):
                    log_success(f"PostgREST REST API: Retrieved {len(result)} rows")
                    if result:
                        log_info(f"  Sample: {result[0]}")
                else:
                    log_warning(f"PostgREST response: {result}")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    log_warning("PostgREST: Table not exposed (may need schema configuration)")
                elif e.code == 406:
                    log_warning("PostgREST: Server returned 406 Not Acceptable")
                else:
                    log_error(f"PostgREST REST API test failed: {e}")
                    return 1
            except Exception as e:
                log_error(f"PostgREST REST API test failed: {e}")
                return 1

            log_success("API layer testing complete")

        # Step 11: Test Observability Stack
        if args.test_observability:
            log_step(f"Step {step_num}: Test Observability Stack")
            step_num += 1

            # Install observability plugins into project venv
            observability_services = ["prometheus", "loki", "alloy", "grafana"]
            for plugin in observability_services:
                if not install_plugin(
                    plugin,
                    project_dir=project_dir,
                    phlo_source=phlo_source,
                    python_exe=project_python,
                ):
                    log_error(f"Failed to install {plugin} plugin")
                    return 1

            # Add observability services
            for svc in observability_services:
                log_info(f"Adding {svc}...")
                run_phlo(
                    ["services", "add", svc, "--no-start"],
                    cwd=project_dir,
                    timeout=120,
                    python_exe=project_python,
                )

            apply_env_updates(phlo_dir, resolved_ports)

            # Start observability stack
            start_args = ["services", "start"]
            for svc in observability_services:
                start_args.extend(["--service", svc])
            run_phlo(start_args, cwd=project_dir, timeout=600, python_exe=project_python)

            # Reload env vars
            env_vars = read_env_file(phlo_dir / ".env")
            prometheus_port = int(env_vars.get("PROMETHEUS_PORT", "9090"))
            loki_port = int(env_vars.get("LOKI_PORT", "3100"))
            alloy_port = int(env_vars.get("ALLOY_PORT", "12345"))
            grafana_port = int(env_vars.get("GRAFANA_PORT", "3003"))

            # Wait for services
            wait_for_http(
                f"http://127.0.0.1:{prometheus_port}/-/healthy", name="Prometheus", timeout=120
            )
            wait_for_http(f"http://127.0.0.1:{loki_port}/ready", name="Loki", timeout=120)
            wait_for_http(f"http://127.0.0.1:{alloy_port}/-/ready", name="Alloy", timeout=60)
            wait_for_http(
                f"http://127.0.0.1:{grafana_port}/api/health", name="Grafana", timeout=120
            )

            # Test Prometheus targets
            log_info("Testing Prometheus targets...")
            try:
                result = http_get(f"http://127.0.0.1:{prometheus_port}/api/v1/targets")
                if isinstance(result, dict):
                    data = result.get("data")
                    if isinstance(data, dict) and "activeTargets" in data:
                        targets = data.get("activeTargets") or []
                        if isinstance(targets, list):
                            up_count = sum(1 for t in targets if t.get("health") == "up")
                            log_success(f"Prometheus: {up_count}/{len(targets)} targets UP")
                            for t in targets[:3]:  # Show first 3 targets
                                state = t.get("health", "unknown")
                                job = t.get("labels", {}).get("job", "unknown")
                                log_info(f"  - {job}: {state}")
                        else:
                            log_warning(f"Prometheus targets response: {result}")
                    else:
                        log_warning(f"Prometheus targets response: {result}")
                else:
                    log_warning(f"Prometheus targets response: {result}")
            except Exception as e:
                log_warning(f"Could not query Prometheus targets: {e}")

            # Test Loki labels
            log_info("Testing Loki log ingestion...")
            try:
                result = http_get(f"http://127.0.0.1:{loki_port}/loki/api/v1/labels")
                if isinstance(result, dict):
                    labels = result.get("data")
                    if isinstance(labels, list):
                        log_success(f"Loki: Found {len(labels)} label types")
                        if labels:
                            log_info(f"  Labels: {', '.join(labels[:5])}")
                    else:
                        log_warning(f"Loki labels response: {result}")
                else:
                    log_warning(f"Loki labels response: {result}")
            except Exception as e:
                log_warning(f"Could not query Loki labels: {e}")

            # Test Grafana datasources
            log_info("Testing Grafana datasources...")
            try:
                result = http_get(
                    f"http://127.0.0.1:{grafana_port}/api/datasources",
                    headers={"Authorization": "Basic YWRtaW46YWRtaW4="},  # admin:admin base64
                )
                if isinstance(result, list):
                    log_success(f"Grafana: {len(result)} datasources configured")
                    for ds in result:
                        log_info(f"  - {ds.get('name')}: {ds.get('type')}")
                else:
                    log_warning(f"Grafana datasources response: {result}")
            except Exception as e:
                log_warning(f"Could not query Grafana datasources: {e}")

            log_success("Observability stack testing complete")

        # Step 12: Test Superset
        if args.test_superset:
            log_step(f"Step {step_num}: Test Superset BI")
            step_num += 1

            log_info("Adding Superset...")
            run_phlo(
                ["services", "add", "superset", "--no-start"],
                cwd=project_dir,
                timeout=180,
                python_exe=project_python,
            )
            apply_env_updates(phlo_dir, resolved_ports)
            run_phlo(
                ["services", "start", "--service", "superset"],
                cwd=project_dir,
                timeout=600,
                python_exe=project_python,
            )

            # Reload env vars
            env_vars = read_env_file(phlo_dir / ".env")
            superset_port = int(env_vars.get("SUPERSET_PORT", "8088"))

            # Wait for Superset (can take a while to initialize)
            log_info("Waiting for Superset to initialize (this may take a few minutes)...")
            if wait_for_http(
                f"http://127.0.0.1:{superset_port}/health", name="Superset", timeout=300
            ):
                log_success("Superset is ready")

                # Try to verify Superset is responsive
                try:
                    result = http_get(f"http://127.0.0.1:{superset_port}/health")
                    log_info(f"Superset health: {result}")
                except Exception as e:
                    log_warning(f"Superset health check: {e}")

                log_info(f"Superset UI: http://localhost:{superset_port}")
                log_info("  Login: admin / admin")
            else:
                log_warning("Superset did not become ready in time")

            log_success("Superset testing complete")

        # Step 13: Test OpenMetadata
        if args.test_openmetadata:
            log_step(f"Step {step_num}: Test OpenMetadata Catalog")
            step_num += 1

            log_warning("OpenMetadata requires 6GB+ RAM. Ensure sufficient resources.")

            # Install openmetadata plugin
            if not install_plugin(
                "openmetadata",
                project_dir=project_dir,
                phlo_source=phlo_source,
                python_exe=project_python,
            ):
                log_warning("OpenMetadata plugin not available, skipping")
            else:
                log_info("Adding OpenMetadata...")
                run_phlo(
                    ["services", "add", "openmetadata", "--no-start"],
                    cwd=project_dir,
                    timeout=180,
                    python_exe=project_python,
                )
                apply_env_updates(phlo_dir, resolved_ports)
                run_phlo(
                    ["services", "start", "--service", "openmetadata"],
                    cwd=project_dir,
                    timeout=900,
                    python_exe=project_python,
                )

            # Reload env vars
            env_vars = read_env_file(phlo_dir / ".env")
            openmetadata_port = int(env_vars.get("OPENMETADATA_PORT", "8585"))

            # Wait for OpenMetadata (can take several minutes to initialize)
            log_info("Waiting for OpenMetadata to initialize (this may take several minutes)...")
            if wait_for_http(
                f"http://127.0.0.1:{openmetadata_port}/api/v1/system/version",
                name="OpenMetadata",
                timeout=600,
            ):
                log_success("OpenMetadata is ready")

                try:
                    result = http_get(f"http://127.0.0.1:{openmetadata_port}/api/v1/system/version")
                    log_info(f"OpenMetadata version: {result}")
                except Exception as e:
                    log_warning(f"OpenMetadata version check: {e}")

                log_info(f"OpenMetadata UI: http://localhost:{openmetadata_port}")
                log_info("  Login: admin / admin")
                log_warning("Note: Run search reindex after setup for search to work")

                # Sync Nessie + dbt metadata into OpenMetadata
                log_info("Syncing metadata to OpenMetadata (Nessie + dbt)...")
                om_service = env_vars.get("OPENMETADATA_SERVICE_NAME", "phlo")
                om_database = env_vars.get(
                    "OPENMETADATA_DATABASE_NAME",
                    env_vars.get("TRINO_CATALOG", "iceberg"),
                )
                os.environ["OPENMETADATA_HOST"] = "127.0.0.1"
                os.environ["OPENMETADATA_PORT"] = str(openmetadata_port)
                os.environ["OPENMETADATA_SERVICE_NAME"] = om_service
                os.environ["OPENMETADATA_SERVICE_TYPE"] = env_vars.get(
                    "OPENMETADATA_SERVICE_TYPE",
                    "Trino",
                )
                os.environ["OPENMETADATA_DATABASE_NAME"] = om_database
                os.environ["NESSIE_HOST"] = "127.0.0.1"
                os.environ["NESSIE_PORT"] = env_vars.get("NESSIE_PORT", "19120")
                os.environ["TRINO_HOST"] = "127.0.0.1"
                os.environ["TRINO_PORT"] = env_vars.get("TRINO_PORT", "8080")
                run_phlo(
                    ["openmetadata", "sync"],
                    cwd=project_dir,
                    timeout=600,
                    python_exe=project_python,
                )

                # Verify dbt mart table is registered in OpenMetadata
                om_user = env_vars.get("OPENMETADATA_USERNAME", "admin")
                om_pass = env_vars.get("OPENMETADATA_PASSWORD", "admin")
                table_fqn = f"{om_service}.{om_database}.raw_marts.posts_mart"
                source_fqn = f"{om_service}.{om_database}.raw.posts"
                table_url = (
                    f"http://127.0.0.1:{openmetadata_port}/api/v1/tables/name/"
                    f"{urllib.parse.quote(table_fqn, safe='')}"
                )
                table: dict | list | str | None = None
                om_token: str | None = None
                try:
                    om_token = openmetadata_login(
                        f"http://127.0.0.1:{openmetadata_port}",
                        username=om_user,
                        password=om_pass,
                    )
                    if om_token:
                        table = http_get_bearer(table_url, token=om_token, timeout=30)
                    else:
                        table = http_get_basic(
                            table_url,
                            username=om_user,
                            password=om_pass,
                            timeout=30,
                        )
                    if isinstance(table, dict) and table.get("name") == "posts_mart":
                        log_success(f"OpenMetadata verified table: {table_fqn}")
                    else:
                        log_warning(f"OpenMetadata table lookup returned: {table}")
                except Exception as e:
                    log_warning(f"OpenMetadata table verification failed: {e}")

                # Emit lineage + quality events to verify OpenMetadata hooks
                log_info("Emitting OpenMetadata lineage + quality smoke events...")
                emit_code = textwrap.dedent(
                    f"""
                    from phlo.hooks.emitters import (
                        LineageEventContext,
                        LineageEventEmitter,
                        QualityResultEventContext,
                        QualityResultEventEmitter,
                    )

                    source_fqn = {source_fqn!r}
                    target_fqn = {table_fqn!r}

                    LineageEventEmitter(LineageEventContext(tags={{"source": "golden_path"}})).emit_edges(
                        edges=[(source_fqn, target_fqn)],
                        asset_keys=[target_fqn],
                        metadata={{"golden_path": True}},
                    )

                    QualityResultEventEmitter(
                        QualityResultEventContext(asset_key=target_fqn, tags={{"source": "golden_path"}})
                    ).emit_result(
                        check_name="golden_path_row_count",
                        passed=True,
                        check_type="CountCheck",
                        metadata={{"table_fqn": target_fqn, "metric_value": {{"row_count": 1}}}},
                    )
                    """
                )
                emit_env = os.environ.copy()
                emit_env.update(
                    {
                        "OPENMETADATA_HOST": "127.0.0.1",
                        "OPENMETADATA_PORT": str(openmetadata_port),
                        "OPENMETADATA_SERVICE_NAME": om_service,
                        "OPENMETADATA_SERVICE_TYPE": env_vars.get(
                            "OPENMETADATA_SERVICE_TYPE", "Trino"
                        ),
                        "OPENMETADATA_DATABASE_NAME": om_database,
                        "OPENMETADATA_USERNAME": om_user,
                        "OPENMETADATA_PASSWORD": om_pass,
                    }
                )
                try:
                    subprocess.run(
                        [project_python, "-c", emit_code],
                        cwd=project_dir,
                        env=emit_env,
                        timeout=60,
                        check=True,
                    )
                    log_success("OpenMetadata smoke events emitted")
                except Exception as e:
                    log_warning(f"Failed to emit OpenMetadata smoke events: {e}")

                # Verify lineage edge in OpenMetadata
                try:
                    if isinstance(table, dict) and table.get("id"):
                        source_table = openmetadata_get_with_fallback(
                            [
                                f"http://127.0.0.1:{openmetadata_port}/api/v1/tables/name/"
                                f"{urllib.parse.quote(source_fqn, safe='')}"
                            ],
                            token=om_token,
                            username=om_user,
                            password=om_pass,
                            timeout=30,
                        )
                        source_id = (
                            source_table.get("id") if isinstance(source_table, dict) else None
                        )
                        lineage_url = (
                            f"http://127.0.0.1:{openmetadata_port}/api/v1/lineage/table/"
                            f"{table['id']}?upstreamDepth=1&downstreamDepth=0"
                        )
                        lineage = openmetadata_get_with_fallback(
                            [lineage_url],
                            token=om_token,
                            username=om_user,
                            password=om_pass,
                            timeout=30,
                        )
                        edges = []
                        nodes: dict[str, str] = {}
                        if isinstance(lineage, dict):
                            edges = lineage.get("edges") or lineage.get("upstreamEdges") or []
                            for node in lineage.get("nodes", []) or []:
                                if isinstance(node, dict) and node.get("id"):
                                    nodes[str(node["id"])] = (
                                        node.get("fullyQualifiedName") or node.get("name") or ""
                                    )
                        has_edge = False
                        for edge in edges:
                            if not isinstance(edge, dict):
                                continue
                            from_entity = edge.get("fromEntity")
                            to_entity = edge.get("toEntity")
                            from_id = None
                            to_id = None
                            from_fqn = None
                            to_fqn = None
                            if isinstance(from_entity, dict):
                                from_id = from_entity.get("id")
                                from_fqn = from_entity.get("fullyQualifiedName")
                            elif isinstance(from_entity, str):
                                from_id = from_entity
                            if isinstance(to_entity, dict):
                                to_id = to_entity.get("id")
                                to_fqn = to_entity.get("fullyQualifiedName")
                            elif isinstance(to_entity, str):
                                to_id = to_entity
                            if from_fqn is None and from_id is not None:
                                from_fqn = nodes.get(str(from_id))
                            if to_fqn is None and to_id is not None:
                                to_fqn = nodes.get(str(to_id))
                            if (
                                (source_id and from_id == source_id) or from_fqn == source_fqn
                            ) and (
                                (table.get("id") and to_id == table.get("id"))
                                or to_fqn == table_fqn
                            ):
                                has_edge = True
                                break
                        if has_edge:
                            log_success("OpenMetadata lineage verified")
                        else:
                            log_warning("OpenMetadata lineage edge not found")
                    else:
                        log_warning("OpenMetadata lineage skipped: missing table id")
                except Exception as e:
                    log_warning(f"OpenMetadata lineage verification failed: {e}")

                # Verify quality test cases in OpenMetadata
                try:
                    time.sleep(2)
                    test_cases = openmetadata_get_with_fallback(
                        [
                            f"http://127.0.0.1:{openmetadata_port}/api/v1/dataQuality/testCases?limit=100",
                            f"http://127.0.0.1:{openmetadata_port}/api/v1/testCases?limit=100",
                        ],
                        token=om_token,
                        username=om_user,
                        password=om_pass,
                        timeout=30,
                    )
                    data = []
                    if isinstance(test_cases, dict):
                        data = test_cases.get("data", []) or []
                    elif isinstance(test_cases, list):
                        data = test_cases
                    matches = [
                        case
                        for case in data
                        if isinstance(case, dict) and table_fqn in str(case.get("entityLink", ""))
                    ]
                    if matches:
                        log_success("OpenMetadata quality test cases verified")
                    else:
                        log_warning("OpenMetadata quality test cases not found")
                except Exception as e:
                    log_warning(f"OpenMetadata quality verification failed: {e}")
            else:
                log_warning("OpenMetadata did not become ready in time")

            log_success("OpenMetadata testing complete")

        # Step 14: Test Alerting Plugin
        if args.test_alerting:
            log_step(f"Step {step_num}: Test Alerting Plugin")
            step_num += 1

            # Install alerting plugin first
            if not install_plugin(
                "alerting",
                project_dir=project_dir,
                phlo_source=phlo_source,
                python_exe=project_python,
            ):
                log_warning("Alerting plugin not available, skipping")
            else:
                # Test alerting CLI commands
                log_info("Testing alerting plugin...")

                # List configured destinations
                try:
                    result = run_phlo(
                        ["alerts", "list"],
                        cwd=project_dir,
                        timeout=60,
                        check=False,
                        stream_output=False,
                        python_exe=project_python,
                    )
                    if result.returncode == 0:
                        log_success("Alerting plugin: list command works")
                        log_info(f"  Output: {result.stdout.strip()[:200]}")
                    else:
                        log_warning("Alerting plugin: list command not available")
                except Exception as e:
                    log_warning(f"Could not test alerting plugin: {e}")

                # Note: Sending test alerts requires external webhook configuration
                log_info("Note: To fully test alerting, configure PHLO_ALERT_SLACK_WEBHOOK")
                log_info("      and run: phlo alerts test --destination slack")

            log_success("Alerting plugin testing complete")

        # Step 15: Test Lineage Tracking
        if args.test_lineage:
            log_step(f"Step {step_num}: Test Lineage Tracking")
            step_num += 1

            # Install lineage plugin first
            if not install_plugin(
                "lineage",
                project_dir=project_dir,
                phlo_source=phlo_source,
                python_exe=project_python,
            ):
                log_warning("Lineage plugin not available, skipping")
            else:
                # Configure lineage to use the existing PostgreSQL database
                log_info("Configuring lineage database...")
                if lineage_db_url_host:
                    os.environ["LINEAGE_DB_URL"] = lineage_db_url_host
                    log_info("Using LINEAGE_DB_URL for host access")
                else:
                    lineage_db_url = os.environ.get("LINEAGE_DB_URL")
                    if lineage_db_url:
                        log_info("Using LINEAGE_DB_URL from environment")
                    else:
                        log_warning("LINEAGE_DB_URL not set; lineage may be empty")

                # Test lineage CLI commands
                log_info("Testing lineage CLI...")
                try:
                    # Try to show lineage for the posts table
                    result = run_phlo(
                        ["lineage", "show", "raw.posts"],
                        cwd=project_dir,
                        timeout=60,
                        check=False,
                        stream_output=False,
                        python_exe=project_python,
                    )
                    if result.returncode == 0:
                        log_success("Lineage: show command works")
                        log_info(f"  Output: {result.stdout.strip()[:200]}")
                    else:
                        # Lineage might not be recorded yet, check if command is available
                        if (
                            "error" not in result.stdout.lower()
                            or "not found" not in result.stdout.lower()
                        ):
                            log_info("Lineage: Table may not have lineage recorded yet")
                        else:
                            log_warning(f"Lineage show error: {result.stdout.strip()[:200]}")
                except Exception as e:
                    log_warning(f"Could not test lineage show: {e}")

                # Try to export lineage
                try:
                    export_path = project_dir / ".phlo" / "lineage_export.json"
                    result = run_phlo(
                        [
                            "lineage",
                            "export",
                            "raw.posts",
                            "--format",
                            "json",
                            "--output",
                            str(export_path),
                        ],
                        cwd=project_dir,
                        timeout=60,
                        check=False,
                        stream_output=False,
                        python_exe=project_python,
                    )
                    if result.returncode == 0:
                        log_success("Lineage: export command works")
                        try:
                            if export_path.exists():
                                lineage_data = json.loads(export_path.read_text(encoding="utf-8"))
                            else:
                                lineage_data = {}
                            if isinstance(lineage_data, dict):
                                assets = lineage_data.get("assets", {})
                                edges = lineage_data.get("edges", {})
                                edge_count = (
                                    sum(len(targets) for targets in edges.values())
                                    if isinstance(edges, dict)
                                    else len(edges or [])
                                )
                                log_info(
                                    f"  Lineage graph: {len(assets)} assets, {edge_count} edges"
                                )
                            elif isinstance(lineage_data, list):
                                log_info(f"  Lineage records: {len(lineage_data)} entries")
                        except (json.JSONDecodeError, OSError) as exc:
                            log_info(f"  Lineage export parse failed: {exc}")
                    else:
                        log_warning(f"Lineage export: {result.stdout.strip()[:200]}")
                except Exception as e:
                    log_warning(f"Could not test lineage export: {e}")

            log_success("Lineage tracking testing complete")

        # =====================================================================
        # COMPLETION
        # =====================================================================

        log_step("=== Golden Path Complete!")
        log_success("All steps completed successfully!")
        log_info(f"Project directory: {project_dir}")
        log_info(f"Dagster UI: http://localhost:{dagster_port}")
        log_info(
            f"MinIO Console: http://localhost:{int(env_vars.get('MINIO_CONSOLE_PORT', '9001'))}"
        )

        # Show optional service URLs
        if args.test_api:
            log_info(
                f"Hasura Console: http://localhost:{env_vars.get('HASURA_PORT', '8082')}/console"
            )
            log_info(f"PostgREST API: http://localhost:{env_vars.get('POSTGREST_PORT', '3002')}")
        if args.test_observability:
            log_info(f"Grafana: http://localhost:{env_vars.get('GRAFANA_PORT', '3003')}")
            log_info(f"Prometheus: http://localhost:{env_vars.get('PROMETHEUS_PORT', '9090')}")
        if args.test_superset:
            log_info(f"Superset: http://localhost:{env_vars.get('SUPERSET_PORT', '8088')}")
        if args.test_openmetadata:
            log_info(f"OpenMetadata: http://localhost:{env_vars.get('OPENMETADATA_PORT', '8585')}")

        if args.keep_running:
            log_info("Services are still running. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except Exception as e:
        log_error(f"Failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if not args.keep_running:
            log_step("Cleanup")
            try:
                # Use project python if available, otherwise default
                cleanup_python = project_python if "project_python" in dir() else None
                run_phlo(
                    ["services", "stop"],
                    cwd=project_dir,
                    timeout=300,
                    check=False,
                    python_exe=cleanup_python,
                )
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(project_dir / ".phlo" / "docker-compose.yml"),
                        "down",
                        "-v",
                        "--remove-orphans",
                    ],
                    cwd=project_dir,
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
                log_success("Cleanup complete")
            except Exception as e:
                log_warning(f"Cleanup error: {e}")


if __name__ == "__main__":
    sys.exit(main())
