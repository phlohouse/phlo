"""Tests for scripts/recovery_drill.py: pinned stack images, isolated
ports, generated compose output, and drill lifecycle behaviour."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "recovery_drill.py"
SPEC = importlib.util.spec_from_file_location("recovery_drill", SCRIPT_PATH)
assert SPEC and SPEC.loader
recovery_drill = importlib.util.module_from_spec(SPEC)
sys.modules["recovery_drill"] = recovery_drill
SPEC.loader.exec_module(recovery_drill)


def test_compose_uses_isolated_ports_and_supported_stack_images(tmp_path):
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")

    services = yaml.safe_load(recovery_drill.compose_yaml(stack))["services"]

    assert services["postgres"]["image"] == recovery_drill.POSTGRES_IMAGE
    assert services["minio"]["image"] == recovery_drill.MINIO_IMAGE
    assert services["nessie"]["image"] == recovery_drill.NESSIE_IMAGE

    expected_binds = {
        "postgres": ["127.0.0.1::5432"],
        "minio": ["127.0.0.1::9000"],
        "nessie": ["127.0.0.1::19120"],
    }
    for service, ports in expected_binds.items():
        assert services[service]["ports"] == ports

    assert services["postgres"]["volumes"] == ["postgres-data:/var/lib/postgresql"]
    assert services["nessie"]["depends_on"]["postgres"]["condition"] == "service_healthy"

    nessie_env = services["nessie"]["environment"]
    assert nessie_env["QUARKUS_DATASOURCE_JDBC_URL"] == (
        "jdbc:postgresql://postgres:5432/phlo?currentSchema=public"
    )
    assert nessie_env["nessie.catalog.warehouses.warehouse.location"] == "s3://lake/warehouse"
    assert nessie_env["nessie.catalog.service.s3.default-options.endpoint"] == "http://minio:9000/"
    assert nessie_env["nessie.catalog.service.s3.default-options.path-style-access"] == "true"
    assert recovery_drill.MC_IMAGE.startswith("minio/mc@sha256:")
    assert recovery_drill.NESSIE_ADMIN_IMAGE.startswith(
        "ghcr.io/projectnessie/nessie-server-admin@sha256:"
    )
    assert ":latest" not in recovery_drill.MC_IMAGE


def test_helper_is_network_local_and_uses_locked_dependency_export(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recovery_drill, "run", lambda command, **_: calls.append(command))

    recovery_drill.prepare_helper(tmp_path)

    assert recovery_drill.HELPER_IMAGE.startswith("python@sha256:")
    assert "http://nessie:19120/iceberg/main" in recovery_drill.helper_source()
    assert '"s3.endpoint": "http://minio:9000"' in recovery_drill.helper_source()
    assert calls == [
        [
            "uv",
            "export",
            "--locked",
            "--package",
            "phlo-iceberg",
            "--no-emit-workspace",
            "--no-editable",
            "--format",
            "requirements-txt",
            "--output-file",
            str(tmp_path / "requirements.txt"),
        ]
    ]
    assert (tmp_path / "iceberg_helper.py").read_text(
        encoding="utf-8"
    ) == recovery_drill.helper_source()


def test_manifest_round_trip_requires_fixture_and_checksum(tmp_path):
    fixture = {"project_id": "project", "table_name": "recovery.rows", "snapshot_id": "42"}
    (tmp_path / "postgres.sql").write_text("backup", encoding="utf-8")
    (tmp_path / "nessie.zip").write_text("backup", encoding="utf-8")
    (tmp_path / "lake").mkdir()
    (tmp_path / "lake" / "metadata.json").write_text("metadata", encoding="utf-8")

    recovery_drill.write_manifest(tmp_path, fixture, "a" * 64)

    manifest = recovery_drill.read_manifest(tmp_path)
    assert manifest["fixture"] == fixture
    assert manifest["probe_checksum"] == "a" * 64
    assert manifest["artifacts"] == {
        "postgres.sql": {"sha256": recovery_drill.sha256_file(tmp_path / "postgres.sql")},
        "nessie.zip": {"sha256": recovery_drill.sha256_file(tmp_path / "nessie.zip")},
        "lake": {"sha256": recovery_drill.sha256_tree(tmp_path / "lake")},
    }


def test_lake_tree_digest_is_stable_and_path_sensitive(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a").write_bytes(b"alpha")
    (first / "b").write_bytes(b"beta")
    (second / "b").write_bytes(b"beta")
    (second / "a").write_bytes(b"alpha")

    assert recovery_drill.sha256_tree(first) == recovery_drill.sha256_tree(second)

    (second / "a").rename(second / "renamed")
    assert recovery_drill.sha256_tree(first) != recovery_drill.sha256_tree(second)


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "[]",
        json.dumps({"fixture": {}}),
        json.dumps(
            {
                "fixture": {"project_id": "p", "table_name": "t", "snapshot_id": "s"},
                "probe_checksum": "not-a-checksum",
            }
        ),
    ],
)
def test_manifest_rejects_missing_or_corrupt_verification_evidence(tmp_path, contents):
    (tmp_path / "manifest.json").write_text(contents, encoding="utf-8")

    with pytest.raises(recovery_drill.RecoveryDrillError, match="backup manifest"):
        recovery_drill.read_manifest(tmp_path)


def test_manifest_rejects_missing_backup_files(tmp_path):
    (tmp_path / "postgres.sql").write_text("backup", encoding="utf-8")
    (tmp_path / "nessie.zip").write_text("backup", encoding="utf-8")
    (tmp_path / "lake").mkdir()
    recovery_drill.write_manifest(
        tmp_path, {"project_id": "p", "table_name": "t", "snapshot_id": "s"}, "a" * 64
    )
    (tmp_path / "nessie.zip").unlink()

    with pytest.raises(recovery_drill.RecoveryDrillError, match="backup manifest"):
        recovery_drill.read_manifest(tmp_path)


def write_backup_set(path, marker):
    path.mkdir()
    (path / "postgres.sql").write_text(f"postgres-{marker}", encoding="utf-8")
    (path / "nessie.zip").write_text(f"nessie-{marker}", encoding="utf-8")
    (path / "lake").mkdir()
    (path / "lake" / "metadata.json").write_text(f"metadata-{marker}", encoding="utf-8")
    recovery_drill.write_manifest(
        path,
        {"project_id": "p", "table_name": "t", "snapshot_id": marker},
        "a" * 64,
    )


@pytest.mark.parametrize(
    ("artifact", "path"),
    [
        ("postgres.sql", "postgres.sql"),
        ("nessie.zip", "nessie.zip"),
        ("lake", "lake/metadata.json"),
    ],
)
def test_corrupt_recovery_set_stops_before_restore_mutation(tmp_path, monkeypatch, artifact, path):
    backup = tmp_path / "backup"
    write_backup_set(backup, "original")
    (backup / path).write_text("corrupt", encoding="utf-8")
    calls = []
    monkeypatch.setattr(recovery_drill, "restore_bucket", lambda *_: calls.append("bucket"))
    monkeypatch.setattr(
        recovery_drill, "compose", lambda *_args, **_kwargs: calls.append("postgres")
    )
    monkeypatch.setattr(recovery_drill, "import_nessie", lambda *_: calls.append("nessie"))

    with pytest.raises(recovery_drill.RecoveryDrillError, match=f"digest mismatch for {artifact}"):
        recovery_drill.restore_recovery_set(
            recovery_drill.Stack("owned-drill", tmp_path / "target"), backup
        )

    assert calls == []


def test_mixed_recovery_set_stops_before_restore_mutation(tmp_path, monkeypatch):
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    write_backup_set(original, "original")
    write_backup_set(replacement, "replacement")
    shutil.copy2(original / "manifest.json", replacement / "manifest.json")
    calls = []
    monkeypatch.setattr(recovery_drill, "restore_bucket", lambda *_: calls.append("bucket"))
    monkeypatch.setattr(
        recovery_drill, "compose", lambda *_args, **_kwargs: calls.append("postgres")
    )
    monkeypatch.setattr(recovery_drill, "import_nessie", lambda *_: calls.append("nessie"))

    with pytest.raises(recovery_drill.RecoveryDrillError, match="digest mismatch"):
        recovery_drill.restore_recovery_set(
            recovery_drill.Stack("owned-drill", tmp_path / "target"), replacement
        )

    assert calls == []


def test_nessie_export_and_import_use_the_pinned_admin_tool(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recovery_drill, "run", lambda command, **_: calls.append(command))
    monkeypatch.setattr(recovery_drill.os, "name", "posix")
    monkeypatch.setattr(recovery_drill.os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(recovery_drill.os, "getgid", lambda: 118, raising=False)
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")

    recovery_drill.export_nessie(stack, tmp_path)
    recovery_drill.import_nessie(stack, tmp_path)

    common = [
        "docker",
        "run",
        "--rm",
        "--user",
        "1001:118",
        "--network",
        "owned-drill_default",
        "-v",
        f"{tmp_path.resolve()}:/backup",
        "-e",
        "NESSIE_VERSION_STORE_TYPE=JDBC",
        "-e",
        "QUARKUS_DATASOURCE_JDBC_URL=jdbc:postgresql://postgres:5432/phlo?currentSchema=public",
        "-e",
        "QUARKUS_DATASOURCE_USERNAME=phlo",
        "-e",
        "QUARKUS_DATASOURCE_PASSWORD=phlo",
        recovery_drill.NESSIE_ADMIN_IMAGE,
    ]
    assert calls == [
        [*common, "export", "--path", "/backup/nessie.zip"],
        [*common, "import", "--erase-before-import", "--path", "/backup/nessie.zip"],
    ]


def test_minio_client_uses_host_owner_for_backup_mounts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(recovery_drill, "run", lambda command, **_: calls.append(command))
    monkeypatch.setattr(recovery_drill.os, "name", "posix")
    monkeypatch.setattr(recovery_drill.os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(recovery_drill.os, "getgid", lambda: 118, raising=False)
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")

    recovery_drill.mc(stack, "true", mounts=[(tmp_path, "/backup")])

    assert calls == [
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "1001:118",
            "-e",
            "HOME=/tmp",
            "--network",
            "owned-drill_default",
            "-v",
            f"{tmp_path.resolve()}:/backup",
            "--entrypoint",
            "/bin/sh",
            recovery_drill.MC_IMAGE,
            "-c",
            "true",
        ]
    ]


def test_restored_evidence_requires_exact_resource_and_catalog_change():
    fixture = {"project_id": "project", "table_name": "recovery.rows", "snapshot_id": "42"}

    class Store:
        def get_run(self, *_):
            return {"run_id": "fixture", "status": "success", "evidence_completeness": "complete"}

        def list_resources(self, *_):
            return [
                {
                    "resource_id": "rows",
                    "resource_kind": "iceberg_table",
                    "role": "output",
                    "table_name": "recovery.rows",
                    "catalog": "iceberg",
                    "ref_name": "main",
                    "record_count": 3,
                    "snapshot_after": "42",
                }
            ]

        def list_catalog_changes(self, *_):
            return [
                {
                    "catalog_change_id": "main",
                    "content_key": "recovery.rows",
                    "catalog_ref": "main",
                    "operation": "create_or_replace",
                    "snapshot_after": "42",
                }
            ]

    recovery_drill.verify_evidence(Store(), fixture)

    class MissingCatalog(Store):
        def list_catalog_changes(self, *_):
            return []

    with pytest.raises(recovery_drill.RecoveryDrillError, match="RunCatalogChange"):
        recovery_drill.verify_evidence(MissingCatalog(), fixture)


def test_cleanup_uses_only_project_scoped_compose_down(tmp_path, monkeypatch):
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")
    stack.directory.mkdir()
    stack.compose_file.write_text("services: {}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(recovery_drill, "run", lambda command, **_: calls.append(command))

    recovery_drill.cleanup(stack)

    assert calls == [
        [
            "docker",
            "compose",
            "-p",
            "owned-drill",
            "-f",
            str(stack.compose_file),
            "down",
            "--volumes",
            "--remove-orphans",
        ]
    ]


def test_remove_owned_only_removes_matching_drill_directory(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / recovery_drill.OWNER_MARKER).write_text(
        json.dumps({"token": "mine"}), encoding="utf-8"
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / recovery_drill.OWNER_MARKER).write_text(
        json.dumps({"token": "other"}), encoding="utf-8"
    )

    recovery_drill.remove_owned(owned, "mine")
    recovery_drill.remove_owned(foreign, "mine")

    assert not owned.exists()
    assert foreign.exists()


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["docker"], 1), OSError("no docker")])
def test_run_normalizes_process_start_failures(monkeypatch, error):
    def fail(*_, **__):
        raise error

    monkeypatch.setattr(recovery_drill.subprocess, "run", fail)

    with pytest.raises(recovery_drill.RecoveryDrillError):
        recovery_drill.run(["docker"], timeout=1)


def test_published_port_requires_loopback(monkeypatch, tmp_path):
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")
    monkeypatch.setattr(
        recovery_drill,
        "compose",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"127.0.0.1:45678\n"),
    )
    assert recovery_drill.published_port(stack, "postgres", 5432) == 45678
    monkeypatch.setattr(
        recovery_drill,
        "compose",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"0.0.0.0:45678\n"),
    )
    with pytest.raises(recovery_drill.RecoveryDrillError, match="loopback"):
        recovery_drill.published_port(stack, "postgres", 5432)


def test_start_waits_for_postgres_before_other_health_checks(monkeypatch, tmp_path):
    stack = recovery_drill.Stack("owned-drill", tmp_path / "source")
    calls = []
    monkeypatch.setattr(
        recovery_drill,
        "compose",
        lambda _stack, *args, **_: (
            calls.append(args) or subprocess.CompletedProcess([], 0, stdout=b"127.0.0.1:45678\n")
        ),
    )
    monkeypatch.setattr(
        recovery_drill, "wait_for", lambda *args, **_: calls.append(("http", args[0]))
    )

    recovery_drill.start(stack, with_nessie=True)

    assert calls[0] == ("up", "-d", "postgres", "minio", "nessie")
    assert calls[1] == ("exec", "-T", "postgres", "pg_isready", "-U", "phlo")


def test_manifest_requires_regular_postgres_file_and_lake_directory(tmp_path):
    fixture = {"project_id": "p", "table_name": "t", "snapshot_id": "s"}
    (tmp_path / "postgres.sql").mkdir()
    (tmp_path / "nessie.zip").write_text("backup", encoding="utf-8")
    (tmp_path / "lake").write_text("not a directory", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"fixture": fixture, "probe_checksum": "a" * 64}), encoding="utf-8"
    )

    with pytest.raises(recovery_drill.RecoveryDrillError, match="backup manifest"):
        recovery_drill.read_manifest(tmp_path)


def test_cleanup_all_aggregates_both_failures_and_preserves_diagnostics(tmp_path, monkeypatch):
    stacks = tuple(recovery_drill.Stack(name, tmp_path / name) for name in ("source", "target"))
    for stack in stacks:
        stack.directory.mkdir()
        stack.compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        recovery_drill,
        "compose",
        lambda stack, *_args, **_kwargs: (_ for _ in ()).throw(
            recovery_drill.RecoveryDrillError(f"cannot clean {stack.project}")
        ),
    )

    error = recovery_drill.cleanup_all(stacks)

    assert error is not None
    assert "source" in str(error) and "target" in str(error)
    assert stacks[0].directory.exists()


def test_setup_failure_still_enters_owned_cleanup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        recovery_drill,
        "prepare_helper",
        lambda _: (_ for _ in ()).throw(recovery_drill.RecoveryDrillError("export failed")),
    )
    monkeypatch.setattr(recovery_drill, "cleanup_all", lambda stacks: calls.append(stacks) or None)

    with pytest.raises(recovery_drill.RecoveryDrillError, match="export failed"):
        recovery_drill.drill(tmp_path)

    assert len(calls) == 1 and {stack.project.split("-")[-2] for stack in calls[0]} == {
        "source",
        "restore",
    }
    assert not list(tmp_path.glob("recovery-drill-*"))


def test_main_emits_structured_failure_json(monkeypatch, capsys):
    monkeypatch.setattr(
        recovery_drill,
        "drill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            recovery_drill.RecoveryDrillError("failed safely")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["recovery_drill.py"])

    assert recovery_drill.main() == 1
    assert json.loads(capsys.readouterr().err) == {
        "continuity_drill": True,
        "error": "failed safely",
        "outcome": "failed",
    }
