#!/usr/bin/env python3
"""Run the Phlo release artifact golden path in an owned temporary project.

Drives the full release path end to end: build a wheelhouse, install
the operator, scaffold and configure a non-dev project, start the
stack, materialize fixture partitions, then exercise WAP promotion
including one deliberately rejected run. The script owns every
directory it creates and removes them on exit unless --keep-project is
given; any failure triggers runtime diagnostics before cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# The candidate-mode modules live beside this script; running as a script puts
# this directory on sys.path already, but importlib-based test loads do not.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import platform as platform_module  # noqa: E402

import release_candidate_bom as bom_module  # noqa: E402
import release_evidence  # noqa: E402

PARTITION = "2025-01-15"
FIXTURE_ROW_COUNT = 2
PORT_NAMES = (
    "POSTGRES_PORT",
    "MINIO_API_PORT",
    "MINIO_CONSOLE_PORT",
    "NESSIE_PORT",
    "TRINO_PORT",
    "DAGSTER_PORT",
    "PHLO_API_PORT",
)
WAP_RUN_TIMEOUT_SECONDS = 180
WAP_PROMOTION_TIMEOUT_SECONDS = 180
RUN_REPORT_TIMEOUT_SECONDS = 60
RUNTIME_DIAGNOSTIC_SERVICES = (
    "dagster",
    "dagster-daemon",
    "nessie",
    "minio",
    "trino",
    "phlo-api",
)

# Candidate mode: the quality gate reads this directive file inside
# the Dagster container at check time, so the harness can flip one owned file
# between the promoted run and the deliberately rejected run.
QUALITY_DIRECTIVE_CONTAINER_PATH = "/app/.phlo/quality_gate.json"
OPERATOR_SERVICE_ACCOUNT = "release-golden-path-operator"
UPGRADE_FROM_VERSION = "0.14.0"
UPGRADE_TO_VERSION = "0.15.0"
# Compose service name -> (container port, published-port env var).
COMPOSE_PORT_PROBES: tuple[tuple[str, int, str], ...] = (
    ("postgres", 5432, "POSTGRES_PORT"),
    ("minio", 9000, "MINIO_API_PORT"),
    ("minio", 9001, "MINIO_CONSOLE_PORT"),
    ("nessie", 19120, "NESSIE_PORT"),
    ("trino", 8080, "TRINO_PORT"),
    ("dagster", 3000, "DAGSTER_PORT"),
    ("phlo-api", 4000, "PHLO_API_PORT"),
)
# First-party packages the operator venv needs for the complete runtime journey.
OPERATOR_PACKAGES = ("phlo", "phlo-api", "phlo-iceberg", "phlo-dbt", "phlo-dlt", "phlo-pandera")
PROJECT_PACKAGES = ("phlo-dbt", "phlo-dlt", "phlo-pandera")


@dataclass(frozen=True)
class RunConfig:
    """Hold paths and names for one release golden path run."""

    repo_root: Path
    project_dir: Path
    wheelhouse: Path
    operator_env: Path
    project_name: str
    partition: str = PARTITION
    bom: dict[str, object] | None = None
    staging_dir: Path | None = None
    report_token: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    rejection_report_token: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)

    @property
    def operator_python(self) -> Path:
        """Return the operator venv's Python executable."""
        return venv_executable(self.operator_env, "python")

    @property
    def operator_bin(self) -> Path:
        """Return the operator venv's phlo executable."""
        return venv_executable(self.operator_env, "phlo")

    @property
    def project_env(self) -> Path:
        """Return the generated project's virtualenv directory."""
        return self.project_dir / ".venv"

    @property
    def project_python(self) -> Path:
        """Return the generated project venv's Python executable."""
        return venv_executable(self.project_env, "python")

    @property
    def compose_file(self) -> Path:
        """Return the generated project's Docker Compose file path."""
        return self.project_dir / ".phlo" / "docker-compose.yml"


@dataclass(frozen=True)
class WapRun:
    """The stable logical and provider IDs for one WAP materialization."""

    logical_run_id: str
    dagster_run_id: str


def venv_executable(environment: Path, executable: str) -> Path:
    """Return a virtualenv executable path for the current platform."""
    if os.name == "nt":
        candidates = (
            environment / "Scripts" / f"{executable}.exe",
            environment / "Scripts" / executable,
            environment / "bin" / executable,
        )
    else:
        candidates = (
            environment / "bin" / executable,
            environment / "Scripts" / f"{executable}.exe",
            environment / "Scripts" / executable,
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def command(*parts: str) -> list[str]:
    """Return a subprocess command as a list, keeping shell interpolation out."""
    return list(parts)


def compose_command(config: RunConfig, *parts: str) -> list[str]:
    """Build a project-scoped Docker Compose command."""
    return command(
        "docker",
        "compose",
        "-p",
        config.project_name,
        "--file",
        str(config.compose_file),
        "--env-file",
        str(config.project_dir / ".phlo" / ".env"),
        "--env-file",
        str(config.project_dir / ".phlo" / ".env.local"),
        *parts,
    )


def project_name() -> str:
    """Return a globally unique Compose project name for one harness run."""
    return f"phlo-qa001-{uuid.uuid4().hex}"


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one command and stream its output."""
    print(f"+ {' '.join(args)}", flush=True)
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=capture_output,
        text=True,
    )


def force_local_install(config: RunConfig, python: Path, *packages: str) -> None:
    """Force-install packages strictly from the local wheelhouse."""
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-index",
            "--no-deps",
            "--reinstall",
            "--find-links",
            str(config.wheelhouse),
            *packages,
        ),
        cwd=config.repo_root,
    )


def build_wheelhouse(config: RunConfig) -> None:
    """Build all workspace wheels into the wheelhouse."""
    config.wheelhouse.mkdir(parents=True, exist_ok=True)
    run(
        command("uv", "build", "--all-packages", "--wheel", "--out-dir", str(config.wheelhouse)),
        cwd=config.repo_root,
    )


# The ordering matters: dependency resolution may still pick a same-named
# workspace package from PyPI, so every run ends by re-pinning all workspace
# packages to the local wheelhouse with --no-index/--no-deps.
def install_operator(config: RunConfig) -> None:
    """Install the phlo CLI and core plugins into the operator venv."""
    run(command("uv", "venv", str(config.operator_env), "--python", "3.11"), cwd=config.repo_root)
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.operator_python),
            "--no-index",
            "--no-deps",
            "--find-links",
            str(config.wheelhouse),
            "phlo",
        ),
        cwd=config.repo_root,
    )
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.operator_python),
            "--find-links",
            str(config.wheelhouse),
            "phlo[core-services]",
            "phlo-api",
            "phlo-dbt",
            "phlo-dlt",
            "phlo-pandera",
        ),
        cwd=config.repo_root,
    )
    force_local_install(
        config,
        config.operator_python,
        "phlo",
        "phlo-api",
        "phlo-dbt",
        "phlo-dlt",
        "phlo-pandera",
    )


def create_project(config: RunConfig) -> None:
    """Scaffold a new CSV-batch project with the installed CLI."""
    run(
        command(
            str(config.operator_bin),
            "init",
            str(config.project_dir),
            "--template",
            "csv-batch",
        ),
        cwd=config.repo_root,
    )


def wap_service_secret(config: RunConfig) -> str:
    """Return the unique, temporary secret shared by this owned local stack."""
    return f"release-golden-path-{config.project_name}"


def service_token(service_id: str, secret: str) -> str:
    """Create the local development HMAC token accepted by Dagster."""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = f"{service_id}:{timestamp}:{nonce}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}:{signature}"


def write_transform_fixture(config: RunConfig) -> None:
    """Add one dbt mart to the generated CSV project for release acceptance."""
    dbt_dir = config.project_dir / "workflows" / "transforms" / "dbt"
    (dbt_dir / "models" / "marts").mkdir(parents=True, exist_ok=True)
    (dbt_dir / "models" / "sources").mkdir(parents=True, exist_ok=True)
    (dbt_dir / "dbt_project.yml").write_text(
        """name: release_golden_path
version: 1.0.0
config-version: 2
profile: phlo
model-paths: [\"models\"]

models:
  release_golden_path:
    +materialized: table
""",
        encoding="utf-8",
    )
    (dbt_dir / "models" / "sources" / "raw.yml").write_text(
        """version: 2

sources:
  - name: raw
    database: iceberg
    schema: raw
    tables:
      - name: events
""",
        encoding="utf-8",
    )
    (dbt_dir / "models" / "marts" / "events_mart.sql").write_text(
        """{{ config(materialized='table', schema='marts') }}
select event_id, id, name, value
from {{ source('raw', 'events') }}
""",
        encoding="utf-8",
    )


def write_wap_fixture(config: RunConfig, rejected_logical_run_id: str) -> None:
    """Add a check that rejects one WAP run and passes the happy path."""
    fixture = config.project_dir / "workflows" / "ingestion" / "csv" / "release_wap_check.py"
    fixture.write_text(
        f"""import dagster as dg


@dg.asset_check(asset=\"dlt_events\")
def release_golden_path_wap_check(context) -> dg.AssetCheckResult:
    run_tags = context.run.tags or {{}}
    rejected = run_tags.get(\"phlo/run_id\") == \"{rejected_logical_run_id}\"
    return dg.AssetCheckResult(
        passed=not rejected,
        metadata={{\"reason\": \"intentional_quality_rejection\" if rejected else \"happy_path\"}},
    )
""",
        encoding="utf-8",
    )


def write_report_policy_fixture(config: RunConfig) -> None:
    """Allow the scoped report reader through the policy boundary."""
    authorization_dir = config.project_dir / ".phlo" / "authorization"
    authorization_dir.mkdir(parents=True, exist_ok=True)
    (authorization_dir / "policies.yaml").write_text(
        """version: 1

policies:
  - policy_id: release-golden-path-wap-catalog-read
    effect: allow
    principal:
      attributes:
        authentication_source: service_token
    action: catalog.read
    resource:
      type: catalog
      id_pattern: "*"
  - policy_id: release-golden-path-wap-run-execute
    effect: allow
    principal:
      attributes:
        authentication_source: service_token
    action: run.execute
    resource:
      type: run
      id_pattern: "*"
  - policy_id: release-golden-path-wap-run-read
    effect: allow
    principal:
      attributes:
        authentication_source: service_token
    action: run.read
    resource:
      type: run
      id_pattern: "*"
  - policy_id: release-golden-path-report-read
    effect: allow
    principal:
      attributes:
        qa001_role: report_reader
    action: run.read
    resource:
      type: run
      id_pattern: "*"
""",
        encoding="utf-8",
    )


def align_project_name(config: RunConfig) -> None:
    """Make generated CLI project discovery use the owned Compose project name."""
    config_file = config.project_dir / "phlo.yaml"
    lines = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("name:"):
            lines[index] = f"name: {config.project_name}\n"
            config_file.write_text("".join(lines), encoding="utf-8")
            return
    raise RuntimeError(f"generated project config has no name: {config_file}")


def install_project_dependencies(config: RunConfig) -> None:
    """Install plugin dependencies into the generated project venv."""
    run(command("uv", "venv", str(config.project_env), "--python", "3.11"), cwd=config.project_dir)
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.project_python),
            "--find-links",
            str(config.wheelhouse),
            "phlo-dbt",
            "phlo-dlt",
            "phlo-pandera",
        ),
        cwd=config.project_dir,
    )
    force_local_install(
        config,
        config.project_python,
        "phlo-dbt",
        "phlo-dlt",
        "phlo-pandera",
    )


def configure_non_dev_compose(
    config: RunConfig,
) -> None:
    """Initialize non-dev services config and write the release environment."""
    run(
        command(
            str(config.operator_bin),
            "services",
            "init",
            "--no-dev",
            "--profile",
            "api",
            "--force",
        ),
        cwd=config.project_dir,
    )
    destination = config.project_dir / ".phlo" / "wheelhouse"
    shutil.copytree(config.wheelhouse, destination)
    with (config.repo_root / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    env_local = config.project_dir / ".phlo" / ".env.local"
    with env_local.open("a", encoding="utf-8") as stream:
        stream.write(f"\nPHLO_VERSION={version}\nPHLO_WHEELHOUSE=wheelhouse\n")
        stream.write("PHLO_WAP_BRANCH_CREATION_INTERVAL_SECONDS=1\n")
        stream.write("PHLO_WAP_PROMOTION_INTERVAL_SECONDS=1\n")
        stream.write(f"PHLO_PROJECT={config.project_name}\n")
        stream.write(f"PHLO_SERVICE_SECRET={wap_service_secret(config)}\n")
        stream.write("PHLO_LOG_FILE_TEMPLATE=/tmp/phlo-{YMD}.log\n")
        stream.write("PHLO_AUTHENTICATION_PROVIDER=service_token\n")
        stream.write("PHLO_AUTH_SERVICE_ENABLED=true\n")
        stream.write("PHLO_AUTHORIZATION_BACKEND=default\n")
        stream.write("PHLO_AUTHORIZATION_MODE=required\n")
        stream.write("PHLO_AUTH_SERVICE_TOKENS={}\n")
        stream.writelines(f"{name}=0\n" for name in PORT_NAMES)


def start_stack(config: RunConfig) -> None:
    """Start the API-profile Compose stack, dumping diagnostics on failure."""
    try:
        run(
            compose_command(config, "--profile", "api", "up", "--detach", "--build"),
            cwd=config.project_dir,
        )
    except subprocess.CalledProcessError:
        for parts in (("ps",), ("logs", "--no-color", "--timestamps")):
            try:
                run(compose_command(config, *parts), cwd=config.project_dir)
            except Exception as exc:
                print(f"release golden path diagnostics failed: {exc}", file=sys.stderr)
        raise


def materialize_partition(config: RunConfig) -> None:
    """Materialize the DLT events asset for the configured partition."""
    run(
        command(
            str(config.operator_bin),
            "materialize",
            "dlt_events",
            "--partition",
            config.partition,
        ),
        cwd=config.project_dir,
    )


def materialize_transform(config: RunConfig) -> None:
    """Materialize the dbt events mart for the configured partition."""
    run(
        command(
            str(config.operator_bin),
            "materialize",
            "events_mart",
            "--partition",
            config.partition,
        ),
        cwd=config.project_dir,
    )


def verify_minio_storage(config: RunConfig) -> None:
    """Prove the owned object store is ready and accepts an authenticated write."""
    bucket = f"qa001-evidence-{uuid.uuid4().hex}"
    check = "\n".join(
        (
            "set -eu",
            "curl -fsS http://localhost:9000/minio/health/ready >/dev/null",
            'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null',
            f"mc mb --ignore-existing local/{bucket} >/dev/null",
            f"printf %s release-golden-path | mc pipe local/{bucket}/ready >/dev/null",
            f"mc stat local/{bucket}/ready >/dev/null",
        )
    )
    run(
        compose_command(config, "exec", "--no-TTY", "minio", "/bin/sh", "-c", check),
        cwd=config.project_dir,
    )
    print("MinIO readiness and owned S3 write check passed")


def service_url(config: RunConfig, service: str, container_port: int, path: str = "") -> str:
    """Resolve an owned Compose service's dynamically published host URL."""
    result = run(
        compose_command(config, "port", service, str(container_port)),
        cwd=config.project_dir,
        capture_output=True,
    )
    address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        port = int(address.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(
            f"could not resolve {service} port {container_port}: {address!r}"
        ) from exc
    return f"http://127.0.0.1:{port}{path}"


def graphql(url: str, query: str, variables: dict[str, object], token: str) -> dict[str, object]:
    """Call the local Dagster GraphQL endpoint and reject GraphQL errors."""
    request = urllib.request.Request(
        url,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace").strip()
        raise RuntimeError(f"Dagster GraphQL HTTP {exc.code}: {body or exc.reason}") from exc
    if not isinstance(payload, dict) or payload.get("errors"):
        raise RuntimeError(f"Dagster GraphQL query failed: {payload!r}")
    return payload


def discover_dagster_selector(config: RunConfig, token: str) -> tuple[str, str]:
    """Find the live repository that exposes Dagster's generated asset job."""
    payload = graphql(
        service_url(config, "dagster", 3000, "/graphql"),
        """query Repositories {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes { name location { name } pipelines { name } }
    }
    ... on PythonError { message }
  }
}""",
        {},
        token,
    )
    connection = (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get(
        "repositoriesOrError", {}
    )
    nodes = connection.get("nodes", []) if isinstance(connection, dict) else []
    for repository in nodes:
        if not isinstance(repository, dict):
            continue
        pipelines = repository.get("pipelines", [])
        if "__ASSET_JOB" not in {
            pipeline.get("name") for pipeline in pipelines if isinstance(pipeline, dict)
        }:
            continue
        location = repository.get("location", {})
        location_name = location.get("name") if isinstance(location, dict) else None
        repository_name = repository.get("name")
        if isinstance(location_name, str) and isinstance(repository_name, str):
            return location_name, repository_name
    raise RuntimeError("Dagster did not expose __ASSET_JOB in a live repository")


def configure_wap(config: RunConfig) -> None:
    """Configure the generated project to use its owned Dagster deployment for WAP."""
    token = service_token("phlo-api", wap_service_secret(config))
    location_name, repository_name = discover_dagster_selector(config, token)
    dagster_url = service_url(config, "dagster", 3000, "/graphql")
    with (config.project_dir / "phlo.yaml").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nwap:\n"
            "  enabled: true\n"
            "  job_name: __ASSET_JOB\n"
            f"  repository_location_name: {location_name}\n"
            f"  repository_name: {repository_name}\n"
            f"  dagster_url: {dagster_url}\n"
        )


def materialize_wap(config: RunConfig) -> WapRun:
    """Launch the generated ingestion asset through the public WAP CLI path."""
    token = service_token("phlo-api", wap_service_secret(config))
    nessie_url = service_url(config, "nessie", 19120)
    environment = {
        **os.environ,
        "NESSIE_HOST": "127.0.0.1",
        "NESSIE_PORT": nessie_url.rsplit(":", 1)[1],
        "PHLO_DAGSTER_ACCESS_TOKEN": token,
    }
    try:
        result = run(
            command(
                str(config.operator_bin),
                "materialize",
                "dlt_events",
                "--partition",
                config.partition,
            ),
            cwd=config.project_dir,
            env=environment,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"WAP materialization failed: {exc.stdout}\n{exc.stderr}") from exc
    match = re.search(r"logical run ([0-9a-z]+), Dagster run ([0-9a-z-]+)", result.stdout)
    if match is None:
        raise RuntimeError(f"WAP launch did not return a Dagster run ID: {result.stdout!r}")
    return WapRun(logical_run_id=match.group(1), dagster_run_id=match.group(2))


def wait_for_wap_promotion(config: RunConfig, wap_run: WapRun) -> None:
    """Require the launched WAP run to succeed and promote its owned branch."""
    token = service_token("phlo-api", wap_service_secret(config))
    dagster_url = service_url(config, "dagster", 3000, "/graphql")
    run_query = """query Run($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run { status tags { key value } }
    ... on PythonError { message }
  }
}"""
    deadline = time.monotonic() + WAP_RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = graphql(dagster_url, run_query, {"runId": wap_run.dagster_run_id}, token)
        run_data = (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get(
            "pipelineRunOrError", {}
        )
        if not isinstance(run_data, dict):
            raise RuntimeError(f"Dagster did not return WAP run data: {run_data!r}")
        status = run_data.get("status")
        if status in {"FAILURE", "CANCELED", "CANCELING"}:
            raise RuntimeError(f"WAP Dagster run finished with {status}")
        if status == "SUCCESS":
            break
        time.sleep(1)
    else:
        raise TimeoutError(f"WAP Dagster run did not finish: {wap_run.dagster_run_id}")

    deadline = time.monotonic() + WAP_PROMOTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = graphql(dagster_url, run_query, {"runId": wap_run.dagster_run_id}, token)
        run_data = (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get(
            "pipelineRunOrError", {}
        )
        tags = (
            {
                str(tag.get("key")): str(tag.get("value"))
                for tag in run_data.get("tags", [])
                if isinstance(tag, dict)
                and tag.get("key") is not None
                and tag.get("value") is not None
            }
            if isinstance(run_data, dict)
            else {}
        )
        if tags.get("phlo/wap_promoted") == "true":
            return
        time.sleep(1)
    raise TimeoutError(f"WAP run was not promoted: {wap_run.dagster_run_id}")


def fetch_run_report(config: RunConfig, wap_run: WapRun, token: str) -> dict[str, object]:
    """Read one exact run report, waiting for its durable projection."""
    url = service_url(
        config,
        "phlo-api",
        4000,
        f"/api/observatory/projects/{config.project_name}/runs/{wap_run.logical_run_id}/attempts/1/report",
    )
    deadline = time.monotonic() + RUN_REPORT_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                payload = json.load(response)
            if isinstance(payload, dict) and payload.get("run_id") == wap_run.logical_run_id:
                return payload
            raise RuntimeError(f"run report returned the wrong run: {payload!r}")
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
            last_error = exc
            time.sleep(1)
    else:
        raise RuntimeError(
            f"run report was not available for {wap_run.logical_run_id}: {last_error}"
        )


def verify_run_report(config: RunConfig, wap_run: WapRun) -> None:
    """Prove the scoped service token reads its report and no other report."""
    fetch_run_report(config, wap_run, config.report_token)

    other_url = service_url(
        config,
        "phlo-api",
        4000,
        f"/api/observatory/projects/{config.project_name}/runs/{wap_run.logical_run_id}-other/attempts/1/report",
    )
    request = urllib.request.Request(
        other_url,
        headers={"Authorization": f"Bearer {config.report_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except urllib.error.HTTPError as exc:
        body = json.load(exc)
        if exc.code == 403 and body == {
            "error": "forbidden",
            "reason": "run_report_scope_mismatch",
        }:
            return
        raise RuntimeError(f"unexpected scoped report response: {exc.code} {body!r}") from exc
    raise RuntimeError("scoped report token read another run report")


def verify_rejected_wap_report(config: RunConfig, wap_run: WapRun) -> None:
    """Require durable rejected-quality evidence and prove that run was not promoted."""
    payload = fetch_run_report(config, wap_run, config.rejection_report_token)
    deadline = time.monotonic() + RUN_REPORT_TIMEOUT_SECONDS
    while True:
        catalog_changes = payload.get("catalog_changes")
        if isinstance(catalog_changes, list) and any(
            isinstance(change, dict) and change.get("merge_outcome") == "rejected_quality"
            for change in catalog_changes
        ):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
        payload = fetch_run_report(config, wap_run, config.rejection_report_token)

    quality = payload.get("quality")
    if not isinstance(quality, list) or not any(
        isinstance(result, dict)
        and result.get("blocking") is True
        and result.get("passed") is False
        for result in quality
    ):
        raise RuntimeError(
            f"rejected WAP report lacks a blocking failed quality result: {payload!r}"
        )
    if not isinstance(catalog_changes, list) or not any(
        isinstance(change, dict) and change.get("merge_outcome") == "rejected_quality"
        for change in catalog_changes
    ):
        raise RuntimeError(f"rejected WAP report lacks rejection evidence: {payload!r}")

    token = service_token("phlo-api", wap_service_secret(config))
    dagster_url = service_url(config, "dagster", 3000, "/graphql")
    run_query = """query Run($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run { tags { key value } }
    ... on PythonError { message }
  }
}"""
    response = graphql(dagster_url, run_query, {"runId": wap_run.dagster_run_id}, token)
    run_data = (response.get("data", {}) if isinstance(response.get("data"), dict) else {}).get(
        "pipelineRunOrError", {}
    )
    if not isinstance(run_data, dict):
        raise RuntimeError(f"Dagster did not return rejected WAP run data: {run_data!r}")
    tags = {
        str(tag.get("key")): str(tag.get("value"))
        for tag in run_data.get("tags", [])
        if isinstance(tag, dict) and tag.get("key") is not None and tag.get("value") is not None
    }
    if tags.get("phlo/wap_promoted") == "true":
        raise RuntimeError(f"quality-rejected WAP run was promoted: {wap_run.dagster_run_id}")


def verify_rows(
    config: RunConfig, table: str = "raw.events", expected_count: int = FIXTURE_ROW_COUNT
) -> None:
    """Require an Iceberg table's Trino row count to match expectations."""
    query = f"SELECT count(*) FROM iceberg.{table}"
    try:
        result = run(
            compose_command(config, "exec", "--no-TTY", "trino", "trino", "--execute", query),
            cwd=config.project_dir,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        details = "\n".join(
            output.strip() for output in (exc.stdout, exc.stderr) if output and output.strip()
        )
        raise RuntimeError(f"Trino query failed for {table}: {details or exc}") from exc
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    try:
        last_line = result.stdout.strip().splitlines()[-1].strip()
        if len(last_line) >= 2 and last_line[0] == last_line[-1] and last_line[0] in "'\"":
            last_line = last_line[1:-1].strip()
        if not last_line.isdigit():
            raise ValueError(last_line)
        count = int(last_line)
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"Trino returned no row count for {table}: {result.stdout!r}") from exc
    if count != expected_count:
        raise RuntimeError(
            f"{table} row count {count} does not match expected {expected_count} "
            f"for partition {config.partition}"
        )


def emit_runtime_diagnostics(config: RunConfig) -> None:
    """Emit owned service logs before cleanup hides a post-start failure."""
    for service in RUNTIME_DIAGNOSTIC_SERVICES:
        try:
            run(
                compose_command(config, "logs", "--no-color", "--timestamps", service),
                cwd=config.project_dir,
            )
        except Exception as exc:
            print(
                f"release golden path diagnostics failed for {service}: {exc}",
                file=sys.stderr,
            )


def emit_missing_raw_diagnostics(config: RunConfig) -> None:
    """Report recent Dagster run IDs and statuses when raw-table verification fails."""
    query = """query RecentRuns {
  runsOrError(limit: 5) {
    __typename
    ... on Runs { results { runId status jobName } }
    ... on PythonError { message }
  }
}"""
    try:
        payload = graphql(
            service_url(config, "dagster", 3000, "/graphql"),
            query,
            {},
            service_token("phlo-api", wap_service_secret(config)),
        )
        run_data = (
            payload.get("data", {}).get("runsOrError", {})
            if isinstance(payload.get("data"), dict)
            else {}
        )
        print(f"release golden path Dagster runs: {json.dumps(run_data, sort_keys=True)}")
    except Exception as exc:
        print(f"release golden path Dagster run diagnostics failed: {exc}", file=sys.stderr)


def cleanup(
    config: RunConfig,
    *,
    owned_paths: set[Path],
    temporary_root: Path | None = None,
) -> list[Exception]:
    """Tear down the stack and delete owned paths, returning any failures."""
    errors: list[Exception] = []
    if config.compose_file.exists():
        try:
            run(
                compose_command(
                    config, "--profile", "api", "down", "--volumes", "--remove-orphans"
                ),
                cwd=config.project_dir,
            )
        except Exception as exc:
            errors.append(exc)
    paths = set(owned_paths)
    if temporary_root:
        paths.add(temporary_root)
    for path in sorted(paths, key=lambda candidate: len(candidate.parts), reverse=True):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except PermissionError:
            # Containers write root-owned files the host user cannot delete;
            # remove them through a throwaway container and retry as the host.
            try:
                run(
                    command(
                        "docker",
                        "run",
                        "--rm",
                        "--volume",
                        f"{path}:/cleanup",
                        "alpine:3.24.1",
                        "sh",
                        "-c",
                        "rm -rf /cleanup/* /cleanup/.[!.]* /cleanup/..?*",
                    ),
                    cwd=config.project_dir,
                )
                shutil.rmtree(path)
            except Exception as exc:
                errors.append(exc)
        except Exception as exc:
            errors.append(exc)
    return errors


# ---------------------------------------------------------------------------
# Candidate mode: the complete runtime journey is bound to one
# immutable candidate BOM, producing one canonical evidence bundle.
# ---------------------------------------------------------------------------


class CandidateError(RuntimeError):
    """The candidate journey cannot continue against the staged BOM."""


def bom_artifacts(bom: dict[str, object], kind: str) -> list[dict[str, object]]:
    """Return every BOM artifact of one kind."""
    return [dict(artifact) for artifact in bom["artifacts"] if artifact["kind"] == kind]  # type: ignore[arg-type]


def bom_release_version(bom: dict[str, object]) -> str:
    """Return the candidate's release version from the source artifact."""
    sources = bom_artifacts(bom, bom_module.KIND_SOURCE)
    if len(sources) != 1:
        raise CandidateError("BOM must carry exactly one source identity artifact")
    return str(sources[0]["version"])


def verify_candidate_bom(config: RunConfig) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Re-derive every BOM invariant and verify every staged distribution digest."""
    bom = config.bom
    assert bom is not None and config.staging_dir is not None
    bom_module.verify_staged_distributions(bom, config.staging_dir)
    distributions = bom_artifacts(bom, bom_module.KIND_SDIST) + bom_artifacts(
        bom, bom_module.KIND_WHEEL
    )
    first_party = bom_artifacts(bom, bom_module.KIND_FIRST_PARTY_IMAGE)
    providers = bom_artifacts(bom, bom_module.KIND_PROVIDER_IMAGE)
    result = {
        "canonical_candidate_digest": bom["canonical_candidate_digest"],
        "release_commit": bom["release_commit"],
        "distribution_count": len(distributions),
        "first_party_image_count": len(first_party),
        "provider_image_count": len(providers),
        "staging_dir": str(config.staging_dir),
        "source_checkout": False,
    }
    exercised = bom_artifacts(bom, bom_module.KIND_SUPPORT_MANIFEST) + bom_artifacts(
        bom, bom_module.KIND_SOURCE
    )
    return result, exercised


def write_hashed_requirements(config: RunConfig, path: Path) -> Path:
    """Pin every BOM wheel with its exact digest for hash-enforced installation."""
    bom = config.bom
    assert bom is not None
    lines = []
    for artifact in sorted(bom_artifacts(bom, bom_module.KIND_WHEEL), key=lambda a: str(a["name"])):
        lines.append(
            f"{artifact['name']}=={artifact['version']} --hash=sha256:{artifact['digest']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def install_operator_from_bom(
    config: RunConfig,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Install the operator venv from exact BOM bytes with installer-side hashes."""
    bom = config.bom
    assert bom is not None and config.staging_dir is not None
    distributions = config.staging_dir / "distributions"
    requirements = write_hashed_requirements(
        config, config.operator_env.parent / "candidate-requirements.txt"
    )
    run(command("uv", "venv", str(config.operator_env), "--python", "3.11"), cwd=config.repo_root)
    # Exact bytes first: the installer itself rejects any wheel whose content
    # does not hash to its BOM digest.
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.operator_python),
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(distributions),
            "-r",
            str(requirements),
        ),
        cwd=config.repo_root,
    )
    # Dependency closure from PyPI with first-party packages version-pinned;
    # the final force-local reinstall below restores exact BOM bytes.
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.operator_python),
            "--find-links",
            str(distributions),
            *[f"{name}=={bom_release_version(bom)}" for name in OPERATOR_PACKAGES],
        ),
        cwd=config.repo_root,
    )
    force_local_install(config, config.operator_python, *OPERATOR_PACKAGES)
    installed = verify_installed_versions(config)
    return (
        {
            "requirement_hashes_enforced": True,
            "installed_versions": installed,
            "no_source_import": True,
        },
        bom_artifacts(bom, bom_module.KIND_SDIST) + bom_artifacts(bom, bom_module.KIND_WHEEL),
    )


def verify_installed_versions(config: RunConfig) -> dict[str, str]:
    """Require every installed first-party distribution to match its BOM version."""
    bom = config.bom
    assert bom is not None
    result = run(
        command("uv", "pip", "freeze", "--python", str(config.operator_python)),
        cwd=config.repo_root,
        capture_output=True,
    )
    installed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, version = line.strip().partition("==")
        if separator:
            installed[name.lower().replace("_", "-")] = version
    mismatches = {}
    for artifact in bom_artifacts(bom, bom_module.KIND_WHEEL):
        name, version = str(artifact["name"]), str(artifact["version"])
        actual = installed.get(name)
        if actual != version:
            mismatches[name] = f"expected {version}, installed {actual!r}"
    if mismatches:
        raise CandidateError(f"installed artifacts do not match the BOM: {mismatches!r}")
    return {
        name: installed[name]
        for name in installed
        if name in {str(artifact["name"]) for artifact in bom_artifacts(bom, bom_module.KIND_WHEEL)}
    }


def install_project_dependencies_from_bom(config: RunConfig) -> None:
    """Install plugin dependencies into the generated project venv from the BOM."""
    bom = config.bom
    assert bom is not None
    run(command("uv", "venv", str(config.project_env), "--python", "3.11"), cwd=config.project_dir)
    run(
        command(
            "uv",
            "pip",
            "install",
            "--python",
            str(config.project_python),
            "--find-links",
            str(config.wheelhouse),
            *[f"{name}=={bom_release_version(bom)}" for name in PROJECT_PACKAGES],
        ),
        cwd=config.project_dir,
    )
    force_local_install(config, config.project_python, *PROJECT_PACKAGES)


def write_quality_gate_fixture(config: RunConfig) -> None:
    """Install the directive-driven WAP quality check used by candidate mode."""
    fixture = config.project_dir / "workflows" / "ingestion" / "csv" / "release_wap_check.py"
    fixture.write_text(
        f"""import json
from pathlib import Path

import dagster as dg


@dg.asset_check(asset="dlt_events")
def release_golden_path_wap_check(context) -> dg.AssetCheckResult:
    directive = {{}}
    try:
        directive = json.loads(Path("{QUALITY_DIRECTIVE_CONTAINER_PATH}").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    rejected = directive.get("reject_next_run") is True
    return dg.AssetCheckResult(
        passed=not rejected,
        metadata={{"reason": "intentional_quality_rejection" if rejected else "happy_path"}},
    )
""",
        encoding="utf-8",
    )


def write_operations_policy(config: RunConfig) -> None:
    """Grant the run's service principal the required operations actions."""
    authorization_dir = config.project_dir / ".phlo" / "authorization"
    roles_path = authorization_dir / "roles.yaml"
    if not roles_path.exists():
        roles_path.write_text(
            "version: 1\n"
            "roles:\n"
            "  operators:\n"
            "    description: Release golden-path operator principal\n"
            "subjects:\n"
            "  services:\n"
            f"    {OPERATOR_SERVICE_ACCOUNT}:\n"
            "      - operators\n",
            encoding="utf-8",
        )
    policies_path = authorization_dir / "policies.yaml"
    if not policies_path.exists():
        policies_path.write_text("policies: []\n", encoding="utf-8")
    with policies_path.open("a", encoding="utf-8") as stream:
        stream.write(
            "  - policy_id: release-golden-path-operations\n"
            "    effect: allow\n"
            "    principal:\n"
            "      roles:\n"
            "        - operators\n"
            "    action: operations.*\n"
            "    resource:\n"
            '      type: "*"\n'
            '      id_pattern: "*"\n'
        )


def compose_config_json(config: RunConfig) -> dict[str, object]:
    """Return the normalized Compose configuration as JSON."""
    result = run(
        compose_command(config, "--profile", "api", "config", "--format", "json"),
        cwd=config.project_dir,
        capture_output=True,
    )
    return dict(json.loads(result.stdout))


def pin_candidate_images(config: RunConfig) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Rewrite every generated image reference to its exact BOM digest."""
    bom = config.bom
    assert bom is not None
    first_party = {
        str(artifact["name"]): artifact
        for artifact in bom_artifacts(bom, bom_module.KIND_FIRST_PARTY_IMAGE)
    }
    providers = {
        (str(artifact["name"]), str(artifact["digest"])): artifact
        for artifact in bom_artifacts(bom, bom_module.KIND_PROVIDER_IMAGE)
    }
    compose = compose_config_json(config)
    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        raise CandidateError("generated Compose configuration has no services")
    replacements: dict[str, str] = {}
    pinned: list[dict[str, object]] = []
    for service_name, service in sorted(services.items()):
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if not isinstance(image, str) or not image:
            raise CandidateError(f"service {service_name!r} has no image reference")
        name, tag, digest = bom_module.parse_image_reference(image)
        if name.startswith(bom_module.FIRST_PARTY_IMAGE_PREFIX):
            entry = first_party.get(name)
            if entry is None:
                raise CandidateError(
                    f"first-party image {image!r} is not part of the candidate BOM"
                )
            if str(entry["version"]) != tag:
                raise CandidateError(
                    f"first-party image {image!r} does not match the BOM version "
                    f"{entry['version']!r}"
                )
            replacement = f"{name}@{entry['digest']}"
        else:
            if digest is None or (name, digest) not in providers:
                raise CandidateError(
                    f"image {image!r} is not pinned in the candidate BOM; "
                    "candidate mode never consumes a mutable tag"
                )
            replacement = image
        if image not in replacements:
            replacements[image] = replacement
        pinned.append(
            {
                "kind": entry["kind"]
                if name.startswith(bom_module.FIRST_PARTY_IMAGE_PREFIX)
                else bom_module.KIND_PROVIDER_IMAGE,
                "name": name,
                "digest": replacement.split("@", 1)[1] if "@" in replacement else str(digest),
                "service": service_name,
            }
        )
    compose_file = config.compose_file
    lines = compose_file.read_text(encoding="utf-8").splitlines(keepends=True)
    replaced = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("image:"):
            continue
        value = stripped[len("image:") :].strip().strip("'\"")
        if value in replacements:
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = f"{indent}image: {replacements[value]}\n"
            replaced += 1
    if replaced != len(replacements):
        raise CandidateError(
            f"compose image pinning replaced {replaced} of {len(replacements)} references"
        )
    compose_file.write_text("".join(lines), encoding="utf-8")
    normalized = compose_config_json(config)
    normalized_services = normalized.get("services", {})
    for service_name, service in sorted(normalized_services.items()):  # type: ignore[union-attr]
        if not isinstance(service, dict):
            continue
        image = str(service.get("image", ""))
        if "@sha256:" not in image:
            raise CandidateError(
                f"service {service_name!r} still references mutable image {image!r}"
            )
    return (
        {
            "digest_pinned_images": sorted(set(replacements.values())),
            "build_fallback": "disabled (--no-build; every reference is a digest)",
        },
        pinned,
    )


def start_stack_candidate(config: RunConfig) -> None:
    """Pull exact digests and start the stack without any build fallback."""
    try:
        run(compose_command(config, "--profile", "api", "pull"), cwd=config.project_dir)
        run(
            compose_command(config, "--profile", "api", "up", "--detach", "--no-build"),
            cwd=config.project_dir,
        )
    except subprocess.CalledProcessError:
        for parts in (("ps",), ("logs", "--no-color", "--timestamps")):
            try:
                run(compose_command(config, *parts), cwd=config.project_dir)
            except Exception as exc:
                print(f"release golden path diagnostics failed: {exc}", file=sys.stderr)
        raise


def production_preflight(config: RunConfig) -> dict[str, object]:
    """Require the production readiness report to pass."""
    result = subprocess.run(
        command(str(config.operator_bin), "services", "preflight", "--production", "--json"),
        cwd=config.project_dir,
        env=ops_environment(config, authorized=False),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CandidateError(
            f"preflight (exit {result.returncode}) returned no JSON report: "
            f"{result.stdout!r} {result.stderr!r}"
        ) from exc
    if (
        result.returncode != 0
        or not isinstance(envelope, dict)
        or envelope.get("schema_version") != 1
        or envelope.get("status") != "success"
        or envelope.get("exit_code") != 0
        or envelope.get("errors") != []
        or not isinstance(envelope.get("data"), dict)
        or not envelope["data"].get("passed")
    ):
        raise CandidateError(
            f"production preflight failed (exit {result.returncode}): {envelope!r} {result.stderr!r}"
        )
    report = envelope["data"]
    return {
        "environment": report.get("environment"),
        "checks": [
            {"id": check.get("id"), "state": str(check.get("state"))}
            for check in report.get("checks", [])
            if isinstance(check, dict)
        ],
    }


def parse_env_values(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE environment file."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def compose_service_port(config: RunConfig, service: str, container_port: int) -> str:
    """Resolve one published dynamic host port for a Compose service."""
    result = run(
        compose_command(config, "port", service, str(container_port)),
        cwd=config.project_dir,
        capture_output=True,
    )
    address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        return address.rsplit(":", 1)[1]
    except IndexError as exc:
        raise CandidateError(
            f"could not resolve {service} port {container_port}: {address!r}"
        ) from exc


def ops_environment(config: RunConfig, *, authorized: bool = False) -> dict[str, str]:
    """Build the operator environment for guarded operations against the stack."""
    environment = dict(os.environ)
    environment.pop("PHLO_SERVICE_ACCOUNT", None)
    environment.pop("PHLO_AUTH_SUBJECT", None)
    if authorized:
        environment["PHLO_SERVICE_ACCOUNT"] = OPERATOR_SERVICE_ACCOUNT
    for service, container_port, variable in COMPOSE_PORT_PROBES:
        environment[variable] = compose_service_port(config, service, container_port)
    values = parse_env_values(config.project_dir / ".phlo" / ".env.local")
    for key in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ):
        if key in values:
            environment[key] = values[key]
    environment.setdefault("POSTGRES_USER", "phlo")
    environment.setdefault("POSTGRES_DB", "phlo")
    minio_user = environment.get("MINIO_ROOT_USER", "minio")
    minio_password = environment.get("MINIO_ROOT_PASSWORD", "")
    if minio_password:
        environment["ICEBERG_S3_ACCESS_KEY"] = minio_user
        environment["ICEBERG_S3_SECRET_KEY"] = minio_password
    environment["PHLO_OPERATIONS_JOURNAL_DIR"] = str(
        config.project_dir / ".phlo" / "operations-journal"
    )
    return environment


def run_operations(
    config: RunConfig,
    *args: str,
    environment: dict[str, str],
) -> dict[str, object]:
    """Run one operations CLI command and parse its JSON envelope."""
    result = run(
        command(str(config.operator_bin), "operations", *args),
        cwd=config.project_dir,
        env=environment,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CandidateError(
            f"operations {' '.join(args[:2])} returned no JSON envelope: "
            f"{result.stdout!r} {result.stderr!r}"
        ) from exc
    if isinstance(payload, dict) and payload.get("accepted") is False:
        raise CandidateError(f"operations {' '.join(args[:2])} was not accepted: {payload!r}")
    return dict(payload)


def negative_security(config: RunConfig) -> dict[str, object]:
    """Require an unauthenticated operations mutation to be refused."""
    environment = ops_environment(config, authorized=False)
    result = subprocess.run(
        command(
            str(config.operator_bin),
            "operations",
            "maintenance",
            "apply",
            "--plan",
            str(config.project_dir / ".phlo" / "no-such-plan.json"),
            "--confirmation-token",
            "not-a-token",
        ),
        cwd=config.project_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        raise CandidateError("unauthorized operations mutation was allowed")
    if "Authorization denied" not in result.stderr + result.stdout:
        raise CandidateError(
            f"unauthorized mutation failed for the wrong reason: "
            f"{result.stdout!r} {result.stderr!r}"
        )
    return {
        "denied_command": "operations.maintenance.apply",
        "denial": "authorization_denied",
        "exit_code": result.returncode,
    }


def run_plan_first_maintenance(config: RunConfig) -> dict[str, object]:
    """Prove inventory, mutation-free planning, and a token-bound maintenance apply."""
    environment = ops_environment(config, authorized=True)
    inventory = run_operations(
        config, "maintenance", "inventory", "--format", "json", environment=environment
    )
    tables = inventory.get("tables")
    if not isinstance(tables, list) or not tables:
        raise CandidateError("maintenance inventory is empty")
    plan = run_operations(
        config,
        "maintenance",
        "plan",
        "--operation",
        "snapshot_expiry",
        "--table",
        "raw.events",
        "--ref",
        "main",
        "--format",
        "json",
        environment=environment,
    )
    plan_path = config.project_dir / ".phlo" / "maintenance-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    applied = run_operations(
        config,
        "maintenance",
        "apply",
        "--plan",
        str(plan_path),
        "--confirmation-token",
        str(plan.get("plan_token", "")),
        "--format",
        "json",
        environment=environment,
    )
    return {"inventory_tables": len(tables), "operation": "snapshot_expiry", "apply": applied}


def create_backup_set(config: RunConfig) -> tuple[dict[str, object], Path]:
    """Create one immutable, verified backup set of the candidate deployment."""
    environment = ops_environment(config, authorized=True)
    target = config.project_dir.parent / "backup-set"
    payload = run_operations(
        config,
        "backup",
        "create",
        "--target",
        str(target),
        "--format",
        "json",
        environment=environment,
    )
    set_id = payload.get("set_id")
    if not isinstance(set_id, str) or not set_id:
        raise CandidateError(f"backup create returned no set id: {payload!r}")
    set_dir = Path(str(payload.get("target", target))) / set_id
    return payload, set_dir


def verify_backup_set(config: RunConfig, set_dir: Path) -> dict[str, object]:
    """Independently verify the backup set (read-only)."""
    payload = run_operations(
        config,
        "backup",
        "verify",
        "--backup-set",
        str(set_dir),
        "--format",
        "json",
        environment=ops_environment(config, authorized=True),
    )
    if payload.get("accepted") is not True:
        raise CandidateError(f"backup verification rejected the set: {payload!r}")
    return payload


def restore_to_explicit_target(
    config: RunConfig, set_dir: Path, target_dir: Path
) -> dict[str, object]:
    """Plan and apply a restore bound to one explicit, new target directory."""
    environment = ops_environment(config, authorized=True)
    plan = run_operations(
        config,
        "restore",
        "plan",
        "--backup-set",
        str(set_dir),
        "--target",
        str(target_dir),
        "--format",
        "json",
        environment=environment,
    )
    plan_path = target_dir.parent / f"restore-plan-{target_dir.name}.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    return run_operations(
        config,
        "restore",
        "apply",
        "--plan",
        str(plan_path),
        "--confirmation-token",
        str(plan.get("plan_token", "")),
        "--fixture-substrate",
        "--format",
        "json",
        environment=environment,
    )


def run_supported_upgrade(config: RunConfig, set_dir: Path, target_dir: Path) -> dict[str, object]:
    """Prove the supported version pair upgrade on a verified backup."""
    environment = ops_environment(config, authorized=True)
    plan = run_operations(
        config,
        "upgrade",
        "plan",
        "--from",
        UPGRADE_FROM_VERSION,
        "--to",
        UPGRADE_TO_VERSION,
        "--backup-set",
        str(set_dir),
        "--target",
        str(target_dir),
        "--format",
        "json",
        environment=environment,
    )
    plan_path = target_dir.parent / f"upgrade-plan-{target_dir.name}.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    return run_operations(
        config,
        "upgrade",
        "apply",
        "--plan",
        str(plan_path),
        "--confirmation-token",
        str(plan.get("plan_token", "")),
        "--fixture-substrate",
        "--format",
        "json",
        environment=environment,
    )


def verify_support_boundary(config: RunConfig) -> dict[str, object]:
    """Run the committed support validator against the release commit tree."""
    bom = config.bom
    assert bom is not None and config.staging_dir is not None
    release_ref = str(bom.get("release_ref") or bom.get("release_commit"))
    tree_dir = config.staging_dir.parent / f"release-tree-{str(bom['release_commit'])[:12]}"
    archive_path = tree_dir.with_suffix(".tar")
    if not tree_dir.exists():
        run(
            command("git", "archive", "--format=tar", "-o", str(archive_path), release_ref),
            cwd=config.repo_root,
        )
        tree_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path) as archive:
            archive.extractall(tree_dir, filter="data")  # noqa: S202
    result = run(
        command(sys.executable, str(tree_dir / "scripts" / "validate_support_manifest.py")),
        cwd=tree_dir,
        capture_output=True,
    )
    return {
        "validator": "scripts/validate_support_manifest.py",
        "release_ref": release_ref,
        "exit_code": result.returncode,
    }


class EvidenceRecorder:
    """Record one structured result per journey step into the evidence bundle."""

    def __init__(self, bundle: dict[str, object]) -> None:
        self.bundle = bundle

    def step(
        self,
        demonstration_id: str,
        title: str,
        action,
    ) -> None:
        """Run one journey step, recording its structured result or failure."""
        started = release_evidence.utc_now()
        try:
            result, artifacts = action()
        except Exception as exc:
            release_evidence.record_demonstration(
                self.bundle,
                demonstration_id=demonstration_id,
                title=title,
                status=release_evidence.STATUS_FAILED,
                result={},
                error=f"{type(exc).__name__}: {exc}",
                started_utc=started,
            )
            raise
        release_evidence.record_demonstration(
            self.bundle,
            demonstration_id=demonstration_id,
            title=title,
            status=release_evidence.STATUS_PASSED,
            result=result,
            artifacts=artifacts,
            started_utc=started,
        )


def main_candidate(args: argparse.Namespace) -> int:
    """Run the artifact-bound candidate journey and emit its evidence bundle."""
    repo_root = args.repo_root.resolve()
    bom_path = args.candidate_bom.resolve()
    evidence_path = args.evidence_output.resolve()
    staging_dir = bom_path.parent
    temporary_root = Path(tempfile.mkdtemp(prefix=".phlo-release-candidate-", dir=repo_root))
    project_dir = temporary_root / f"csv-batch-{os.getpid()}"
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    config = RunConfig(
        repo_root=repo_root,
        project_dir=project_dir,
        wheelhouse=staging_dir / "distributions",
        operator_env=temporary_root / "operator-env",
        project_name=project_name(),
        partition=args.partition,
        staging_dir=staging_dir,
    )
    bundle = release_evidence.new_bundle(
        release_commit="pending",
        canonical_candidate_digest="pending",
        artifact_count=0,
        environment={
            "runner": "scripts/release_golden_path.py --candidate-bom",
            "host": platform_module.node(),
            "platform": f"{platform_module.system()} {platform_module.machine()}",
            "python": sys.version.split()[0],
            "promoting": False,
        },
    )
    recorder = EvidenceRecorder(bundle)
    primary_error: Exception | None = None
    cleanup_errors: list[Exception] = []
    stack_started = False
    journey: dict[str, object] = {}

    def bind_candidate() -> tuple[dict[str, object], list[dict[str, object]]]:
        bom = bom_module.load_bom(bom_path)
        bundle["candidate"] = {
            "release_commit": bom["release_commit"],
            "canonical_candidate_digest": bom["canonical_candidate_digest"],
            "artifact_count": len(bom["artifacts"]),
        }
        bundle["checksum"] = {
            "algorithm": "sha256",
            "value": release_evidence.bundle_checksum(bundle),
        }
        config.__dict__.update(bom=bom)
        return verify_candidate_bom(config)

    def scaffold_project() -> tuple[dict[str, object], list[dict[str, object]]]:
        create_project(config)
        write_transform_fixture(config)
        write_quality_gate_fixture(config)
        align_project_name(config)
        install_project_dependencies_from_bom(config)
        configure_non_dev_compose(config)
        write_report_policy_fixture(config)
        write_operations_policy(config)
        return {}, []

    def start_candidate_stack() -> tuple[dict[str, object], list[dict[str, object]]]:
        nonlocal stack_started
        pinned, pinned_artifacts = pin_candidate_images(config)
        start_stack_candidate(config)
        stack_started = True
        return pinned, pinned_artifacts

    def promote_wap() -> tuple[dict[str, object], list[dict[str, object]]]:
        wap_run = materialize_wap(config)
        wait_for_wap_promotion(config, wap_run)
        journey["promoted_wap_run"] = wap_run
        return {
            "logical_run_id": wap_run.logical_run_id,
            "dagster_run_id": wap_run.dagster_run_id,
            "promoted": True,
        }, []

    def reject_wap() -> tuple[dict[str, object], list[dict[str, object]]]:
        directive = config.project_dir / ".phlo" / "quality_gate.json"
        directive.parent.mkdir(parents=True, exist_ok=True)
        directive.write_text('{"reject_next_run": true}\n', encoding="utf-8")
        try:
            wap_run = materialize_wap(config)
            verify_rejected_wap_report(config, wap_run)
        finally:
            directive.unlink(missing_ok=True)
        return {
            "logical_run_id": wap_run.logical_run_id,
            "dagster_run_id": wap_run.dagster_run_id,
            "merge_outcome": "rejected_quality",
            "promoted": False,
        }, []

    def prove_run_report() -> tuple[dict[str, object], list[dict[str, object]]]:
        wap_run = journey["promoted_wap_run"]
        assert isinstance(wap_run, WapRun)
        fetch_run_report(config, wap_run, config.report_token)
        return {"logical_run_id": wap_run.logical_run_id, "scope_mismatch_denied": True}, []

    def create_backup() -> tuple[dict[str, object], list[dict[str, object]]]:
        payload, set_dir = create_backup_set(config)
        journey["backup_set_dir"] = set_dir
        return payload, []

    def backup_set_dir() -> Path:
        set_dir = journey.get("backup_set_dir")
        if not isinstance(set_dir, Path):
            raise CandidateError("backup set was not created by an earlier demonstration")
        return set_dir

    try:
        recorder.step("candidate_bom_verification", "Candidate BOM verification", bind_candidate)
        recorder.step(
            "operator_installation",
            "Exact BOM artifact installation",
            lambda: install_operator_from_bom(config),
        )
        recorder.step(
            "project_scaffold", "Project scaffold from installed artifacts", scaffold_project
        )
        recorder.step(
            "stack_start", "Exact image digest stack start without build", start_candidate_stack
        )
        recorder.step(
            "production_preflight",
            "Production readiness preflight",
            lambda: (production_preflight(config), []),
        )
        recorder.step(
            "negative_security",
            "Negative security enforcement",
            lambda: (negative_security(config), []),
        )

        def materialize() -> tuple[dict[str, object], list[dict[str, object]]]:
            materialize_partition(config)
            return {"partition": config.partition, "asset": "dlt_events"}, []

        recorder.step("ingestion_materialization", "Ingestion materialization", materialize)

        def storage() -> tuple[dict[str, object], list[dict[str, object]]]:
            verify_minio_storage(config)
            return {"probe": "minio-ready-and-owned-write"}, []

        recorder.step("storage_probe", "Object storage readiness and owned write", storage)
        recorder.step(
            "row_query_initial",
            "Initial row query",
            lambda: (_verify_rows_result(config, "raw.events"), []),
        )

        def transform() -> tuple[dict[str, object], list[dict[str, object]]]:
            materialize_transform(config)
            return {"partition": config.partition, "asset": "events_mart"}, []

        recorder.step("transformation_materialization", "Transformation materialization", transform)
        recorder.step(
            "row_query_transform",
            "Transformed row query",
            lambda: (_verify_rows_result(config, "raw_marts.events_mart"), []),
        )

        def wap_config() -> tuple[dict[str, object], list[dict[str, object]]]:
            configure_wap(config)
            return {"wap": "enabled", "job": "__ASSET_JOB"}, []

        recorder.step("wap_configuration", "WAP configuration", wap_config)
        recorder.step("wap_promotion", "WAP materialization and promotion", promote_wap)
        recorder.step("wap_rejection", "WAP quality rejection", reject_wap)
        recorder.step("run_report", "Run report and scoped denial", prove_run_report)
        recorder.step(
            "plan_first_maintenance",
            "Plan-first table maintenance",
            lambda: (run_plan_first_maintenance(config), []),
        )
        recorder.step("backup_creation", "Verified backup set creation", create_backup)
        recorder.step(
            "backup_verification",
            "Independent backup verification",
            lambda: (verify_backup_set(config, backup_set_dir()), []),
        )
        recorder.step(
            "restore_explicit_target",
            "Restore to explicit target",
            lambda: (
                restore_to_explicit_target(
                    config, backup_set_dir(), config.project_dir.parent / "restore-target"
                ),
                [],
            ),
        )
        recorder.step(
            "supported_upgrade",
            "Supported pair upgrade",
            lambda: (
                run_supported_upgrade(
                    config, backup_set_dir(), config.project_dir.parent / "upgrade-target"
                ),
                [],
            ),
        )
        recorder.step(
            "upgrade_recovery",
            "Upgrade recovery reconciliation",
            lambda: (
                restore_to_explicit_target(
                    config, backup_set_dir(), config.project_dir.parent / "recovery-target"
                ),
                [],
            ),
        )
        recorder.step(
            "row_query_final",
            "Final row query",
            lambda: (
                {
                    "raw_events": _verify_rows_result(config, "raw.events"),
                    "events_mart": _verify_rows_result(config, "raw_marts.events_mart"),
                },
                [],
            ),
        )
        recorder.step(
            "support_boundary_consistency",
            "Support-boundary consistency",
            lambda: (verify_support_boundary(config), []),
        )
    except Exception as exc:
        primary_error = exc
    finally:
        release_evidence.finalize_bundle(bundle)
        try:
            release_evidence.write_bundle(bundle, evidence_path)
        except Exception as exc:
            print(f"release golden path could not write evidence: {exc}", file=sys.stderr)
        if not args.keep_project:
            cleanup_errors = cleanup(
                config,
                owned_paths={project_dir, config.operator_env},
                temporary_root=temporary_root,
            )
        else:
            print(f"kept project at {project_dir}")

    if primary_error:
        print(f"release candidate golden path failed: {primary_error}", file=sys.stderr)
        if stack_started and config.compose_file.exists():
            emit_runtime_diagnostics(config)
    for error in cleanup_errors:
        print(f"release golden path cleanup failed: {error}", file=sys.stderr)
    if primary_error or cleanup_errors:
        return 1
    print(
        f"release candidate golden path passed: candidate "
        f"{bundle['candidate']['canonical_candidate_digest']}, "
        f"evidence at {evidence_path}"
    )
    return 0


def _verify_rows_result(config: RunConfig, table: str) -> dict[str, object]:
    verify_rows(config, table=table, expected_count=FIXTURE_ROW_COUNT)
    return {"table": table, "expected_count": FIXTURE_ROW_COUNT}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse release golden path CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--partition", default=PARTITION)
    parser.add_argument(
        "--candidate-bom",
        type=Path,
        help=(
            "Run the artifact-bound candidate mode against one staged immutable "
            "candidate BOM (the staging directory next to it must hold distributions/)."
        ),
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        help="Path for the canonical evidence bundle (required with --candidate-bom).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the full release golden path and return its exit code."""
    args = parse_args(argv)
    if args.candidate_bom or args.evidence_output:
        if not (args.candidate_bom and args.evidence_output):
            print(
                "candidate mode requires both --candidate-bom and --evidence-output",
                file=sys.stderr,
            )
            return 2
        return main_candidate(args)
    return main_source(args)


def main_source(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    temporary_root: Path | None = None
    if args.project_dir:
        project_dir = args.project_dir.resolve()
        wheelhouse = project_dir.parent / "wheelhouse"
        operator_env = project_dir.parent / "operator-env"
        existing_paths = [path for path in (project_dir, wheelhouse, operator_env) if path.exists()]
        if existing_paths:
            print(
                "refusing to use existing project artifacts: "
                + ", ".join(str(path) for path in existing_paths),
                file=sys.stderr,
            )
            return 2
    else:
        temporary_root = Path(tempfile.mkdtemp(prefix=".phlo-release-golden-path-", dir=repo_root))
        project_dir = temporary_root / f"csv-batch-{os.getpid()}"
        wheelhouse = project_dir.parent / "wheelhouse"
        operator_env = project_dir.parent / "operator-env"
    owned_paths = {path for path in (project_dir, wheelhouse, operator_env) if not path.exists()}
    config = RunConfig(
        repo_root=repo_root,
        project_dir=project_dir,
        wheelhouse=wheelhouse,
        operator_env=operator_env,
        project_name=project_name(),
        partition=args.partition,
    )
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    primary_error: Exception | None = None
    cleanup_errors: list[Exception] = []
    stack_started = False
    try:
        build_wheelhouse(config)
        install_operator(config)
        create_project(config)
        write_transform_fixture(config)
        align_project_name(config)
        install_project_dependencies(config)
        configure_non_dev_compose(config)
        write_report_policy_fixture(config)
        start_stack(config)
        stack_started = True
        materialize_partition(config)
        verify_minio_storage(config)
        verify_rows(config, expected_count=FIXTURE_ROW_COUNT)
        materialize_transform(config)
        verify_rows(config, table="raw_marts.events_mart", expected_count=FIXTURE_ROW_COUNT)
        configure_wap(config)
        promoted_wap_run = materialize_wap(config)
        wait_for_wap_promotion(config, promoted_wap_run)
    except Exception as exc:
        primary_error = exc
    finally:
        if primary_error and stack_started:
            if "raw.events" in str(primary_error):
                emit_missing_raw_diagnostics(config)
            emit_runtime_diagnostics(config)
        if not args.keep_project:
            cleanup_errors = cleanup(
                config,
                owned_paths=owned_paths,
                temporary_root=temporary_root,
            )
        elif temporary_root:
            print(f"kept project at {project_dir}")

    if primary_error:
        print(f"release golden path failed: {primary_error}", file=sys.stderr)
    for error in cleanup_errors:
        print(f"release golden path cleanup failed: {error}", file=sys.stderr)
    if primary_error or cleanup_errors:
        return 1
    print("release golden path passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
