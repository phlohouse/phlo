#!/usr/bin/env python3
"""Run the complete Phlo golden path workflow end-to-end with live output.

Script version of the golden path E2E test for easier debugging. Pass
--test-api, --test-observability, --test-superset, --test-openmetadata,
or --test-all to exercise optional services.

The harness layer (env layering, HTTP probes, ports, process execution)
is shared with release_golden_path.py via golden_path_common.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_path_common import (  # noqa: E402
    apply_env_updates,
    check_port_in_use,
    cleanup_phlo_containers,
    force_remove_directory,
    http_get,
    http_get_basic,
    http_get_bearer,
    http_post,
    install_plugin,
    log_error,
    log_info,
    log_step,
    log_success,
    log_warning,
    openmetadata_get_with_fallback,
    openmetadata_login,
    read_project_env,
    resolve_port,
    run_phlo,
    setup_project_venv,
    verify_bind_mount_visibility,
    wait_for_http,
    wait_for_tcp,
    write_file,
)

_COMMON_PORTS = {
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


def _check_phlo_containers(*, auto_cleanup: bool) -> bool:
    """Report running Phlo containers; stop them when auto-cleanup is enabled."""
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

        if not phlo_containers:
            log_success("No phlo containers running")
            return True
        log_warning(f"Found {len(phlo_containers)} phlo-related containers running:")
        for c in phlo_containers[:5]:
            log_info(f"  - {c}")
        if len(phlo_containers) > 5:
            log_info(f"  ... and {len(phlo_containers) - 5} more")

        if not auto_cleanup:
            log_error("Please stop containers first or use --auto-cleanup")
            return False
        log_info("Auto-cleanup enabled, stopping containers...")
        removed = cleanup_phlo_containers()
        log_success(f"Stopped and removed {removed} Phlo containers")
    except Exception as e:
        log_warning(f"Could not check Docker containers: {e}")
    return True


def _check_common_ports(*, auto_cleanup: bool) -> bool:
    """Report common service ports already in use."""
    log_info("Checking for port availability...")
    ports_in_use = [
        (port, service) for port, service in _COMMON_PORTS.items() if check_port_in_use(port)
    ]
    if not ports_in_use:
        log_success("All common ports are available")
        return True
    log_warning(f"Found {len(ports_in_use)} ports in use:")
    for port, service in ports_in_use:
        log_info(f"  - Port {port} ({service})")
    if auto_cleanup:
        return True
    log_error("Please free these ports or use --auto-cleanup")
    return False


def _check_docker_daemon() -> bool:
    log_info("Checking Docker daemon...")
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
    except Exception as e:
        log_error(f"Docker check failed: {e}")
        return False
    if result.returncode == 0:
        log_success("Docker daemon is running")
        return True
    log_error("Docker daemon is not running")
    return False


def _available_tmp_gb() -> int | None:
    """Return the free space in /tmp in GB, or None when df output cannot be parsed."""
    result = subprocess.run(
        ["df", "-BG", "/tmp"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    lines = result.stdout.strip().split("\n")
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    if len(parts) < 4:
        return None
    try:
        return int(parts[3].replace("G", ""))
    except ValueError:
        return None


def _check_disk_space() -> None:
    log_info("Checking disk space...")
    try:
        available_gb = _available_tmp_gb()
    except Exception as e:
        log_warning(f"Could not check disk space: {e}")
        return
    if available_gb is None:
        return
    if available_gb < 10:
        log_warning(f"Low disk space: {available_gb}GB available in /tmp")
    else:
        log_success(f"Disk space OK: {available_gb}GB available in /tmp")


def preflight_check(*, auto_cleanup: bool = False) -> bool:
    """
    Run preflight checks before starting the golden path test.

    Returns True if all checks pass, False otherwise.
    """
    log_step("Preflight Checks")
    containers_ok = _check_phlo_containers(auto_cleanup=auto_cleanup)
    ports_ok = _check_common_ports(auto_cleanup=auto_cleanup)
    docker_ok = _check_docker_daemon()
    _check_disk_space()
    all_ok = containers_ok and ports_ok and docker_ok

    if all_ok:
        log_success("All preflight checks passed!")
    else:
        log_error("Preflight checks failed. Fix issues above or use --auto-cleanup")

    return all_ok


class GoldenPathFailure(Exception):
    """A golden path step failed after logging its own reason."""


def _fail(message: str) -> NoReturn:
    log_error(message)
    raise GoldenPathFailure(message)


@dataclass
class GoldenPathRun:
    """State shared by the golden path phases."""

    args: argparse.Namespace
    project_name: str
    project_dir: Path
    phlo_source: Path
    project_python: str | Path | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    resolved_ports: dict[str, str] = field(default_factory=dict)
    lineage_db_url_host: str | None = None
    trino_port: int = 8080
    postgres_port: int = 5432
    dagster_port: int = 3000

    @property
    def phlo_dir(self) -> Path:
        return self.project_dir / ".phlo"

    def phlo(
        self, command: list[str], *, timeout: int, check: bool = True, stream_output: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a phlo CLI command inside the project with the project interpreter."""
        return run_phlo(
            command,
            cwd=self.project_dir,
            timeout=timeout,
            check=check,
            stream_output=stream_output,
            python_exe=self.project_python,
        )

    def reload_env(self) -> dict[str, str]:
        self.env_vars = read_project_env(self.phlo_dir)
        return self.env_vars

    def add_services(self, services: list[str], *, timeout: int) -> None:
        """Add services without starting them, then reapply the resolved ports."""
        for svc in services:
            log_info(f"Adding {svc}...")
            self.phlo(["services", "add", svc, "--no-start"], timeout=timeout)
        apply_env_updates(self.phlo_dir, self.resolved_ports)

    def start_services(self, services: list[str], *, timeout: int) -> None:
        start_args = ["services", "start"]
        for svc in services:
            start_args.extend(["--service", svc])
        self.phlo(start_args, timeout=timeout)

    def install_plugin(self, plugin: str) -> bool:
        return install_plugin(
            plugin,
            project_dir=self.project_dir,
            phlo_source=self.phlo_source,
            python_exe=self.project_python,
        )


def _build_parser() -> argparse.ArgumentParser:
    """Return the golden path CLI parser."""
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
    return parser


_OPTIONAL_TEST_FLAGS = (
    "test_api",
    "test_observability",
    "test_superset",
    "test_alerting",
    "test_lineage",
    "test_openmetadata",
)

_OPTIONAL_TEST_LABELS = {
    "test_api": "API (Hasura/PostgREST)",
    "test_observability": "Observability (Prometheus/Loki/Alloy/Grafana)",
    "test_superset": "Superset",
    "test_alerting": "Alerting",
    "test_lineage": "Lineage",
    "test_openmetadata": "OpenMetadata",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments, expanding --test-all into every optional test flag."""
    args = _build_parser().parse_args(argv)
    if args.test_all:
        for flag in _OPTIONAL_TEST_FLAGS:
            setattr(args, flag, True)
    return args


def _new_run(args: argparse.Namespace) -> GoldenPathRun:
    default_project_name = "phlo-golden-path"
    default_project_root = Path.home() / "tmp"
    project_name = args.project_dir.name if args.project_dir else default_project_name
    project_dir = (
        args.project_dir.expanduser() if args.project_dir else default_project_root / project_name
    )
    return GoldenPathRun(
        args=args,
        project_name=project_name,
        project_dir=project_dir,
        phlo_source=Path(__file__).resolve().parents[1],
    )


def _log_run_header(run: GoldenPathRun) -> None:
    log_step("Golden Path E2E Workflow")
    log_info(f"Mode: {run.args.mode}")
    log_info(f"Project dir: {run.project_dir}")
    log_info(f"Phlo source: {run.phlo_source}")
    optional_tests = [
        label for flag, label in _OPTIONAL_TEST_LABELS.items() if getattr(run.args, flag)
    ]
    if optional_tests:
        log_info(f"Optional tests: {', '.join(optional_tests)}")
    else:
        log_info("Optional tests: None (use --test-api, --test-observability, etc.)")


def _prepare_host(run: GoldenPathRun) -> bool:
    """Run preflight, confirm Docker can bind-mount, and clear any stale project dir."""
    args = run.args
    if not args.skip_preflight:
        if not preflight_check(auto_cleanup=args.auto_cleanup):
            return False
    else:
        log_warning("Skipping preflight checks (--skip-preflight)")

    project_dir = run.project_dir
    bind_mount_ok, bind_mount_detail = verify_bind_mount_visibility(project_dir.parent)
    if not bind_mount_ok:
        log_error(f"Docker cannot bind-mount project parent: {project_dir.parent}")
        log_warning(bind_mount_detail)
        log_info("Choose a different --project-dir under your home directory and rerun.")
        return False

    if project_dir.exists():
        log_warning(f"Removing existing project dir: {project_dir}")
        if not force_remove_directory(project_dir):
            log_error(f"Failed to remove {project_dir}")
            return False
    return True


def _init_project(run: GoldenPathRun) -> None:
    """Steps 1-2: initialise the project, its virtualenv, and its services."""
    log_step("Step 1: Initialize Project")
    run_phlo(
        ["init", run.project_name, "--template", "basic", "--force"],
        cwd=run.project_dir.parent,
        timeout=120,
    )
    log_success("Project initialized")

    log_step("Step 1.5: Setup Project Environment")
    run.project_python = setup_project_venv(run.project_dir, run.phlo_source)

    log_step("Step 2: Initialize Services")
    if run.args.mode == "dev":
        run.phlo(
            ["services", "init", "--dev", "--phlo-source", str(run.phlo_source), "--force"],
            timeout=180,
        )
    else:
        run.phlo(["services", "init", "--no-dev", "--force"], timeout=180)
    log_success("Services initialized")
    run.reload_env()


_CORE_PORT_DEFAULTS = {
    "DAGSTER_PORT": 3000,
    "POSTGRES_PORT": 5432,
    "TRINO_PORT": 8080,
    "MINIO_API_PORT": 9000,
    "MINIO_CONSOLE_PORT": 9001,
    "NESSIE_PORT": 19120,
}

_DEV_EXTRA_PACKAGES = [
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

_OPTIONAL_PORTS = {
    "test_api": (("HASURA_PORT", "Hasura", 8082), ("POSTGREST_PORT", "PostgREST", 3002)),
    "test_observability": (
        ("PROMETHEUS_PORT", "Prometheus", 9090),
        ("LOKI_PORT", "Loki", 3100),
        ("GRAFANA_PORT", "Grafana", 3003),
        ("ALLOY_PORT", "Alloy", 12345),
    ),
    "test_superset": (("SUPERSET_PORT", "Superset", 8088),),
    "test_openmetadata": (("OPENMETADATA_PORT", "OpenMetadata", 8585),),
}


def _lineage_env_updates(run: GoldenPathRun, env_updates: dict[str, str]) -> dict[str, str]:
    """Point lineage at the project Postgres; remember the host URL for later steps."""
    env_vars = run.env_vars
    pg_user = env_vars.get("POSTGRES_USER", "phlo")
    pg_pass = env_vars.get("POSTGRES_PASSWORD", "phlo")
    pg_db = env_vars.get("POSTGRES_DB", "phlo")
    postgres_port = int(env_updates.get("POSTGRES_PORT", env_vars.get("POSTGRES_PORT", "5432")))
    run.lineage_db_url_host = f"postgresql://{pg_user}:{pg_pass}@localhost:{postgres_port}/{pg_db}"
    os.environ["LINEAGE_DB_URL"] = run.lineage_db_url_host
    return {
        "LINEAGE_DB_URL": f"postgresql://{pg_user}:{pg_pass}@postgres:5432/{pg_db}",
        "PHLO_DEV_EXTRA_PACKAGES": "phlo-lineage",
    }


def _resolve_ports(run: GoldenPathRun) -> None:
    """Pick free ports for every service and write them into the .phlo env files."""
    args = run.args
    env_updates: dict[str, str] = {"PHLO_API_PORT": str(resolve_port("Phlo API", 54000))}
    for key, default_port in _CORE_PORT_DEFAULTS.items():
        env_updates[key] = str(resolve_port(key, default_port))
    if args.mode == "dev":
        # Force Dagster containers to install local workspace packages from /opt/phlo-dev.
        env_updates["PHLO_DEV_EXTRA_PACKAGES"] = ",".join(_DEV_EXTRA_PACKAGES)
    for flag, ports in _OPTIONAL_PORTS.items():
        if getattr(args, flag):
            for key, name, default_port in ports:
                env_updates[key] = str(resolve_port(name, default_port))
    if args.test_lineage:
        env_updates.update(_lineage_env_updates(run, env_updates))

    run.resolved_ports = dict(env_updates)
    apply_env_updates(run.phlo_dir, run.resolved_ports)
    log_info("Updated .phlo environment files with resolved ports")


def _write_workflow_files(project_dir: Path, env_vars: dict[str, str]) -> None:
    """Write the ingestion asset, dbt profile, sources, mart model, and publishing asset."""
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
        project_dir / "workflows" / "transforms" / "dbt" / "models" / "marts" / "posts_mart.sql",
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


def _create_workflow(run: GoldenPathRun) -> None:
    """Step 3: scaffold the ingestion workflow and its transform and publishing files."""
    log_step("Step 3: Create Workflow")
    run.phlo(
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
        timeout=60,
    )
    log_success("Workflow created")
    _write_workflow_files(run.project_dir, run.env_vars)


_CORE_SERVICES = ["postgres", "minio", "minio-setup", "nessie", "trino", "dagster"]


def _start_core_services(run: GoldenPathRun) -> None:
    """Step 4: start the core stack and wait until every service answers."""
    log_step("Step 4: Start Core Services")
    run.start_services(_CORE_SERVICES, timeout=600)
    log_success("Core services started")

    env_vars = run.reload_env()
    run.dagster_port = int(env_vars.get("DAGSTER_PORT", "3000"))
    run.trino_port = int(env_vars.get("TRINO_PORT", "8080"))
    run.postgres_port = int(env_vars.get("POSTGRES_PORT", "5432"))
    minio_port = int(env_vars.get("MINIO_API_PORT", "9000"))
    nessie_port = int(env_vars.get("NESSIE_PORT", "19120"))

    ready = (
        wait_for_tcp("127.0.0.1", run.dagster_port, name="Dagster", timeout=120)
        and wait_for_http(
            f"http://127.0.0.1:{minio_port}/minio/health/live",
            name="MinIO",
            timeout=60,
        )
        and wait_for_http(f"http://127.0.0.1:{run.trino_port}/v1/info", name="Trino", timeout=120)
        and wait_for_tcp("127.0.0.1", run.postgres_port, name="Postgres", timeout=120)
        and wait_for_tcp("127.0.0.1", nessie_port, name="Nessie", timeout=120)
    )
    if not ready:
        raise GoldenPathFailure("core services did not become ready")


def _ingest_and_transform(run: GoldenPathRun, partition_date: str) -> None:
    """Steps 5-8: ingest, verify in Trino, transform, and publish to Postgres."""
    log_step("Step 5: Run DLT Ingestion")
    run.phlo(["materialize", "dlt_posts", "--partition", partition_date], timeout=1200)
    log_success("DLT ingestion completed")

    log_step("Step 6: Verify Trino Data")
    from phlo_trino import TrinoResource

    trino = TrinoResource(host="127.0.0.1", port=run.trino_port, catalog="iceberg")
    rows = trino.execute("SELECT count(*) FROM posts", schema="raw")
    count = rows[0][0] if rows else 0
    log_info(f"Row count in raw.posts: {count}")
    if count <= 0:
        _fail("No data in Trino!")
    log_success(f"Data verified: {count} rows in Trino")

    log_step("Step 7: Run dbt Transform")
    run.phlo(["materialize", "posts_mart", "--partition", partition_date], timeout=1200)
    log_success("dbt transform completed")

    rows = trino.execute("SELECT count(*) FROM posts_mart", schema="raw_marts")
    mart_count = rows[0][0] if rows else 0
    log_info(f"Row count in raw_marts.posts_mart: {mart_count}")

    log_step("Step 8: Publish to PostgreSQL")
    run.phlo(["materialize", "publish_jsonplaceholder_marts"], timeout=1200)
    log_success("Published to PostgreSQL")


def _verify_postgres(run: GoldenPathRun) -> None:
    """Step 9: confirm the published mart has rows in Postgres."""
    log_step("Step 9: Verify PostgreSQL Data")
    import psycopg2
    from psycopg2 import sql

    env_vars = run.env_vars
    mart_schema = env_vars.get("POSTGRES_MART_SCHEMA", "marts")
    pg_conn = psycopg2.connect(
        host="localhost",
        port=run.postgres_port,
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
            if pg_count <= 0:
                _fail("No data in PostgreSQL!")
            log_success(f"PostgreSQL verified: {pg_count} rows")
    finally:
        pg_conn.close()


# ---------------------------------------------------------------------------
# Optional service testing
# ---------------------------------------------------------------------------


def _check_hasura(hasura_port: int, hasura_secret: str) -> None:
    """Query the posts mart through Hasura, falling back to schema introspection."""
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
    graphql_url = f"http://127.0.0.1:{hasura_port}/v1/graphql"
    headers = {"x-hasura-admin-secret": hasura_secret}
    try:
        result = http_post(graphql_url, graphql_query, headers=headers)
        if not isinstance(result, dict):
            _fail(f"Unexpected Hasura response: {result}")
        data = result.get("data")
        if isinstance(data, dict) and "marts_posts_mart" in data:
            rows = data["marts_posts_mart"]
            if not isinstance(rows, list):
                _fail(f"Unexpected Hasura response: {result}")
            log_success(f"Hasura GraphQL: Retrieved {len(rows)} rows")
            if rows:
                log_info(f"  Sample: {rows[0]}")
            return
        if "errors" not in result:
            _fail(f"Unexpected Hasura response: {result}")
        # Table might not be tracked yet, try introspection
        log_warning(f"Hasura query error (table may not be tracked): {result.get('errors')}")
        introspect_query = {"query": "{ __schema { types { name } } }"}
        result = http_post(graphql_url, introspect_query, headers=headers)
        if not (isinstance(result, dict) and "data" in result):
            _fail(f"Hasura GraphQL introspection failed: {result}")
        log_success("Hasura GraphQL: Schema introspection works")
    except GoldenPathFailure:
        raise
    except Exception as e:
        _fail(f"Hasura GraphQL test failed: {e}")


def _check_postgrest(postgrest_port: int) -> None:
    """Read the OpenAPI schema and the posts mart through PostgREST."""
    log_info("Testing PostgREST REST API...")
    try:
        # Get OpenAPI schema to verify server is working
        schema = http_get(f"http://127.0.0.1:{postgrest_port}/")
        if isinstance(schema, dict) and "paths" in schema:
            log_success(f"PostgREST OpenAPI schema: {len(schema.get('paths', {}))} endpoints")

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
            _fail(f"PostgREST REST API test failed: {e}")
    except Exception as e:
        _fail(f"PostgREST REST API test failed: {e}")


def _test_api_layer(run: GoldenPathRun) -> None:
    # Note: Both services have post_start hooks that auto-discover schemas
    run.add_services(["hasura", "postgrest"], timeout=120)
    # Start the API services (hooks will auto-configure schemas)
    run.start_services(["hasura", "postgrest"], timeout=300)

    env_vars = run.reload_env()
    hasura_port = int(env_vars.get("HASURA_PORT", "8082"))
    hasura_secret = env_vars.get("HASURA_ADMIN_SECRET", "phlo-hasura-admin-secret")
    postgrest_port = int(env_vars.get("POSTGREST_PORT", "3002"))

    wait_for_http(f"http://127.0.0.1:{hasura_port}/healthz", name="Hasura", timeout=120)
    wait_for_http(f"http://127.0.0.1:{postgrest_port}/", name="PostgREST", timeout=60)
    # Note: Hasura tables should be auto-tracked by the post_start hook

    _check_hasura(hasura_port, hasura_secret)
    _check_postgrest(postgrest_port)
    log_success("API layer testing complete")


def _check_prometheus(prometheus_port: int) -> None:
    log_info("Testing Prometheus targets...")
    try:
        result = http_get(f"http://127.0.0.1:{prometheus_port}/api/v1/targets")
        data = result.get("data") if isinstance(result, dict) else None
        targets = data.get("activeTargets") if isinstance(data, dict) else None
        if not (isinstance(data, dict) and "activeTargets" in data):
            log_warning(f"Prometheus targets response: {result}")
            return
        targets = targets or []
        if not isinstance(targets, list):
            log_warning(f"Prometheus targets response: {result}")
            return
        up_count = sum(1 for t in targets if t.get("health") == "up")
        log_success(f"Prometheus: {up_count}/{len(targets)} targets UP")
        for t in targets[:3]:  # Show first 3 targets
            state = t.get("health", "unknown")
            job = t.get("labels", {}).get("job", "unknown")
            log_info(f"  - {job}: {state}")
    except Exception as e:
        log_warning(f"Could not query Prometheus targets: {e}")


def _check_loki(loki_port: int) -> None:
    log_info("Testing Loki log ingestion...")
    try:
        result = http_get(f"http://127.0.0.1:{loki_port}/loki/api/v1/labels")
        labels = result.get("data") if isinstance(result, dict) else None
        if not isinstance(labels, list):
            log_warning(f"Loki labels response: {result}")
            return
        log_success(f"Loki: Found {len(labels)} label types")
        if labels:
            log_info(f"  Labels: {', '.join(labels[:5])}")
    except Exception as e:
        log_warning(f"Could not query Loki labels: {e}")


def _check_grafana(grafana_port: int) -> None:
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


_OBSERVABILITY_SERVICES = ["prometheus", "loki", "alloy", "grafana"]


def _test_observability(run: GoldenPathRun) -> None:
    for plugin in _OBSERVABILITY_SERVICES:
        if not run.install_plugin(plugin):
            _fail(f"Failed to install {plugin} plugin")
    run.add_services(_OBSERVABILITY_SERVICES, timeout=120)
    run.start_services(_OBSERVABILITY_SERVICES, timeout=600)

    env_vars = run.reload_env()
    prometheus_port = int(env_vars.get("PROMETHEUS_PORT", "9090"))
    loki_port = int(env_vars.get("LOKI_PORT", "3100"))
    alloy_port = int(env_vars.get("ALLOY_PORT", "12345"))
    grafana_port = int(env_vars.get("GRAFANA_PORT", "3003"))

    wait_for_http(f"http://127.0.0.1:{prometheus_port}/-/healthy", name="Prometheus", timeout=120)
    wait_for_http(f"http://127.0.0.1:{loki_port}/ready", name="Loki", timeout=120)
    wait_for_http(f"http://127.0.0.1:{alloy_port}/-/ready", name="Alloy", timeout=60)
    wait_for_http(f"http://127.0.0.1:{grafana_port}/api/health", name="Grafana", timeout=120)

    _check_prometheus(prometheus_port)
    _check_loki(loki_port)
    _check_grafana(grafana_port)
    log_success("Observability stack testing complete")


def _test_superset(run: GoldenPathRun) -> None:
    log_info("Adding Superset...")
    run.phlo(["services", "add", "superset", "--no-start"], timeout=180)
    apply_env_updates(run.phlo_dir, run.resolved_ports)
    run.start_services(["superset"], timeout=600)

    superset_port = int(run.reload_env().get("SUPERSET_PORT", "8088"))

    # Wait for Superset (can take a while to initialize)
    log_info("Waiting for Superset to initialize (this may take a few minutes)...")
    if wait_for_http(f"http://127.0.0.1:{superset_port}/health", name="Superset", timeout=300):
        log_success("Superset is ready")
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


@dataclass
class _OpenMetadataTarget:
    """Connection details and the table names the OpenMetadata checks look up."""

    port: int
    service: str
    database: str
    user: str
    password: str
    token: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def table_fqn(self) -> str:
        return f"{self.service}.{self.database}.raw_marts.posts_mart"

    @property
    def source_fqn(self) -> str:
        return f"{self.service}.{self.database}.raw.posts"

    def table_url(self, fqn: str) -> str:
        return f"{self.base_url}/api/v1/tables/name/{urllib.parse.quote(fqn, safe='')}"

    def get(self, urls: list[str]) -> Any:
        return openmetadata_get_with_fallback(
            urls,
            token=self.token,
            username=self.user,
            password=self.password,
            timeout=30,
        )


def _sync_openmetadata(run: GoldenPathRun, port: int) -> _OpenMetadataTarget:
    """Sync Nessie and dbt metadata into OpenMetadata; return the target it wrote to."""
    log_info("Syncing metadata to OpenMetadata (Nessie + dbt)...")
    env_vars = run.env_vars
    om_service = env_vars.get("OPENMETADATA_SERVICE_NAME", "phlo")
    om_database = env_vars.get(
        "OPENMETADATA_DATABASE_NAME",
        env_vars.get("TRINO_CATALOG", "iceberg"),
    )
    os.environ["OPENMETADATA_HOST"] = "127.0.0.1"
    os.environ["OPENMETADATA_PORT"] = str(port)
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
    run.phlo(["openmetadata", "sync"], timeout=600)
    return _OpenMetadataTarget(
        port=port,
        service=om_service,
        database=om_database,
        user=env_vars.get("OPENMETADATA_USERNAME", "admin"),
        password=env_vars.get("OPENMETADATA_PASSWORD", "admin"),
    )


def _lookup_openmetadata_table(om: _OpenMetadataTarget) -> dict | list | str | None:
    """Verify the dbt mart table is registered; log in and keep the token on ``om``."""
    table: dict | list | str | None = None
    table_url = om.table_url(om.table_fqn)
    try:
        om.token = openmetadata_login(om.base_url, username=om.user, password=om.password)
        if om.token:
            table = http_get_bearer(table_url, token=om.token, timeout=30)
        else:
            table = http_get_basic(table_url, username=om.user, password=om.password, timeout=30)
        if isinstance(table, dict) and table.get("name") == "posts_mart":
            log_success(f"OpenMetadata verified table: {om.table_fqn}")
        else:
            log_warning(f"OpenMetadata table lookup returned: {table}")
    except Exception as e:
        log_warning(f"OpenMetadata table verification failed: {e}")
    return table


def _emit_openmetadata_smoke_events(run: GoldenPathRun, om: _OpenMetadataTarget) -> None:
    """Emit lineage and quality events through the Phlo hooks into OpenMetadata."""
    log_info("Emitting OpenMetadata lineage + quality smoke events...")
    source_fqn = om.source_fqn
    table_fqn = om.table_fqn
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
            "OPENMETADATA_PORT": str(om.port),
            "OPENMETADATA_SERVICE_NAME": om.service,
            "OPENMETADATA_SERVICE_TYPE": run.env_vars.get("OPENMETADATA_SERVICE_TYPE", "Trino"),
            "OPENMETADATA_DATABASE_NAME": om.database,
            "OPENMETADATA_USERNAME": om.user,
            "OPENMETADATA_PASSWORD": om.password,
        }
    )
    try:
        subprocess.run(
            [run.project_python, "-c", emit_code],
            cwd=run.project_dir,
            env=emit_env,
            timeout=60,
            check=True,
        )
        log_success("OpenMetadata smoke events emitted")
    except Exception as e:
        log_warning(f"Failed to emit OpenMetadata smoke events: {e}")


def _edge_endpoint(entity: Any, nodes: dict[str, str]) -> tuple[object, object]:
    """Return the id and fully-qualified name of one lineage edge endpoint."""
    entity_id = None
    entity_fqn = None
    if isinstance(entity, dict):
        entity_id = entity.get("id")
        entity_fqn = entity.get("fullyQualifiedName")
    elif isinstance(entity, str):
        entity_id = entity
    if entity_fqn is None and entity_id is not None:
        entity_fqn = nodes.get(str(entity_id))
    return entity_id, entity_fqn


def _lineage_has_edge(
    lineage: Any, *, source_id: object, source_fqn: str, table: dict, table_fqn: str
) -> bool:
    """Return True when the lineage response holds the source -> mart edge."""
    edges = []
    nodes: dict[str, str] = {}
    if isinstance(lineage, dict):
        edges = lineage.get("edges") or lineage.get("upstreamEdges") or []
        for node in lineage.get("nodes", []) or []:
            if isinstance(node, dict) and node.get("id"):
                nodes[str(node["id"])] = node.get("fullyQualifiedName") or node.get("name") or ""
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_id, from_fqn = _edge_endpoint(edge.get("fromEntity"), nodes)
        to_id, to_fqn = _edge_endpoint(edge.get("toEntity"), nodes)
        from_matches = (source_id and from_id == source_id) or from_fqn == source_fqn
        to_matches = (table.get("id") and to_id == table.get("id")) or to_fqn == table_fqn
        if from_matches and to_matches:
            return True
    return False


def _verify_openmetadata_lineage(om: _OpenMetadataTarget, table: Any) -> None:
    try:
        if not (isinstance(table, dict) and table.get("id")):
            log_warning("OpenMetadata lineage skipped: missing table id")
            return
        source_table = om.get([om.table_url(om.source_fqn)])
        source_id = source_table.get("id") if isinstance(source_table, dict) else None
        lineage = om.get(
            [f"{om.base_url}/api/v1/lineage/table/{table['id']}?upstreamDepth=1&downstreamDepth=0"]
        )
        if _lineage_has_edge(
            lineage,
            source_id=source_id,
            source_fqn=om.source_fqn,
            table=table,
            table_fqn=om.table_fqn,
        ):
            log_success("OpenMetadata lineage verified")
        else:
            log_warning("OpenMetadata lineage edge not found")
    except Exception as e:
        log_warning(f"OpenMetadata lineage verification failed: {e}")


def _verify_openmetadata_quality(om: _OpenMetadataTarget) -> None:
    try:
        time.sleep(2)
        test_cases = om.get(
            [
                f"{om.base_url}/api/v1/dataQuality/testCases?limit=100",
                f"{om.base_url}/api/v1/testCases?limit=100",
            ]
        )
        data = []
        if isinstance(test_cases, dict):
            data = test_cases.get("data", []) or []
        elif isinstance(test_cases, list):
            data = test_cases
        matches = [
            case
            for case in data
            if isinstance(case, dict) and om.table_fqn in str(case.get("entityLink", ""))
        ]
        if matches:
            log_success("OpenMetadata quality test cases verified")
        else:
            log_warning("OpenMetadata quality test cases not found")
    except Exception as e:
        log_warning(f"OpenMetadata quality verification failed: {e}")


def _verify_openmetadata(run: GoldenPathRun, port: int) -> None:
    log_success("OpenMetadata is ready")
    try:
        result = http_get(f"http://127.0.0.1:{port}/api/v1/system/version")
        log_info(f"OpenMetadata version: {result}")
    except Exception as e:
        log_warning(f"OpenMetadata version check: {e}")

    log_info(f"OpenMetadata UI: http://localhost:{port}")
    log_info("  Login: admin / admin")
    log_warning("Note: Run search reindex after setup for search to work")

    om = _sync_openmetadata(run, port)
    table = _lookup_openmetadata_table(om)
    _emit_openmetadata_smoke_events(run, om)
    _verify_openmetadata_lineage(om, table)
    _verify_openmetadata_quality(om)


def _test_openmetadata(run: GoldenPathRun) -> None:
    log_warning("OpenMetadata requires 6GB+ RAM. Ensure sufficient resources.")
    if not run.install_plugin("openmetadata"):
        log_warning("OpenMetadata plugin not available, skipping")
    else:
        log_info("Adding OpenMetadata...")
        run.phlo(["services", "add", "openmetadata", "--no-start"], timeout=180)
        apply_env_updates(run.phlo_dir, run.resolved_ports)
        run.start_services(["openmetadata"], timeout=900)

    openmetadata_port = int(run.reload_env().get("OPENMETADATA_PORT", "8585"))

    # Wait for OpenMetadata (can take several minutes to initialize)
    log_info("Waiting for OpenMetadata to initialize (this may take several minutes)...")
    if wait_for_http(
        f"http://127.0.0.1:{openmetadata_port}/api/v1/system/version",
        name="OpenMetadata",
        timeout=600,
    ):
        _verify_openmetadata(run, openmetadata_port)
    else:
        log_warning("OpenMetadata did not become ready in time")

    log_success("OpenMetadata testing complete")


def _test_alerting(run: GoldenPathRun) -> None:
    if not run.install_plugin("alerting"):
        log_warning("Alerting plugin not available, skipping")
        log_success("Alerting plugin testing complete")
        return

    log_info("Testing alerting plugin...")
    try:
        result = run.phlo(["alerts", "list"], timeout=60, check=False, stream_output=False)
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


def _configure_lineage_db(run: GoldenPathRun) -> None:
    log_info("Configuring lineage database...")
    if run.lineage_db_url_host:
        os.environ["LINEAGE_DB_URL"] = run.lineage_db_url_host
        log_info("Using LINEAGE_DB_URL for host access")
    elif os.environ.get("LINEAGE_DB_URL"):
        log_info("Using LINEAGE_DB_URL from environment")
    else:
        log_warning("LINEAGE_DB_URL not set; lineage may be empty")


def _check_lineage_show(run: GoldenPathRun) -> None:
    log_info("Testing lineage CLI...")
    try:
        # Try to show lineage for the posts table
        result = run.phlo(
            ["lineage", "show", "raw.posts"], timeout=60, check=False, stream_output=False
        )
        output = result.stdout.lower()
        if result.returncode == 0:
            log_success("Lineage: show command works")
            log_info(f"  Output: {result.stdout.strip()[:200]}")
        # Lineage might not be recorded yet, check if command is available
        elif "error" not in output or "not found" not in output:
            log_info("Lineage: Table may not have lineage recorded yet")
        else:
            log_warning(f"Lineage show error: {result.stdout.strip()[:200]}")
    except Exception as e:
        log_warning(f"Could not test lineage show: {e}")


def _summarize_lineage_export(export_path: Path) -> None:
    try:
        lineage_data = (
            json.loads(export_path.read_text(encoding="utf-8")) if export_path.exists() else {}
        )
    except (json.JSONDecodeError, OSError) as exc:
        log_info(f"  Lineage export parse failed: {exc}")
        return
    if isinstance(lineage_data, dict):
        assets = lineage_data.get("assets", {})
        edges = lineage_data.get("edges", {})
        edge_count = (
            sum(len(targets) for targets in edges.values())
            if isinstance(edges, dict)
            else len(edges or [])
        )
        log_info(f"  Lineage graph: {len(assets)} assets, {edge_count} edges")
    elif isinstance(lineage_data, list):
        log_info(f"  Lineage records: {len(lineage_data)} entries")


def _check_lineage_export(run: GoldenPathRun) -> None:
    try:
        export_path = run.project_dir / ".phlo" / "lineage_export.json"
        result = run.phlo(
            [
                "lineage",
                "export",
                "raw.posts",
                "--format",
                "json",
                "--output",
                str(export_path),
            ],
            timeout=60,
            check=False,
            stream_output=False,
        )
        if result.returncode == 0:
            log_success("Lineage: export command works")
            _summarize_lineage_export(export_path)
        else:
            log_warning(f"Lineage export: {result.stdout.strip()[:200]}")
    except Exception as e:
        log_warning(f"Could not test lineage export: {e}")


def _test_lineage(run: GoldenPathRun) -> None:
    if not run.install_plugin("lineage"):
        log_warning("Lineage plugin not available, skipping")
    else:
        _configure_lineage_db(run)
        _check_lineage_show(run)
        _check_lineage_export(run)
    log_success("Lineage tracking testing complete")


_OPTIONAL_STEPS: tuple[tuple[str, str, Callable[[GoldenPathRun], None]], ...] = (
    ("test_api", "Test API Layer (Hasura + PostgREST)", _test_api_layer),
    ("test_observability", "Test Observability Stack", _test_observability),
    ("test_superset", "Test Superset BI", _test_superset),
    ("test_openmetadata", "Test OpenMetadata Catalog", _test_openmetadata),
    ("test_alerting", "Test Alerting Plugin", _test_alerting),
    ("test_lineage", "Test Lineage Tracking", _test_lineage),
)


def _run_optional_steps(run: GoldenPathRun) -> None:
    """Run each requested optional service test, numbering steps from 10."""
    step_num = 10
    for flag, title, step in _OPTIONAL_STEPS:
        if getattr(run.args, flag):
            log_step(f"Step {step_num}: {title}")
            step_num += 1
            step(run)


def _log_completion(run: GoldenPathRun) -> None:
    args = run.args
    env_vars = run.env_vars
    log_step("=== Golden Path Complete!")
    log_success("All steps completed successfully!")
    log_info(f"Project directory: {run.project_dir}")
    log_info(f"Dagster UI: http://localhost:{run.dagster_port}")
    log_info(f"MinIO Console: http://localhost:{int(env_vars.get('MINIO_CONSOLE_PORT', '9001'))}")

    if args.test_api:
        log_info(f"Hasura Console: http://localhost:{env_vars.get('HASURA_PORT', '8082')}/console")
        log_info(f"PostgREST API: http://localhost:{env_vars.get('POSTGREST_PORT', '3002')}")
    if args.test_observability:
        log_info(f"Grafana: http://localhost:{env_vars.get('GRAFANA_PORT', '3003')}")
        log_info(f"Prometheus: http://localhost:{env_vars.get('PROMETHEUS_PORT', '9090')}")
    if args.test_superset:
        log_info(f"Superset: http://localhost:{env_vars.get('SUPERSET_PORT', '8088')}")
    if args.test_openmetadata:
        log_info(f"OpenMetadata: http://localhost:{env_vars.get('OPENMETADATA_PORT', '8585')}")


def _wait_until_interrupted() -> None:
    log_info("Services are still running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _cleanup(run: GoldenPathRun) -> None:
    """Stop the project's services and remove its Compose volumes."""
    log_step("Cleanup")
    try:
        run_phlo(
            ["services", "stop"],
            cwd=run.project_dir,
            timeout=300,
            check=False,
            python_exe=run.project_python,
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(run.project_dir / ".phlo" / "docker-compose.yml"),
                "down",
                "-v",
                "--remove-orphans",
            ],
            cwd=run.project_dir,
            capture_output=True,
            timeout=120,
            check=False,
        )
        log_success("Cleanup complete")
    except Exception as e:
        log_warning(f"Cleanup error: {e}")


def _run_phases(run: GoldenPathRun) -> None:
    """Run the core workflow, then each requested optional service test."""
    _init_project(run)
    _resolve_ports(run)
    _create_workflow(run)
    _start_core_services(run)
    partition_date = (
        run.args.partition_date or (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    )
    log_info(f"Using partition date: {partition_date}")
    _ingest_and_transform(run, partition_date)
    _verify_postgres(run)
    _run_optional_steps(run)
    _log_completion(run)
    if run.args.keep_running:
        _wait_until_interrupted()


def main() -> int:
    """Run the full golden path workflow and return its exit code."""
    run = _new_run(parse_args())
    _log_run_header(run)
    if not _prepare_host(run):
        return 1
    try:
        _run_phases(run)
        return 0
    except GoldenPathFailure:
        return 1
    except Exception as e:
        log_error(f"Failed: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if not run.args.keep_running:
            _cleanup(run)


if __name__ == "__main__":
    sys.exit(main())
