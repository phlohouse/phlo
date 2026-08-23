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
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True)
class RunConfig:
    """Hold paths and names for one release golden path run."""

    repo_root: Path
    project_dir: Path
    wheelhouse: Path
    operator_env: Path
    project_name: str
    partition: str = PARTITION
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse release golden path CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--keep-project", action="store_true")
    parser.add_argument("--partition", default=PARTITION)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the full release golden path and return its exit code."""
    args = parse_args(argv)
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
