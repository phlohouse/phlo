"""Unit tests for the bundled-stack contract harness utilities.

Covers the contract enablement flag, port resolution with duplicate
avoidance and generated project identity in env updates, partitioned
materialization, .env.local secret merging, cleanup semantics (kept
stacks skipped unless forced), run-status reads from the Dagster
metadata DB, MinIO credential resolution, optional package installs,
and frontend health checks. All subprocesses and HTTP are faked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from dagster import DagsterRunStatus
from phlo_testing.profile_harness import (
    BUNDLED_STACK_OPTIONAL_PACKAGES,
    BUNDLED_STACK_DEV_PACKAGES,
    BundledStackHarness,
    BundledStackPorts,
    _cleanup_existing_bundled_stack_projects,
    build_bundled_stack_env_updates,
    bundled_stack_contract_enabled,
)


def test_bundled_stack_contract_enabled_reads_truthy_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PHLO_RUN_BUNDLED_STACK_CONTRACT", "true")
    assert bundled_stack_contract_enabled() is True


def test_build_bundled_stack_env_updates_resolves_core_ports(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_resolve_port(service_name: str, default_port: int) -> int:
        calls.append((service_name, default_port))
        return default_port + 10

    monkeypatch.setattr("phlo_testing.profile_harness._port_in_use", lambda port: False)

    updates = build_bundled_stack_env_updates(fake_resolve_port)

    assert updates["DAGSTER_PORT"] == "3010"
    assert updates["POSTGRES_PORT"] == "5442"
    assert updates["PHLO_DEV_EXTRA_PACKAGES"] == ",".join(BUNDLED_STACK_DEV_PACKAGES)
    assert updates["PHLO_WAP_SENSORS_ENABLED"] == "true"
    assert ("Dagster", 3000) in calls
    assert ("Nessie", 19120) in calls
    assert ("Hasura", 8082) in calls
    assert ("OpenMetadata", 8585) in calls


def test_build_bundled_stack_env_updates_avoids_duplicate_ports(monkeypatch) -> None:
    duplicate_ports = {
        "Dagster": 3001,
        "Observatory": 3001,
        "MinIO API": 9002,
        "MinIO Console": 9002,
    }

    def fake_resolve_port(service_name: str, default_port: int) -> int:
        return duplicate_ports.get(service_name, default_port)

    monkeypatch.setattr("phlo_testing.profile_harness._port_in_use", lambda port: False)

    updates = build_bundled_stack_env_updates(fake_resolve_port)

    assert updates["DAGSTER_PORT"] == "3001"
    assert updates["OBSERVATORY_PORT"] == "3002"
    assert updates["MINIO_API_PORT"] == "9002"
    assert updates["MINIO_CONSOLE_PORT"] == "9003"


def test_build_bundled_stack_env_updates_sets_generated_project_identity(monkeypatch) -> None:
    monkeypatch.setattr("phlo_testing.profile_harness._port_in_use", lambda port: False)

    updates = build_bundled_stack_env_updates(
        lambda _service_name, port: port, project_name="proof"
    )

    assert updates["PHLO_PROJECT"] == "proof"


def test_bundled_stack_harness_materialize_adds_partition(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_phlo(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {"run_phlo": staticmethod(fake_run_phlo)},
        )(),
    )

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(
            phlo_api=54000,
            dagster=3000,
            postgres=5432,
            trino=8080,
            minio_api=9000,
            minio_console=9001,
            nessie=19120,
        ),
    )

    result = harness.materialize("dlt_posts", partition_date="2025-01-01", stream_output=False)

    assert result == "ok"
    assert captured["args"] == ["materialize", "dlt_posts", "--partition", "2025-01-01"]
    assert captured["kwargs"] == {
        "cwd": Path("/tmp/project"),
        "timeout": 1200,
        "check": True,
        "stream_output": False,
        "python_exe": Path("/tmp/project/.venv/bin/python"),
    }


def test_bundled_stack_harness_read_env_merges_local_secrets(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text(
        "POSTGRES_USER=phlo\nPOSTGRES_PASSWORD=phlo\nPOSTGRES_DB=phlo\n",
        encoding="utf-8",
    )
    (phlo_dir / ".env.local").write_text(
        "POSTGRES_PASSWORD=secret\nMINIO_ROOT_PASSWORD=minio-secret\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {
                "read_env_file": staticmethod(
                    lambda path: dict(
                        line.split("=", 1)
                        for line in Path(path).read_text(encoding="utf-8").splitlines()
                        if line
                    )
                )
            },
        )(),
    )
    harness = BundledStackHarness(
        project_dir=tmp_path,
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000),
    )

    env_vars = harness.read_env()

    assert env_vars["POSTGRES_USER"] == "phlo"
    assert env_vars["POSTGRES_PASSWORD"] == "secret"
    assert env_vars["MINIO_ROOT_PASSWORD"] == "minio-secret"


def test_bundled_stack_harness_cleanup_skips_kept_stack(monkeypatch) -> None:
    stop_calls: list[bool] = []
    removed_paths: list[Path] = []

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {
                "force_remove_directory": staticmethod(
                    lambda path: removed_paths.append(path) or True
                )
            },
        )(),
    )

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(
            phlo_api=54000,
            dagster=3000,
            postgres=5432,
            trino=8080,
            minio_api=9000,
            minio_console=9001,
            nessie=19120,
        ),
        keep_running=True,
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "stop_services",
        lambda self, *, stream_output=True: stop_calls.append(stream_output),
    )

    harness.cleanup(stream_output=False)

    assert stop_calls == []
    assert removed_paths == []


def test_bundled_stack_harness_cleanup_force_stops_kept_stack(monkeypatch) -> None:
    stop_calls: list[bool] = []
    removed_paths: list[Path] = []

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {
                "force_remove_directory": staticmethod(
                    lambda path: removed_paths.append(path) or True
                )
            },
        )(),
    )

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(
            phlo_api=54000,
            dagster=3000,
            postgres=5432,
            trino=8080,
            minio_api=9000,
            minio_console=9001,
            nessie=19120,
        ),
        keep_running=True,
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "stop_services",
        lambda self, *, stream_output=True: stop_calls.append(stream_output),
    )

    harness.cleanup(stream_output=False, force=True)

    assert stop_calls == [False]
    assert removed_paths == [Path("/tmp/project")]


def test_bundled_stack_harness_get_run_status_reads_metadata_db(monkeypatch) -> None:
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("SUCCESS",)
    connection.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr("phlo_testing.profile_harness.psycopg2.connect", lambda **_: connection)

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(
            phlo_api=54000,
            dagster=3000,
            postgres=5432,
            trino=8080,
            minio_api=9000,
            minio_console=9001,
            nessie=19120,
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "read_env",
        lambda self: {
            "POSTGRES_USER": "phlo",
            "POSTGRES_PASSWORD": "phlo",
            "POSTGRES_DB": "phlo",
        },
    )

    assert harness.get_run_status("run-1") == DagsterRunStatus.SUCCESS
    cursor.execute.assert_called_once_with(
        "SELECT status FROM runs WHERE run_id = %s",
        ("run-1",),
    )


@pytest.mark.parametrize(
    ("override_access_key", "override_secret_key", "expected_access_key", "expected_secret_key"),
    [
        (None, None, "generated-user", "generated-password"),
        ("override-user", "override-password", "override-user", "override-password"),
    ],
)
def test_bundled_stack_harness_snapshot_read_uses_resolved_minio_credentials(
    monkeypatch,
    override_access_key: str | None,
    override_secret_key: str | None,
    expected_access_key: str,
    expected_secret_key: str,
) -> None:
    captured: dict[str, str | None] = {}

    class FakeCatalog:
        def _load_file_io(self, properties: dict[str, str], location: str | None = None) -> object:
            captured.update(properties)
            captured["location"] = location
            return object()

    catalog = FakeCatalog()

    class FakeIcebergResource:
        def __init__(self, *, ref: str) -> None:
            captured["ref"] = ref

        def list_snapshots(self, *, table_name: str, limit: int) -> list[dict[str, Any]]:
            catalog._load_file_io(
                {
                    "s3.endpoint": "http://minio:9000/",
                    "s3.access-key-id": "catalog-user",
                    "s3.secret-access-key": "catalog-password",
                },
                "s3://lake/warehouse/raw/posts/metadata.json",
            )
            captured.update(
                table_name=table_name,
                limit=str(limit),
                aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                iceberg_access_key=os.environ.get("ICEBERG_S3_ACCESS_KEY"),
                iceberg_secret_key=os.environ.get("ICEBERG_S3_SECRET_KEY"),
            )
            return [{"snapshot-id": 1}]

    monkeypatch.setattr("phlo_iceberg.resource.IcebergResource", FakeIcebergResource)
    monkeypatch.setattr("phlo_iceberg.catalog.get_catalog", lambda *, ref: catalog)
    monkeypatch.setattr("phlo_iceberg.catalog.reset_catalog_cache", lambda: None)
    monkeypatch.setattr("phlo_iceberg.settings.get_settings.cache_clear", lambda: None)
    for name, value in {
        "PHLO_TEST_MINIO_ACCESS_KEY": override_access_key,
        "PHLO_TEST_MINIO_SECRET_KEY": override_secret_key,
    }.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(
            phlo_api=54000,
            dagster=3000,
            minio_api=9000,
            nessie=19120,
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "read_env",
        lambda self: {
            "MINIO_ROOT_USER": "generated-user",
            "MINIO_ROOT_PASSWORD": "generated-password",
        },
    )

    assert harness.list_table_snapshots(table_name="raw.posts", ref="main", limit=3) == [
        {"snapshot-id": 1}
    ]
    assert captured == {
        "ref": "main",
        "table_name": "raw.posts",
        "limit": "3",
        "aws_access_key": expected_access_key,
        "aws_secret_key": expected_secret_key,
        "iceberg_access_key": expected_access_key,
        "iceberg_secret_key": expected_secret_key,
        "s3.endpoint": "http://127.0.0.1:9000",
        "s3.access-key-id": expected_access_key,
        "s3.secret-access-key": expected_secret_key,
        "location": "s3://lake/warehouse/raw/posts/metadata.json",
    }
    assert catalog._load_file_io.__func__ is FakeCatalog._load_file_io


def test_bundled_stack_harness_installs_optional_workspace_packages(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {"run_command": staticmethod(fake_run_command)},
        )(),
    )

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path(__file__).resolve().parents[3],
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000),
    )

    harness.ensure_full_stack_packages()

    assert captured["kwargs"] == {"cwd": Path("/tmp/project"), "timeout": 600}
    args = captured["args"]
    assert isinstance(args, list)
    assert args[:4] == [
        "uv",
        "pip",
        "install",
        "--python",
    ]
    for package_name in BUNDLED_STACK_OPTIONAL_PACKAGES:
        assert str(Path(__file__).resolve().parents[3] / "packages" / package_name) in args


def test_bundled_stack_harness_verify_default_frontends_checks_both_endpoints(monkeypatch) -> None:
    started_services: list[tuple[tuple[str, ...], bool]] = []
    waited_urls: list[tuple[str, str, int]] = []

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000, observatory=3001),
    )

    monkeypatch.setattr(
        BundledStackHarness,
        "start_services",
        lambda self, service_names, *, timeout=600, native=False: started_services.append(
            (tuple(service_names), native)
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "wait_for_http",
        lambda self, url, *, name, timeout=120: waited_urls.append((url, name, timeout)),
    )
    response = MagicMock()
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.raise_for_status.return_value = None
    monkeypatch.setattr(
        "phlo_testing.profile_harness.requests.get", lambda *args, **kwargs: response
    )

    harness.verify_default_frontends()

    assert started_services == [(("phlo-api", "observatory"), True)]
    assert waited_urls == [
        ("http://127.0.0.1:54000/health", "Phlo API", 120),
        ("http://127.0.0.1:3001/", "Observatory", 180),
    ]


def test_bundled_stack_harness_stop_services_builds_expected_cli_args(monkeypatch) -> None:
    run_calls: list[tuple[list[str], int, bool, bool]] = []

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000),
    )

    monkeypatch.setattr(
        BundledStackHarness,
        "run_phlo",
        lambda self, args, *, timeout=300, stream_output=False, check=True: run_calls.append(
            (args, timeout, stream_output, check)
        ),
    )

    harness.stop_services(["phlo-api", "observatory"], native=True)
    harness.stop_services(["superset"], timeout=120)

    assert run_calls == [
        (
            ["services", "stop", "--native", "--service", "phlo-api", "--service", "observatory"],
            300,
            True,
            False,
        ),
        (
            ["services", "stop", "--service", "superset"],
            120,
            True,
            False,
        ),
    ]


def test_bundled_stack_harness_verify_superset_accepts_plaintext_health(monkeypatch) -> None:
    added_services: list[tuple[str, ...]] = []
    started_services: list[tuple[tuple[str, ...], int]] = []
    waited_urls: list[tuple[str, str, int]] = []

    harness = BundledStackHarness(
        project_dir=Path("/tmp/project"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000, superset=8088),
    )

    monkeypatch.setattr(
        BundledStackHarness,
        "add_services",
        lambda self, service_names: added_services.append(tuple(service_names)),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "start_services",
        lambda self, service_names, *, timeout=600, native=False: started_services.append(
            (tuple(service_names), timeout)
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "wait_for_http",
        lambda self, url, *, name, timeout=120: waited_urls.append((url, name, timeout)),
    )
    monkeypatch.setattr(BundledStackHarness, "http_get", lambda self, url: "OK")

    harness.verify_superset()

    assert added_services == [("superset",)]
    assert started_services == [(("superset",), 900)]
    assert waited_urls == [("http://127.0.0.1:8088/health", "Superset", 300)]


def test_bundled_stack_harness_verify_openmetadata_falls_back_to_manual_start(monkeypatch) -> None:
    added_services: list[tuple[str, ...]] = []
    waited_urls: list[tuple[str, str, int]] = []
    phlo_calls: list[tuple[str, ...]] = []
    docker_calls: list[tuple[str, ...]] = []

    harness = BundledStackHarness(
        project_dir=Path("/tmp/phlo-bundled-stack-abc12345"),
        phlo_source=Path("/tmp/source"),
        python_executable=Path("/tmp/project/.venv/bin/python"),
        ports=BundledStackPorts(phlo_api=54000, dagster=3000, openmetadata=8585),
    )

    monkeypatch.setattr(
        BundledStackHarness,
        "add_services",
        lambda self, service_names: added_services.append(tuple(service_names)),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "wait_for_http",
        lambda self, url, *, name, timeout=120: (
            waited_urls.append((url, name, timeout)) or (_ for _ in ()).throw(RuntimeError("stop"))
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "run_phlo",
        lambda self, args, **kwargs: (
            phlo_calls.append(tuple(args))
            or type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        ),
    )
    monkeypatch.setattr(
        BundledStackHarness,
        "run_command",
        lambda self, args, **kwargs: (
            docker_calls.append(tuple(args))
            or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        ),
    )
    with pytest.raises(RuntimeError, match="stop"):
        harness.verify_openmetadata()

    assert added_services == [("openmetadata",)]
    assert phlo_calls == [("services", "start", "--service", "openmetadata")]
    assert docker_calls == [
        ("docker", "start", "-a", "phlo-bundled-stack-abc12345-openmetadata-setup-1"),
        ("docker", "start", "phlo-bundled-stack-abc12345-openmetadata-1"),
    ]
    assert waited_urls == [("http://127.0.0.1:8585/api/v1/system/version", "OpenMetadata", 900)]


def test_cleanup_existing_bundled_stack_projects_stops_native_and_docker(
    monkeypatch, tmp_path
) -> None:
    project_dir = tmp_path / "phlo-bundled-stack-stale"
    (project_dir / ".phlo").mkdir(parents=True)
    python_executable = project_dir / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    run_calls: list[tuple[tuple[str, ...], Path]] = []
    removed_paths: list[Path] = []
    docker_calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        "phlo_testing.profile_harness._load_golden_path_module",
        lambda: type(
            "StubGoldenPathModule",
            (),
            {
                "run_phlo": staticmethod(
                    lambda args, **kwargs: run_calls.append((tuple(args), kwargs["cwd"]))
                ),
                "force_remove_directory": staticmethod(
                    lambda path: removed_paths.append(path) or True
                ),
            },
        )(),
    )
    monkeypatch.setattr(
        "phlo_testing.profile_harness.subprocess.run",
        lambda args, **kwargs: (
            docker_calls.append(tuple(args))
            or type(
                "Result",
                (),
                {
                    "stdout": "abc123\nphlo-bundled-stack-test\n"
                    if args[:4] == ["docker", "network", "ls", "--format"]
                    else "abc123\n",
                },
            )()
        ),
    )

    _cleanup_existing_bundled_stack_projects(tmp_path, stream_output=False)

    assert run_calls == [
        (("services", "stop", "--native"), project_dir),
        (("services", "stop"), project_dir),
    ]
    assert removed_paths == [project_dir]
    assert docker_calls == [
        ("docker", "ps", "-aq", "--filter", "name=phlo-bundled-stack-"),
        ("docker", "rm", "-f", "abc123"),
        ("docker", "network", "ls", "--format", "{{.Name}}"),
        ("docker", "network", "rm", "phlo-bundled-stack-test"),
    ]
