"""Run selected required behavioral contracts and retain each suite's evidence.

Only the Iceberg suite needs the shared disposable MinIO and Nessie services;
API journeys own their service fixtures and can run in independent CI jobs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

import httpx
from testcontainers.core.container import DockerContainer

GROUPS = (
    "all",
    "storage",
    "api-contracts",
    "api-preview",
    "api-query-journey",
    "api-branch-journey",
)


def ready(url: str) -> None:
    """Require a service to become healthy before testing it."""
    for _ in range(90):
        try:
            if httpx.get(url, timeout=2).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Required service did not start: {url}")


def select_suites(group: str) -> dict[str, list[str]]:
    """Select complete modules, retaining discovery for future API contracts."""
    suites = {
        "iceberg": ["packages/phlo-iceberg/tests/test_integration_iceberg.py"],
        "dagster": ["packages/phlo-dagster/tests/test_integration_dagster.py"],
        "api": [
            "packages/phlo-api/tests",
            "tests/integration/test_issue1035_dataset_journey.py",
            "tests/integration/test_issue1035_signed_branch_journey.py",
            "-m",
            "integration",
        ],
        "minio-migration": [
            "packages/phlo-minio/tests/test_minio_migration.py",
            "-m",
            "integration",
        ],
        # These are behavioral capability contracts, not integration-marked tests.
        "dbt-quality": ["packages/phlo-dbt/tests/test_quality_asset_checks.py"],
        "schema-generation": ["packages/phlo-pandera/tests/test_cli_004_schema_generate.py"],
        "schema-conversion": ["packages/phlo-iceberg/tests/test_schema_converter.py"],
        "ingestion": ["packages/phlo-dlt/tests/test_ingestion_decorator.py"],
    }
    if group == "all":
        return suites
    if group == "storage":
        return {name: args for name, args in suites.items() if name != "api"}
    api_groups = {
        "api-contracts": [
            "packages/phlo-api/tests",
            "--ignore=packages/phlo-api/tests/test_usage_trino_integration.py",
            "--ignore=packages/phlo-api/tests/test_preview_provision_integration.py",
            "-m",
            "integration",
        ],
        "api-preview": [
            "packages/phlo-api/tests/test_preview_provision_integration.py",
            "-m",
            "integration",
        ],
        "api-query-journey": [
            "packages/phlo-api/tests/test_usage_trino_integration.py",
            "tests/integration/test_issue1035_dataset_journey.py",
            "-m",
            "integration",
        ],
        "api-branch-journey": [
            "tests/integration/test_issue1035_signed_branch_journey.py",
            "-m",
            "integration",
        ],
    }
    if group not in api_groups:
        raise ValueError(f"Unknown integration group: {group!r}")
    return {group: api_groups[group]}


def run_suites(env: dict[str, str], suites: dict[str, list[str]]) -> int:
    """Attempt every selected contract and fail if any required suite fails."""
    if not suites:
        raise ValueError("At least one required suite must be selected")
    results = Path("test-results/integration")
    results.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, args in suites.items():
        print(f"::group::required contract / {name}", flush=True)
        result = subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                "pytest",
                "-p",
                "scripts.ci_required",
                "-o",
                "addopts=",
                "--import-mode=importlib",
                "--tb=short",
                "--durations=20",
                "--durations-min=1",
                f"--junitxml={results / name}.xml",
                *args,
            ],
            env=env,
            check=False,
        )
        failed |= result.returncode != 0
        print("::endgroup::", flush=True)
    return int(failed)


def run_with_services(env: dict[str, str], suites: dict[str, list[str]]) -> int:
    """Provision host-accessible storage and catalog with ephemeral credentials."""
    with (
        DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.2")
        .with_env("MINIO_ROOT_USER", "localtest")
        .with_env("MINIO_ROOT_PASSWORD", "localpass123")
        .with_command("server /bitnami/minio/data")
        .with_exposed_ports(9000)
    ) as minio:
        endpoint = f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
        ready(endpoint + "/minio/health/ready")
        bucket = minio.exec(
            [
                "sh",
                "-c",
                "mc alias set test http://127.0.0.1:9000 localtest localpass123 >/dev/null "
                "&& mc mb test/lake",
            ]
        )
        if bucket.exit_code != 0:
            raise RuntimeError("Required lake bucket could not be created")
        with (
            DockerContainer("ghcr.io/projectnessie/nessie:0.108.3")
            .with_kwargs(network_mode="host")
            .with_env("NESSIE_VERSION_STORE_TYPE", "IN_MEMORY")
            .with_env("nessie.catalog.default-warehouse", "warehouse")
            .with_env("nessie.catalog.warehouses.warehouse.location", "s3://lake/warehouse")
            .with_env("nessie.catalog.service.s3.default-options.endpoint", endpoint)
            .with_env("nessie.catalog.service.s3.default-options.path-style-access", "true")
            .with_env("nessie.catalog.service.s3.default-options.region", "us-east-1")
            .with_env(
                "nessie.catalog.service.s3.default-options.access-key",
                "urn:nessie-secret:quarkus:nessie.catalog.secrets.access-key",
            )
            .with_env("nessie.catalog.secrets.access-key.name", "localtest")
            .with_env("nessie.catalog.secrets.access-key.secret", "localpass123")
        ):
            ready("http://localhost:19120/api/v2/config")
            return run_suites(
                dict(
                    env,
                    MINIO_HOST=minio.get_container_host_ip(),
                    MINIO_API_PORT=str(minio.get_exposed_port(9000)),
                    MINIO_ROOT_USER="localtest",
                    MINIO_ROOT_PASSWORD="localpass123",
                    ICEBERG_CATALOG_URI="http://localhost:19120/iceberg",
                    ICEBERG_S3_ENDPOINT=endpoint,
                    ICEBERG_S3_ACCESS_KEY="localtest",
                    ICEBERG_S3_SECRET_KEY="localpass123",
                    ICEBERG_WAREHOUSE_PATH="warehouse",
                ),
                suites,
            )


def main(argv: list[str] | None = None) -> int:
    """Run one required group, provisioning shared services only for Iceberg."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=GROUPS, default="all")
    args = parser.parse_args(argv)
    suites = select_suites(args.group)
    env = dict(os.environ, PHLO_CI_REQUIRED="true")
    if "iceberg" in suites:
        return run_with_services(env, suites)
    return run_suites(env, suites)


if __name__ == "__main__":
    raise SystemExit(main())
