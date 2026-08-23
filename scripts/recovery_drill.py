#!/usr/bin/env python3
"""Run an isolated Phlo continuity recovery drill.

This is a continuity exercise, not an atomic production cutover. It records
observed backup and restore duration but deliberately makes no RTO or RPO claim.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

POSTGRES_IMAGE = "postgres:18-alpine"
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"
NESSIE_IMAGE = "ghcr.io/projectnessie/nessie:0.108.3"
NESSIE_ADMIN_IMAGE = "ghcr.io/projectnessie/nessie-server-admin@sha256:ffccc83adc048ae9c069205b2b7c79c8c72604574558f915b730f2266262c159"
MC_IMAGE = "minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
HELPER_IMAGE = "python@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93"
OWNER_MARKER = ".phlo-recovery-drill-owner.json"


class RecoveryDrillError(RuntimeError):
    """The drill could not demonstrate the requested recovery property."""


@dataclass(frozen=True)
class Stack:
    """An isolated docker compose project and scratch directory for one drill run."""

    project: str
    directory: Path

    @property
    def compose_file(self) -> Path:
        """Return the path of the stack's generated compose.yaml."""
        return self.directory / "compose.yaml"


def run(
    command: list[str], *, input: bytes | None = None, timeout: int = 180
) -> subprocess.CompletedProcess[bytes]:
    """Run a command; timeouts, spawn failures, and nonzero exits raise RecoveryDrillError."""
    try:
        completed = subprocess.run(command, input=input, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RecoveryDrillError(
            f"command timed out after {timeout}s: {' '.join(command)}"
        ) from exc
    except OSError as exc:
        raise RecoveryDrillError(f"command could not start: {' '.join(command)}: {exc}") from exc
    if completed.returncode:
        detail = (
            completed.stderr.decode(errors="replace").strip()
            or completed.stdout.decode(errors="replace").strip()
        )
        raise RecoveryDrillError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}"
        )
    return completed


def compose_yaml(stack: Stack) -> str:
    """Render the compose file defining Postgres, MinIO, and Nessie on a shared network."""
    return f"""services:
  postgres:
    image: {POSTGRES_IMAGE}
    environment:
      POSTGRES_USER: phlo
      POSTGRES_PASSWORD: phlo
      POSTGRES_DB: phlo
    ports: [\"127.0.0.1::5432\"]
    volumes: [postgres-data:/var/lib/postgresql]
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U phlo\"]
      interval: 2s
      timeout: 2s
      retries: 30
  minio:
    image: {MINIO_IMAGE}
    command: [\"server\", \"/data\"]
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: minio123
    ports: [\"127.0.0.1::9000\"]
    volumes: [minio-data:/data]
  nessie:
    image: {NESSIE_IMAGE}
    environment:
      NESSIE_VERSION_STORE_TYPE: JDBC
      QUARKUS_DATASOURCE_JDBC_URL: jdbc:postgresql://postgres:5432/phlo?currentSchema=public
      QUARKUS_DATASOURCE_USERNAME: phlo
      QUARKUS_DATASOURCE_PASSWORD: phlo
      nessie.catalog.default-warehouse: warehouse
      nessie.catalog.warehouses.warehouse.location: s3://lake/warehouse
      nessie.catalog.service.s3.default-options.endpoint: http://minio:9000/
      nessie.catalog.service.s3.default-options.path-style-access: \"true\"
      nessie.catalog.service.s3.default-options.region: us-east-1
      nessie.catalog.service.s3.default-options.access-key: urn:nessie-secret:quarkus:nessie.catalog.secrets.access-key
      nessie.catalog.secrets.access-key.name: minio
      nessie.catalog.secrets.access-key.secret: minio123
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_started
    ports: [\"127.0.0.1::19120\"]
volumes:
  postgres-data: {{}}
  minio-data: {{}}
"""


def compose(
    stack: Stack, *args: str, input: bytes | None = None, timeout: int = 180
) -> subprocess.CompletedProcess[bytes]:
    """Run a docker compose subcommand against this stack's project and compose file."""
    return run(
        ["docker", "compose", "-p", stack.project, "-f", str(stack.compose_file), *args],
        input=input,
        timeout=timeout,
    )


def published_port(stack: Stack, service: str, container_port: int) -> int:
    """Resolve the loopback host port Docker published for a service's container port."""
    output = (
        compose(stack, "port", service, str(container_port), timeout=30).stdout.decode().strip()
    )
    try:
        host, port = output.rsplit(":", 1)
        if host not in {"127.0.0.1", "[::1]"}:
            raise ValueError("published address is not loopback")
        return int(port)
    except ValueError as exc:
        raise RecoveryDrillError(
            f"could not discover loopback port for {stack.project}/{service}"
        ) from exc


def wait_for(url: str, *, name: str, timeout: int = 120) -> None:
    """Poll a URL until it responds with a status below 400 or raise RecoveryDrillError."""
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 400:
                    return
        except Exception as exc:  # the endpoint is expected to be unavailable while starting
            last_error = str(exc)
        time.sleep(2)
    raise RecoveryDrillError(f"{name} did not become healthy within {timeout}s: {last_error}")


def wait_for_postgres(stack: Stack, *, timeout: int = 120) -> None:
    """Poll pg_isready until Postgres accepts connections or the timeout expires."""
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            compose(stack, "exec", "-T", "postgres", "pg_isready", "-U", "phlo", timeout=10)
            return
        except RecoveryDrillError as exc:
            last_error = str(exc)
            time.sleep(2)
    raise RecoveryDrillError(
        f"{stack.project} Postgres did not become healthy within {timeout}s: {last_error}"
    )


def start(stack: Stack, *, with_nessie: bool) -> None:
    """Start the requested services and block until each is healthy."""
    services = ["postgres", "minio"] + (["nessie"] if with_nessie else [])
    compose(stack, "up", "-d", *services, timeout=300)
    wait_for_postgres(stack)
    wait_for(
        f"http://127.0.0.1:{published_port(stack, 'minio', 9000)}/minio/health/ready",
        name=f"{stack.project} MinIO",
    )
    if with_nessie:
        wait_for(
            f"http://127.0.0.1:{published_port(stack, 'nessie', 19120)}/api/v1/config",
            name=f"{stack.project} Nessie",
            timeout=180,
        )


def mc(
    stack: Stack, command: str, *, mounts: list[tuple[Path, str]] = (), timeout: int = 180
) -> subprocess.CompletedProcess[bytes]:
    """Run an mc shell command in a container joined to the stack's compose network."""
    host_user = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []
    args = [
        "docker",
        "run",
        "--rm",
        *host_user,
        "-e",
        "HOME=/tmp",
        "--network",
        f"{stack.project}_default",
    ]
    for source, target in mounts:
        args.extend(["-v", f"{source.resolve()}:{target}"])
    args.extend(["--entrypoint", "/bin/sh", MC_IMAGE, "-c", command])
    return run(args, timeout=timeout)


def prepare_bucket(stack: Stack, probe: Path, key: str) -> None:
    """Create the lake bucket and copy the probe file into it under key."""
    mc(
        stack,
        f"mc alias set source http://minio:9000 minio minio123 >/dev/null && mc mb --ignore-existing source/lake >/dev/null && mc cp /backup/{probe.name} source/lake/{key} >/dev/null",
        mounts=[(probe.parent, "/backup")],
    )


def mirror_bucket(source: Stack, backup_dir: Path) -> None:
    """Mirror the source stack's lake objects into backup_dir/lake."""
    (backup_dir / "lake").mkdir(parents=True, exist_ok=True)
    mc(
        source,
        "mc alias set source http://minio:9000 minio minio123 >/dev/null && mc mirror --overwrite source/lake /backup/lake >/dev/null",
        mounts=[(backup_dir, "/backup")],
        timeout=300,
    )


def restore_bucket(target: Stack, backup_dir: Path) -> None:
    """Recreate the target lake bucket and overwrite its contents from backup_dir/lake."""
    mc(
        target,
        "mc alias set target http://minio:9000 minio minio123 >/dev/null && mc mb --ignore-existing target/lake >/dev/null && mc mirror --overwrite /backup/lake target/lake >/dev/null",
        mounts=[(backup_dir, "/backup")],
        timeout=300,
    )


def object_checksum(stack: Stack, key: str) -> str:
    """Return the sha256 hex digest of the lake object stored under key."""
    command = f"mc alias set target http://minio:9000 minio minio123 >/dev/null && mc cat target/lake/{key} | sha256sum"
    return mc(stack, command).stdout.decode().split()[0]


def nessie_admin(stack: Stack, backup_dir: Path, *args: str) -> None:
    """Export or import Nessie's repository without running a target server."""
    host_user = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name == "posix" else []
    run(
        [
            "docker",
            "run",
            "--rm",
            *host_user,
            "--network",
            f"{stack.project}_default",
            "-v",
            f"{backup_dir.resolve()}:/backup",
            "-e",
            "NESSIE_VERSION_STORE_TYPE=JDBC",
            "-e",
            "QUARKUS_DATASOURCE_JDBC_URL=jdbc:postgresql://postgres:5432/phlo?currentSchema=public",
            "-e",
            "QUARKUS_DATASOURCE_USERNAME=phlo",
            "-e",
            "QUARKUS_DATASOURCE_PASSWORD=phlo",
            NESSIE_ADMIN_IMAGE,
            *args,
        ],
        timeout=300,
    )


def export_nessie(stack: Stack, backup_dir: Path) -> None:
    """Export the Nessie repository to nessie.zip inside backup_dir."""
    nessie_admin(stack, backup_dir, "export", "--path", "/backup/nessie.zip")


def import_nessie(stack: Stack, backup_dir: Path) -> None:
    """Import nessie.zip into the target, erasing its current Nessie state first."""
    nessie_admin(
        stack,
        backup_dir,
        "import",
        "--erase-before-import",
        "--path",
        "/backup/nessie.zip",
    )


def helper_source() -> str:
    """Return the in-network helper script that creates or verifies an Iceberg fixture table."""
    return """import json, sys
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

request = json.load(open(sys.argv[1], encoding="utf-8"))
catalog = load_catalog("recovery_drill", type="rest", uri="http://nessie:19120/iceberg/main", warehouse="s3://lake/warehouse", **{"s3.endpoint": "http://minio:9000", "s3.access-key-id": "minio", "s3.secret-access-key": "minio123", "s3.path-style-access": "true", "s3.region": "us-east-1"})
if request["action"] == "create":
    catalog.create_namespace(request["namespace"])
    table = catalog.create_table(request["table_name"], Schema(NestedField(1, "id", LongType(), required=True), NestedField(2, "value", StringType(), required=False)))
    table.append(pa.Table.from_arrays([pa.array([1, 2, 3], type=pa.int64()), pa.array(["alpha", "beta", "gamma"])], schema=pa.schema([pa.field("id", pa.int64(), nullable=False), pa.field("value", pa.string())])))
    snapshot = table.current_snapshot()
    if snapshot is None: raise RuntimeError("Iceberg fixture did not create a snapshot")
    print(json.dumps({"table_name": request["table_name"], "snapshot_id": str(snapshot.snapshot_id)}))
elif request["action"] == "verify":
    table = catalog.load_table(request["table_name"])
    snapshot = table.current_snapshot()
    print(json.dumps({"snapshot_id": None if snapshot is None else str(snapshot.snapshot_id), "row_count": table.scan().to_arrow().num_rows}))
else: raise RuntimeError("unsupported recovery helper action")
"""


def prepare_helper(directory: Path) -> None:
    """Write the locked requirements export and helper script into directory."""
    run(
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
            str(directory / "requirements.txt"),
        ]
    )
    (directory / "iceberg_helper.py").write_text(helper_source(), encoding="utf-8")


def helper(stack: Stack, directory: Path, request: dict[str, str]) -> dict[str, Any]:
    """Run the Iceberg helper container and return its final JSON output as a dict."""
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            f"{stack.project}_default",
            "-v",
            f"{directory.resolve()}:/work:ro",
            HELPER_IMAGE,
            "sh",
            "-ec",
            "pip install --disable-pip-version-check --no-cache-dir --require-hashes -q -r /work/requirements.txt && python /work/iceberg_helper.py /work/request.json",
        ],
        timeout=600,
    )
    try:
        return json.loads(result.stdout.decode().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RecoveryDrillError("in-network Iceberg helper did not return JSON evidence") from exc


def create_fixture(stack: Stack, token: str, helper_dir: Path) -> dict[str, Any]:
    """Create an Iceberg table and matching run evidence, returning fixture identifiers."""
    table_name = f"recovery_{token}.rows"
    result = helper(
        stack,
        helper_dir,
        {"action": "create", "namespace": f"recovery_{token}", "table_name": table_name},
    )
    snapshot_id = result.get("snapshot_id")
    if not isinstance(snapshot_id, str):
        raise RecoveryDrillError("in-network Iceberg helper returned no snapshot")
    from phlo.run_evidence import (
        EvidenceCompleteness,
        PipelineRun,
        PostgresRunEvidenceStore,
        RunCatalogChange,
        RunResource,
    )

    project_id = f"recovery-drill-{token}"
    store = PostgresRunEvidenceStore(
        f"postgresql://phlo:phlo@127.0.0.1:{published_port(stack, 'postgres', 5432)}/phlo"
    )
    store.append_pipeline_run(
        PipelineRun(
            project_id=project_id,
            run_id="fixture",
            pipeline_name="recovery-drill",
            status="success",
            evidence_completeness=EvidenceCompleteness.COMPLETE,
        )
    )
    store.append_resource(
        RunResource(
            project_id=project_id,
            run_id="fixture",
            resource_id="rows",
            resource_kind="iceberg_table",
            role="output",
            table_name=table_name,
            catalog="iceberg",
            ref_name="main",
            record_count=3,
            snapshot_after=snapshot_id,
        )
    )
    store.append_catalog_change(
        RunCatalogChange(
            project_id=project_id,
            run_id="fixture",
            catalog_change_id="main",
            catalog_ref="main",
            content_key=table_name,
            operation="create_or_replace",
            snapshot_after=snapshot_id,
        )
    )
    return {
        "project_id": project_id,
        "table_name": table_name,
        "snapshot_id": snapshot_id,
    }


def verify_fixture(
    stack: Stack, fixture: dict[str, Any], expected_checksum: str, key: str, helper_dir: Path
) -> None:
    """Compare restored snapshot, rows, object checksum, and evidence against the fixture."""
    from phlo.run_evidence import PostgresRunEvidenceStore

    result = helper(
        stack, helper_dir, {"action": "verify", "table_name": str(fixture["table_name"])}
    )
    if result.get("snapshot_id") != fixture["snapshot_id"]:
        raise RecoveryDrillError("restored Iceberg snapshot does not match the backup fixture")
    if result.get("row_count") != 3:
        raise RecoveryDrillError("restored Iceberg table does not contain the expected three rows")
    if object_checksum(stack, key) != expected_checksum:
        raise RecoveryDrillError("restored object checksum does not match the backup fixture")
    store = PostgresRunEvidenceStore(
        f"postgresql://phlo:phlo@127.0.0.1:{published_port(stack, 'postgres', 5432)}/phlo"
    )
    verify_evidence(store, fixture)


def verify_evidence(store: Any, fixture: dict[str, Any]) -> None:
    """Assert the stored pipeline run, resources, and catalog changes match the fixture."""
    project_id, run_id, snapshot_id = fixture["project_id"], "fixture", fixture["snapshot_id"]
    run_evidence = store.get_run(project_id, run_id)
    if (
        run_evidence is None
        or run_evidence.get("status") != "success"
        or run_evidence.get("evidence_completeness") != "complete"
    ):
        raise RecoveryDrillError("restored PipelineRun evidence does not match the fixture")
    resources = store.list_resources(project_id, run_id)
    changes = store.list_catalog_changes(project_id, run_id)
    expected_resource = {
        "resource_id": "rows",
        "resource_kind": "iceberg_table",
        "role": "output",
        "table_name": fixture["table_name"],
        "catalog": "iceberg",
        "ref_name": "main",
        "record_count": 3,
        "snapshot_after": snapshot_id,
    }
    expected_change = {
        "catalog_change_id": "main",
        "content_key": fixture["table_name"],
        "catalog_ref": "main",
        "operation": "create_or_replace",
        "snapshot_after": snapshot_id,
    }
    if not any(
        all(item.get(key) == value for key, value in expected_resource.items())
        for item in resources
    ):
        raise RecoveryDrillError("restored RunResource evidence does not match the Iceberg fixture")
    if not any(
        all(item.get(key) == value for key, value in expected_change.items()) for item in changes
    ):
        raise RecoveryDrillError(
            "restored RunCatalogChange evidence does not match the Iceberg fixture"
        )


def sha256_file(path: Path) -> str:
    """Hash a file in 1 MiB blocks and return the hex digest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    """Digest every file under root in sorted path order, rejecting symlinks and specials."""
    digest = hashlib.sha256(b"phlo-recovery-tree-v1\0")
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise RecoveryDrillError(f"backup lake contains unsupported entry: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RecoveryDrillError(f"backup lake contains unsupported entry: {path}")
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def write_manifest(backup_dir: Path, fixture: dict[str, Any], checksum: str) -> None:
    """Record artifact digests, the fixture, and probe checksum in manifest.json."""
    artifacts = {
        "postgres.sql": {"sha256": sha256_file(backup_dir / "postgres.sql")},
        "nessie.zip": {"sha256": sha256_file(backup_dir / "nessie.zip")},
        "lake": {"sha256": sha256_tree(backup_dir / "lake")},
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {"artifacts": artifacts, "fixture": fixture, "probe_checksum": checksum},
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def read_manifest(backup_dir: Path) -> dict[str, Any]:
    """Load the backup manifest, validate its shape, and verify artifact digests."""
    try:
        payload = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryDrillError("backup manifest is missing or corrupt") from exc
    fixture = payload.get("fixture") if isinstance(payload, dict) else None
    checksum = payload.get("probe_checksum") if isinstance(payload, dict) else None
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if (
        not (backup_dir / "postgres.sql").is_file()
        or not (backup_dir / "nessie.zip").is_file()
        or not (backup_dir / "lake").is_dir()
        or not isinstance(artifacts, dict)
        or not isinstance(fixture, dict)
        or not all(
            isinstance(fixture.get(key), str) and fixture[key]
            for key in ("project_id", "table_name", "snapshot_id")
        )
        or not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum.lower())
    ):
        raise RecoveryDrillError("backup manifest is missing required verification evidence")
    actual_digests = {
        "postgres.sql": sha256_file(backup_dir / "postgres.sql"),
        "nessie.zip": sha256_file(backup_dir / "nessie.zip"),
        "lake": sha256_tree(backup_dir / "lake"),
    }
    for name, actual in actual_digests.items():
        artifact = artifacts.get(name)
        expected = artifact.get("sha256") if isinstance(artifact, dict) else None
        if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
            raise RecoveryDrillError(f"backup manifest digest mismatch for {name}")
    return payload


def restore_recovery_set(target: Stack, backup_dir: Path) -> dict[str, Any]:
    """Restore lake, Postgres, and Nessie from a verified backup and return the manifest."""
    manifest = read_manifest(backup_dir)
    restore_bucket(target, backup_dir)
    compose(
        target,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "phlo",
        "-d",
        "phlo",
        input=(backup_dir / "postgres.sql").read_bytes(),
        timeout=300,
    )
    import_nessie(target, backup_dir)
    return manifest


def cleanup(stack: Stack) -> RecoveryDrillError | None:
    """Tear down a stack's containers and volumes, returning any failure instead of raising."""
    if not stack.compose_file.exists():
        return None
    try:
        compose(stack, "down", "--volumes", "--remove-orphans", timeout=180)
    except RecoveryDrillError as exc:
        return exc
    return None


def cleanup_all(stacks: tuple[Stack, ...]) -> RecoveryDrillError | None:
    """Tear down every stack, aggregating individual failures into one error."""
    failures = [f"{stack.project}: {error}" for stack in stacks if (error := cleanup(stack))]
    if failures:
        return RecoveryDrillError(
            "cleanup failed; owned diagnostics preserved: " + "; ".join(failures)
        )
    return None


def owned_directory(path: Path, token: str) -> None:
    """Create a new directory stamped with this run's owner marker."""
    path.mkdir(parents=True, exist_ok=False)
    (path / OWNER_MARKER).write_text(json.dumps({"token": token}), encoding="utf-8")


# Delete only when the marker proves this run created the directory; anything
# else (foreign data, a marker from another run) is left in place.
def remove_owned(path: Path, token: str) -> None:
    """Delete a directory only when its owner marker matches this run's token."""
    marker = path / OWNER_MARKER
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("token") == token:
        shutil.rmtree(path)


def drill(root: Path, *, keep_artifacts: bool = False) -> dict[str, float]:
    """Execute the full backup-and-restore scenario and return backup/restore durations."""
    token = uuid4().hex[:12]
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / f"recovery-drill-{token}"
    source = Stack(f"phlo-recovery-source-{token}", run_dir / "source")
    target = Stack(f"phlo-recovery-restore-{token}", run_dir / "restore")
    stacks = (target, source)
    owned_directory(run_dir, token)
    try:
        for stack in (source, target):
            owned_directory(stack.directory, token)
            stack.compose_file.write_text(compose_yaml(stack), encoding="utf-8")
        backup = run_dir / "backup"
        backup.mkdir()
        helper_dir = run_dir / "helper"
        helper_dir.mkdir()
        prepare_helper(helper_dir)
        probe = backup / "probe.json"
        probe.write_text(
            json.dumps({"created_at": datetime.now(UTC).isoformat(), "token": token}),
            encoding="utf-8",
        )
        key = f"recovery-drill/{token}/probe.json"
        checksum = hashlib.sha256(probe.read_bytes()).hexdigest()
        start(source, with_nessie=True)
        prepare_bucket(source, probe, key)
        fixture = create_fixture(source, token, helper_dir)
        backup_started = time.monotonic()
        dump = compose(
            source,
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "--clean",
            "--if-exists",
            "--no-owner",
            "-U",
            "phlo",
            "phlo",
            timeout=300,
        ).stdout
        (backup / "postgres.sql").write_bytes(dump)
        export_nessie(source, backup)
        mirror_bucket(source, backup)
        write_manifest(backup, fixture, checksum)
        backup_seconds = time.monotonic() - backup_started
        compose(source, "restart", "postgres", "minio", "nessie", timeout=180)
        start(source, with_nessie=True)

        restore_started = time.monotonic()
        start(target, with_nessie=False)
        manifest = restore_recovery_set(target, backup)
        compose(target, "up", "-d", "nessie", timeout=300)
        wait_for(
            f"http://127.0.0.1:{published_port(target, 'nessie', 19120)}/api/v1/config",
            name=f"{target.project} Nessie",
            timeout=180,
        )
        verify_fixture(target, manifest["fixture"], manifest["probe_checksum"], key, helper_dir)
        return {
            "backup_seconds": round(backup_seconds, 3),
            "restore_seconds": round(time.monotonic() - restore_started, 3),
        }
    finally:
        cleanup_error = cleanup_all(stacks)
        if cleanup_error is not None:
            raise cleanup_error
        if not keep_artifacts:
            remove_owned(run_dir, token)


def main() -> int:
    """Parse CLI arguments, run the drill, and return the process exit code."""
    parser = argparse.ArgumentParser(
        description="Run an isolated Phlo backup and recovery continuity drill."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("PHLO_TESTING_ROOT", Path.home() / "Developer/phlo-testing")),
        help="directory for a new owned disposable drill directory",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="keep only this drill's owned files after cleanup for diagnosis",
    )
    args = parser.parse_args()
    try:
        result = drill(args.root, keep_artifacts=args.keep_artifacts)
    except Exception as exc:
        print(
            json.dumps(
                {"outcome": "failed", "continuity_drill": True, "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"outcome": "verified", "continuity_drill": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
