"""
Pytest configuration and shared fixtures for Phlo tests.

This conftest.py imports fixtures from phlo_testing and makes them available
to all tests in the repository.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Disable Telemetry aggressively at system level before any imports
# Note: Lowercase 'false' is safer for TOML-based config parsers in dlt
os.environ["DLT__RUNTIME__DLTHUB_TELEMETRY"] = "false"
os.environ["DLT__RUNTIME__DLTHUB_TELEMETRY_ENDPOINT"] = "http://localhost/donotcall"
os.environ["DLT_TELEMETRY_DISABLED"] = "1"
os.environ["DAGSTER_TELEMETRY_ENABLED"] = "False"
os.environ["DAGSTER_DISABLE_TELEMETRY"] = "True"
os.environ["DO_NOT_TRACK"] = "1"  # General standard

import contextlib
import importlib.util
import logging

import pytest

logger = logging.getLogger(__name__)

# Add src to path for imports
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Add workspace package sources for local test runs (monorepo layout)
packages_dir = Path(__file__).parent / "packages"
if packages_dir.exists():
    for package_src in packages_dir.glob("*/src"):
        if str(package_src) not in sys.path:
            sys.path.insert(0, str(package_src))

_PHLO_TESTING_FIXTURE_MODULES = (
    "dagster",
    "dagster_graphql",
    "duckdb",
    "pandas",
)

# Load the shared fixture modules only when their heavy dependencies are
# installed; otherwise collection would fail on environments that have only
# the core package.
pytest_plugins = (
    ("phlo_testing.fixtures",)
    if all(
        importlib.util.find_spec(module_name) is not None
        for module_name in _PHLO_TESTING_FIXTURE_MODULES
    )
    else ()
)


def _register_workspace_plugins() -> None:
    # Best effort: workspaces without the phlo_dlt package installed must
    # still be able to collect and run the rest of the suite.
    try:
        from phlo_dlt.plugin import DltAssetProvider

        from phlo.plugins.discovery import get_global_registry
    except Exception:
        return

    registry = get_global_registry()
    try:
        registry.register("asset_provider", DltAssetProvider(), replace=True)
    except ValueError as exc:
        logger.debug(
            "Failed to register DltAssetProvider for workspace tests",
            exc_info=exc,
        )
        return


_register_workspace_plugins()

# Import fixtures from phlo_testing - these are auto-discovered by pytest


def _minio_container_endpoint(minio_service) -> str:
    """Return the HTTP endpoint for the current testcontainers MinIO API."""
    if hasattr(minio_service, "get_url"):
        return minio_service.get_url()
    if hasattr(minio_service, "get_config"):
        endpoint = (minio_service.get_config() or {}).get("endpoint")
        if isinstance(endpoint, str) and endpoint:
            return (
                endpoint if endpoint.startswith(("http://", "https://")) else f"http://{endpoint}"
            )
    host = minio_service.get_container_host_ip()
    port = minio_service.get_exposed_port(getattr(minio_service, "port_to_expose", 9000))
    return f"http://{host}:{port}"


@pytest.fixture
def configured_minio_object_store(minio_service, monkeypatch):
    """Register MinIO's object-store capability for tests that need S3 metadata."""
    if not minio_service:
        return None

    endpoint = _minio_container_endpoint(minio_service)
    parsed_endpoint = urlparse(endpoint)
    endpoint_host = parsed_endpoint.hostname or "127.0.0.1"
    endpoint_port = parsed_endpoint.port or 80

    monkeypatch.setenv("MINIO_HOST", endpoint_host)
    monkeypatch.setenv("MINIO_API_PORT", str(endpoint_port))
    monkeypatch.setenv("MINIO_ROOT_USER", minio_service.access_key)
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", minio_service.secret_key)

    from phlo_minio.plugin import MinioResourceProvider
    from phlo_minio.settings import get_settings as get_minio_settings

    from phlo.capabilities import register_capability, resolve_capability

    get_minio_settings.cache_clear()
    register_capability("object_store", MinioResourceProvider().get_object_stores()[0])
    return resolve_capability("object_store", "minio")


@pytest.fixture(autouse=True)
def reset_test_env(monkeypatch):
    """Reset environment variables before each test."""
    monkeypatch.setenv("PHLO_ENV", "test")
    monkeypatch.setenv("PHLO_LOG_LEVEL", "DEBUG")
    # Disable DLT telemetry
    monkeypatch.setenv("DLT__RUNTIME__DLTHUB_TELEMETRY", "False")
    monkeypatch.setenv("DLT_TELEMETRY_DISABLED", "1")

    # Disable Dagster telemetry
    monkeypatch.setenv("DAGSTER_TELEMETRY_ENABLED", "False")
    monkeypatch.setenv("DAGSTER_DISABLE_TELEMETRY", "True")  # Older variants


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch):
    """
    Mock DNS resolver to block external calls and force localhost for telemetry.
    This prevents 'NameResolutionError' in restricted environments.
    """
    import socket

    real_getaddrinfo = socket.getaddrinfo

    def side_effect(host, port, family=0, type=0, proto=0, flags=0):
        # Allow local
        if host in ["localhost", "127.0.0.1", "::1"]:
            return real_getaddrinfo(host, port, family, type, proto, flags)

        # Block telemetry domains by routing to localhost (fails fast)
        if hasattr(host, "lower") and any(
            x in host.lower() for x in ["scalevector", "dlthub", "dagster", "segment"]
        ):
            return real_getaddrinfo("127.0.0.1", port, family, type, proto, flags)

        # Fall back for internal service aliases commonly used in compose configs.
        # This removes hidden dependence on local DNS entries during host-side tests.
        try:
            return real_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            if hasattr(host, "lower") and host.lower() in {"minio", "nessie"}:
                return real_getaddrinfo("127.0.0.1", port, family, type, proto, flags)
            raise

    monkeypatch.setattr(socket, "getaddrinfo", side_effect)


@pytest.fixture
def project_root() -> Path:
    """Return path to project root."""
    return Path(__file__).parent


@pytest.fixture(scope="session")
def minio_service():
    """Spin up a MinIO container for integration tests."""
    try:
        import docker
        from testcontainers.minio import MinioContainer

        # Try to verify docker access early
        client = docker.from_env()
        client.ping()
    except (ImportError, Exception):
        # Fallback to None if Docker is unavailable (very common in CI/Sandbox)
        yield None
        return

    try:
        with MinioContainer("minio/minio:latest") as minio:
            yield minio
    # A container that fails to start degrades to None exactly like the
    # no-Docker path, so integration tests fall back to the local filesystem.
    except Exception:
        yield None


@pytest.fixture
def iceberg_catalog(configured_minio_object_store, tmp_path):
    """
    Return a PyIceberg catalog.
    If minio_service is available, uses MinIO (S3).
    Otherwise, uses local filesystem.
    """
    from pyiceberg.catalog import load_catalog

    catalog_config = {
        "type": "sql",
        "uri": f"sqlite:///{tmp_path}/catalog.db",
    }

    if configured_minio_object_store:
        provider = configured_minio_object_store.provider
        config = provider.to_sling_connection()
        warehouse_path = "s3://warehouse"
        catalog_config.update(
            {
                "warehouse": warehouse_path,
                "s3.endpoint": config["endpoint"],
                "s3.access-key-id": config["access_key_id"],
                "s3.secret-access-key": config["secret_access_key"],
                "s3.region": config["region"],
                "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
            }
        )
    else:
        # Local fallback
        warehouse_path = f"file://{tmp_path}/warehouse"
        catalog_config.update(
            {
                "warehouse": warehouse_path,
            }
        )

    catalog = load_catalog("default", **catalog_config)

    # Init warehouse
    if configured_minio_object_store:
        import s3fs

        provider = configured_minio_object_store.provider
        config = provider.to_sling_connection()

        fs = s3fs.S3FileSystem(
            endpoint_url=config["endpoint"],
            key=config["access_key_id"],
            secret=config["secret_access_key"],
            client_kwargs={"region_name": config["region"]},
        )
        with contextlib.suppress(FileExistsError):
            fs.mkdir("warehouse")
    else:
        (tmp_path / "warehouse").mkdir(exist_ok=True)

    return catalog
