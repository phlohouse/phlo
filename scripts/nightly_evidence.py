#!/usr/bin/env python3
"""Nightly operations-evidence lane for the v1 support manifest.

Boots a scratch project's postgres/minio/nessie services, syncs canonical
RBAC onto the live backends, verifies convergence, then runs one journaled
snapshot-expiry plan/execute against a real table. Writes one JSON artifact
plus the durable journal records.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_path_common import project_env_paths, read_env_file  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

GOVERNED_BACKENDS = ("postgres", "minio", "nessie")
EVIDENCE_TABLE = "raw.nightly_evidence"
EVIDENCE_NAMESPACE = "raw"

# Postgres only manages grantees matching phlo_*; MinIO groups take the role
# name verbatim. Both roles use the managed prefix so all three backends
# govern them.
ROLES_YAML = """version: 1
roles:
  phlo_nightly_reader:
    description: Nightly evidence read-only role
  phlo_nightly_writer:
    inherits:
      - phlo_nightly_reader
    description: Nightly evidence write role
subjects:
  services:
    dagster-agent:
      - phlo_nightly_writer
"""

# Covers every governed pair: postgres dataset.*, minio object.* + dataset.*,
# nessie catalog.* + dataset.*. Pairs a backend does not govern are skipped
# by that backend's compiler.
POLICIES_YAML = """version: 1
policies:
  - policy_id: nightly-dataset-read
    effect: allow
    principal:
      roles:
        - phlo_nightly_reader
    action: dataset.read
    resource:
      type: dataset
      id_pattern: "public.*"
  - policy_id: nightly-dataset-query
    effect: allow
    principal:
      roles:
        - phlo_nightly_reader
    action: dataset.query
    resource:
      type: dataset
      id_pattern: "public.*"
  - policy_id: nightly-dataset-write
    effect: allow
    principal:
      roles:
        - phlo_nightly_writer
    action: dataset.write
    resource:
      type: dataset
      id_pattern: "public.*"
  - policy_id: nightly-object-read
    effect: allow
    principal:
      roles:
        - phlo_nightly_reader
    action: object.read
    resource:
      type: object
      id_pattern: "lake/warehouse/*"
  - policy_id: nightly-object-write
    effect: allow
    principal:
      roles:
        - phlo_nightly_writer
    action: object.write
    resource:
      type: object
      id_pattern: "lake/warehouse/*"
  - policy_id: nightly-catalog-read
    effect: allow
    principal:
      roles:
        - phlo_nightly_reader
    action: catalog.read
    resource:
      type: catalog
      id_pattern: "*"
"""


class StepError(RuntimeError):
    """One evidence step failed; the report records it and the run exits 1."""


def run(
    cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command, returning the completed process; raises on failure."""
    completed = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900, check=False
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise StepError(f"{' '.join(cmd)} exited {completed.returncode}: {detail[-800:]}")
    return completed


def _phlo_bin() -> str:
    """Resolve the phlo executable next to this interpreter's environment."""
    exe_dir = Path(sys.executable).parent
    for name in ("phlo", "phlo.exe"):
        candidate = exe_dir / name
        if candidate.exists():
            return str(candidate)
    raise StepError(f"phlo executable not found next to {sys.executable}")


def phlo(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    """Invoke the repo environment's phlo CLI and return stdout.

    When ``cwd`` is a generated project, its .phlo env layers are merged into
    the subprocess environment so host-side connections (postgres, minio,
    nessie) resolve the published ports and credentials.
    """
    merged = dict(os.environ)
    phlo_dir = cwd / ".phlo"
    if phlo_dir.is_dir():
        merged.update(read_env_layers(phlo_dir))
    if env:
        merged.update(env)
    return run([_phlo_bin(), *args], cwd=cwd, env=merged).stdout


def _project_name(project: Path) -> str:
    """Match the compose project name `phlo services` derives."""
    config_path = project / "phlo.yaml"
    if config_path.is_file():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            return data["name"]
    return project.name.lower().replace(" ", "-").replace("_", "-")


def compose_files(project: Path) -> list[str]:
    """Return the -f layer list matching what ``phlo services`` merges."""
    phlo_dir = project / ".phlo"
    files = ["-f", str(phlo_dir / "docker-compose.yml")]
    for override in (phlo_dir / "overrides" / "compose.yaml",):
        if override.is_file():
            files += ["-f", str(override)]
    return files


def _compose_cmd(project: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        *compose_files(project),
        "-p",
        _project_name(project),
        *args,
    ]


def compose(project: Path, *args: str) -> str:
    """Run docker compose against the generated project stack."""
    return run(_compose_cmd(project, *args), cwd=project).stdout


def read_env_layers(phlo_dir: Path) -> dict[str, str]:
    """Merge the generated project's env files in precedence order."""
    merged: dict[str, str] = {}
    for path in project_env_paths(phlo_dir):
        merged.update(read_env_file(path))
    return merged


def wait_http(url: str, *, timeout: float = 180.0) -> None:
    """Wait for an HTTP endpoint to answer any response."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except Exception:
            time.sleep(2)
    raise StepError(f"timed out waiting for {url}")


def init_project(scratch: Path) -> Path:
    """Scaffold a minimal project and generate its .phlo service stack."""
    project = scratch / "nightly-evidence-project"
    phlo("init", str(project), "--template", "csv-batch", cwd=REPO_ROOT)
    phlo("services", "init", cwd=project)
    return project


def write_rbac_config(project: Path) -> Path:
    """Write the canonical roles and policies fixture under .phlo."""
    auth_dir = project / ".phlo" / "authorization"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "roles.yaml").write_text(ROLES_YAML, encoding="utf-8")
    (auth_dir / "policies.yaml").write_text(POLICIES_YAML, encoding="utf-8")
    return auth_dir


def write_host_endpoint_override(project: Path) -> Path:
    """Make Nessie advertise the host-published MinIO endpoint.

    pyiceberg builds a table's FileIO purely from server-vended config, so
    Nessie's default ``http://minio:9000/`` advertisement is unreachable for a
    host-side client. ``external-endpoint`` is the client-facing value; the
    server's own ``endpoint`` stays ``minio:9000`` for commit writes. The
    scratch lane starts no in-compose data-plane clients, so advertising
    ``localhost:${MINIO_API_PORT}`` is safe.
    """
    override = project / ".phlo" / "overrides" / "compose.yaml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(
        "services:\n"
        "  nessie:\n"
        "    environment:\n"
        "      nessie.catalog.service.s3.default-options.external-endpoint:"
        ' "http://localhost:${MINIO_API_PORT:-9000}/"\n',
        encoding="utf-8",
    )
    return override


def start_services(project: Path) -> None:
    """Start only the governed-backend services plus their init helpers."""
    phlo(
        "services",
        "start",
        "--service",
        "postgres",
        "--service",
        "minio",
        "--service",
        "minio-setup",
        "--service",
        "nessie",
        cwd=project,
    )
    env_values = read_env_layers(project / ".phlo")
    nessie_port = env_values.get("NESSIE_PORT", "19120")
    wait_http(f"http://localhost:{nessie_port}/api/v1/config")


def _try(cmd: list[str], cwd: Path) -> bool:
    """Run a probe command; True on exit 0."""
    return _probe(cmd, cwd)[0] == 0


def _probe(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run a probe command; return (exit code, combined output)."""
    completed = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def probe_compose(project: Path, *args: str) -> tuple[int, str]:
    """Like compose() but returns (exit code, combined output)."""
    return _probe(_compose_cmd(project, *args), cwd=project)


def try_compose(project: Path, *args: str) -> bool:
    """Like compose() but returns True/False instead of raising."""
    return _try(_compose_cmd(project, *args), cwd=project)


def wait_stack_ready(project: Path) -> None:
    """Wait for postgres, minio, and the bucket-setup container to finish.

    `phlo services start` returns at container creation, not readiness;
    exec-based steps need real readiness first.
    """
    env_values = read_env_layers(project / ".phlo")
    pg_user = env_values.get("POSTGRES_USER", "phlo")
    minio_port = env_values.get("MINIO_API_PORT", "10001")
    name = _project_name(project)
    base = [
        "docker",
        "compose",
        "-f",
        str(project / ".phlo" / "docker-compose.yml"),
        "-p",
        name,
    ]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        pg_ok = _try([*base, "exec", "-T", "postgres", "pg_isready", "-U", pg_user], cwd=project)
        minio_ok = False
        try:
            with urllib.request.urlopen(
                f"http://localhost:{minio_port}/minio/health/live", timeout=5
            ):
                minio_ok = True
        except Exception:
            pass
        setup_out = subprocess.run(
            [
                *base,
                "ps",
                "--status",
                "exited",
                "--format",
                "{{.ExitCode}}",
                "minio-setup",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.strip()
        if pg_ok and minio_ok and setup_out == "0":
            return
        time.sleep(3)
    raise StepError("stack readiness timed out (postgres/minio/minio-setup)")


EVIDENCE_ROLES = ("phlo_nightly_reader", "phlo_nightly_writer")


def create_postgres_roles(project: Path) -> None:
    """Create the fixture roles so GRANT statements can target them.

    Also creates the enforcement-probe fixtures before sync so the
    ``ALL TABLES`` grant covers the allowed table directly.
    """
    env_values = read_env_layers(project / ".phlo")
    user = env_values.get("POSTGRES_USER", "phlo")
    for role in EVIDENCE_ROLES:
        compose(
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            user,
            "-c",
            f"CREATE ROLE {role} NOLOGIN;",
        )
    compose(
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        user,
        "-c",
        "CREATE TABLE IF NOT EXISTS public.nightly_probe_allow (id int);"
        "CREATE SCHEMA IF NOT EXISTS nightly_private;"
        "CREATE TABLE IF NOT EXISTS nightly_private.nightly_probe_deny (id int);",
    )


def create_minio_groups(project: Path) -> None:
    """Create one MinIO user+group per role; policy attach needs the group.

    A MinIO group only exists once it has a member, so each role gets a
    matching service user.
    """
    env_values = read_env_layers(project / ".phlo")
    user = env_values.get("MINIO_ROOT_USER", "minio")
    password = env_values.get("MINIO_ROOT_PASSWORD", "minio123")
    commands = [
        f"mc alias set local http://localhost:9000 {user} {password} >/dev/null",
    ]
    for role in EVIDENCE_ROLES:
        member = role.replace("_", "-") + "-svc"
        commands.append(f"mc admin user add local {member} {member}-secret-1")
        commands.append(f"mc admin group add local {role} {member}")
    compose(project, "exec", "-T", "minio", "/bin/sh", "-c", " && ".join(commands))


def sync_and_verify(project: Path) -> dict[str, Any]:
    """Sync canonical RBAC to live backends, restart Nessie, then verify.

    The verify output is recorded whether or not drift is reported — a
    non-zero verify is evidence of non-convergence, not a lost artifact.
    """
    phlo_dir = project / ".phlo"
    backend_args = [arg for name in GOVERNED_BACKENDS for arg in ("--backend", name)]
    phlo("authz", "validate", "--path", str(phlo_dir), cwd=project)
    sync_out = phlo("authz", "sync", "--path", str(phlo_dir), *backend_args, cwd=project)
    # Nessie loads rendered rules only at boot; a restart applies them.
    compose(project, "restart", "nessie")
    env_values = read_env_layers(phlo_dir)
    nessie_port = env_values.get("NESSIE_PORT", "19120")
    wait_http(f"http://localhost:{nessie_port}/api/v1/config")
    merged_env = dict(os.environ)
    merged_env.update(read_env_layers(phlo_dir))
    verify = subprocess.run(
        [_phlo_bin(), "authz", "verify", "--path", str(phlo_dir), *backend_args],
        cwd=project,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return {
        "sync_output": sync_out,
        "verify_exit_code": verify.returncode,
        "verify_output": verify.stdout + verify.stderr,
        "converged": verify.returncode == 0,
    }


def _iceberg_env(project: Path) -> None:
    """Point phlo_iceberg settings at the scratch stack's catalog and MinIO.

    Must run before any phlo_iceberg import so settings resolve this stack.
    The generated env layers carry NESSIE_PORT/MINIO_API_PORT, which the
    settings' host-resolution turns into localhost-published endpoints; only
    the S3 credential names need mapping.
    """
    env_values = read_env_layers(project / ".phlo")
    os.environ.update(env_values)
    os.environ["PHLO_ICEBERG_S3_ACCESS_KEY"] = env_values.get("MINIO_ROOT_USER", "minio")
    os.environ["PHLO_ICEBERG_S3_SECRET_KEY"] = env_values.get("MINIO_ROOT_PASSWORD", "minio123")


def create_iceberg_table() -> str:
    """Create one real Iceberg table with one snapshot for maintenance."""
    import pyarrow as pa
    from phlo_iceberg.resource import IcebergResource

    resource = IcebergResource(ref="main")
    catalog = resource.get_catalog()
    catalog.create_namespace_if_not_exists(EVIDENCE_NAMESPACE)
    try:
        catalog.create_table(EVIDENCE_TABLE, schema=pa.schema([pa.field("id", pa.int64())]))
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise
    table = catalog.load_table(EVIDENCE_TABLE)
    table.append(pa.table({"id": [1, 2, 3]}))
    return EVIDENCE_TABLE


def journaled_maintenance(project: Path, journal_dir: Path) -> dict[str, Any]:
    """Run one journaled plan/apply cycle through the operations CLI.

    ``phlo operations maintenance plan`` emits the store's dry-run result
    (``plan_token`` + ``before_revision`` bound); ``apply`` re-binds the exact
    plan through the durable journal's claim → submit → execute → complete
    lifecycle. A fresh table's single protected snapshot yields an accepted
    no-op execution — the full precondition and journal chain still runs
    against the real catalog.
    """
    env = {"PHLO_OPERATIONS_JOURNAL_DIR": str(journal_dir)}

    plan_path = journal_dir.parent / "maintenance-plan.json"
    plan_out = phlo(
        "operations",
        "maintenance",
        "plan",
        "--operation",
        "snapshot_expiry",
        "--table",
        EVIDENCE_TABLE,
        "--format",
        "json",
        cwd=project,
        env=env,
    )
    plan_path.write_text(plan_out, encoding="utf-8")
    plan = json.loads(plan_out)
    token = str(plan.get("plan_token") or "")
    if not token or plan.get("before_revision") is None:
        raise StepError(f"maintenance plan missing token/revision: {plan_out[:400]}")

    apply_out = phlo(
        "operations",
        "maintenance",
        "apply",
        "--plan",
        str(plan_path),
        "--confirmation-token",
        token,
        "--format",
        "json",
        cwd=project,
        env=env,
    )
    execute = json.loads(apply_out)
    states = [
        json.loads(record.read_text(encoding="utf-8")).get("state")
        for record in sorted(journal_dir.glob("*.json"))
    ]
    if execute.get("accepted") is not True or execute.get("status") not in (
        "noop",
        "succeeded",
    ):
        raise StepError(f"maintenance apply was not accepted: {apply_out[:400]}")
    if not states or any(state != "succeeded" for state in states):
        raise StepError(f"journal recorded no succeeded operation: {states}")
    return {
        "plan": plan,
        "execute_result": execute,
        "journal_records": [p.name for p in sorted(journal_dir.glob("*.json"))],
        "journal_states": states,
    }


def _http_status(url: str) -> int | None:
    """Return the HTTP status for a GET, or None when unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def enforcement_probe(project: Path) -> dict[str, Any]:
    """Probe live allow/deny behavior against the synced policies.

    Postgres and MinIO enforce at request time, so the probe issues a real
    allowed and a real denied request on each. Nessie only evaluates its
    authorization rules when authentication is enabled and the dev stack
    ships no OIDC provider, so its probe enables authorization, recreates
    the server, and records the observed anonymous status — an explicit
    boundary record, not implied enforcement.
    """
    env_values = read_env_layers(project / ".phlo")
    pg_user = env_values.get("POSTGRES_USER", "phlo")
    nessie_port = env_values.get("NESSIE_PORT", "19120")
    reader_secret = "phlo-nightly-reader-svc-secret-1"

    pg_allow_rc, _ = probe_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        pg_user,
        "-c",
        "SET ROLE phlo_nightly_reader; SELECT count(*) FROM public.nightly_probe_allow;",
    )
    pg_deny_rc, pg_deny_out = probe_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        pg_user,
        "-c",
        "SET ROLE phlo_nightly_reader; SELECT * FROM nightly_private.nightly_probe_deny;",
    )
    postgres_allow = pg_allow_rc == 0
    postgres_deny = pg_deny_rc != 0 or "permission denied" in pg_deny_out.lower()

    # The reader service user authenticates as itself; its group policy scopes
    # access to lake/warehouse/*. Listing the bucket root is outside the grant.
    # mc exits 0 even on Access Denied, so deny is judged on output text.
    minio_alias = (
        "mc alias set probe http://localhost:9000 phlo-nightly-reader-svc "
        f"{reader_secret} >/dev/null"
    )
    mc_allow_rc, mc_allow_out = probe_compose(
        project,
        "exec",
        "-T",
        "minio",
        "/bin/sh",
        "-c",
        f"{minio_alias} && mc ls probe/lake/warehouse/",
    )
    _, mc_deny_out = probe_compose(
        project,
        "exec",
        "-T",
        "minio",
        "/bin/sh",
        "-c",
        f"{minio_alias} && mc ls probe/lake/",
    )
    minio_allow = mc_allow_rc == 0 and "access denied" not in mc_allow_out.lower()
    minio_deny = "access denied" in mc_deny_out.lower() or "accessdenied" in mc_deny_out.lower()

    if not postgres_allow:
        raise StepError("postgres allow probe failed: reader could not read public.*")
    if not postgres_deny:
        raise StepError("postgres deny probe failed: reader read nightly_private.*")
    if not minio_allow:
        raise StepError("minio allow probe failed: reader could not list lake/warehouse/*")
    if not minio_deny:
        raise StepError("minio deny probe failed: reader listed the lake bucket root")

    # Nessie: enable authorization, recreate, and record what an anonymous
    # request actually gets. Without an IdP there is no authenticated role,
    # so this documents the boundary rather than claiming denial.
    override = project / ".phlo" / "overrides" / "compose.yaml"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(
        "services:\n"
        "  nessie:\n"
        "    environment:\n"
        "      nessie.catalog.service.s3.default-options.external-endpoint:"
        ' "http://localhost:${MINIO_API_PORT:-9000}/"\n'
        '      NESSIE_SERVER_AUTHORIZATION_ENABLED: "true"\n',
        encoding="utf-8",
    )
    compose(project, "up", "-d", "nessie")
    wait_http(f"http://localhost:{nessie_port}/api/v1/config")
    nessie_status = _http_status(f"http://localhost:{nessie_port}/api/v1/trees")

    return {
        "postgres": {
            "allow": postgres_allow,
            "deny": postgres_deny,
            "deny_evidence": pg_deny_out.strip()[:200],
        },
        "minio": {
            "allow": minio_allow,
            "deny": minio_deny,
            "deny_evidence": mc_deny_out.strip()[:200],
        },
        "nessie": {
            "authorization_enabled": True,
            "anonymous_status": nessie_status,
            "enforcement_evaluated": nessie_status in (401, 403),
            "note": "dev stack ships no OIDC provider; Nessie evaluates "
            "authorization only under authentication",
        },
    }


def teardown(project: Path) -> None:
    """Stop the scratch stack; volume prune is left to the runner."""
    with contextlib.suppress(Exception):
        compose(project, "down", "--volumes", "--remove-orphans")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory receiving nightly-evidence.json, the plan, and the journal.",
    )
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        help="Leave the scratch stack running after the lane completes.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    journal_dir = output / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    scratch = Path(tempfile.mkdtemp(prefix="phlo-nightly-evidence-"))
    report: dict[str, Any] = {
        "steps": {},
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "host": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }
    project: Path | None = None
    try:
        report["steps"]["init"] = "running"
        project = init_project(scratch)
        report["steps"]["init"] = "ok"

        write_rbac_config(project)
        write_host_endpoint_override(project)
        start_services(project)
        wait_stack_ready(project)
        report["steps"]["stack"] = "ok"

        create_postgres_roles(project)
        create_minio_groups(project)
        report["steps"]["backend_principals"] = "ok"

        report["security"] = sync_and_verify(project)
        report["steps"]["authz_sync_verify"] = "ok"
        if not report["security"]["converged"]:
            raise StepError("authz verify reported drift after sync + restart")

        _iceberg_env(project)
        table = create_iceberg_table()
        report["steps"]["iceberg_table"] = "ok"

        report["maintenance"] = journaled_maintenance(project, journal_dir)
        report["steps"]["journaled_maintenance"] = "ok"
        report["maintenance"]["table"] = table

        report["enforcement"] = enforcement_probe(project)
        report["steps"]["enforcement_probe"] = "ok"
        return_code = 0
    except StepError as exc:
        report["error"] = str(exc)
        return_code = 1
    finally:
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (output / "nightly-evidence.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        if project is not None and not args.keep_stack:
            teardown(project)
        shutil.rmtree(scratch, ignore_errors=True)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
