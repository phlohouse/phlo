"""Disposable full-stack fixture for the Observatory acceptance suite.

Boots the committed ``examples/lakehouses/wap-failure-lab`` project against a
real bundled stack: Postgres, MinIO, Nessie, Trino, Dagster (+daemon) and the
packaged ``observatory`` image run in Docker, while ``phlo-api`` runs as a
native dev subprocess from the project's editable-installed venv — the API
must exec project workflow files (dagster/dlt/pandera imports) during
capability discovery, which the slim Alpine image cannot provide. The
containerized Observatory reaches that native API through
``host.docker.internal``. The browser exercises the installed app through its
same-origin API proxy; provider state is asserted independently (Dagster
GraphQL/metadata DB, Nessie, Trino, durable settings).

Authentication is the real static provider: ``phlo.yaml`` selects it, user
records arrive through the project's secrets env file, and RBAC
roles/policies live in ``.phlo/authorization/`` — the same files an operator
deployment would carry.

Opt-in gate: ``PHLO_RUN_OBSERVATORY_ACCEPTANCE=1``. Preserve the stack for
debugging with ``PHLO_KEEP_BUNDLED_STACK=1`` (shared with the contract suite).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phlo_testing.harness_utils import (
    apply_env_updates,
    force_remove_directory,
    read_env_file,
    resolve_port,
    run_phlo,
    setup_project_venv,
    upsert_env_file,
    wait_for_http,
)
from phlo_testing.profile_harness import (
    BUNDLED_STACK_DEV_PACKAGES,
    BundledStackPorts,
    _repo_root,
    _verify_bind_mount_parent,
    build_bundled_stack_env_updates,
    keep_bundled_stack_running,
)

from phlo.config.layout import env_defaults_path, env_secrets_path, project_env_paths

FIXTURE_PATH = _repo_root() / "examples" / "lakehouses" / "wap-failure-lab"

ACCEPTANCE_SERVICES = (
    "postgres",
    "minio",
    "minio-setup",
    "nessie",
    "trino",
    "dagster",
    "dagster-daemon",
)
# Docker data-plane services. The packaged Observatory starts through a direct
# ``docker compose up --no-deps`` (services start would drag the API container
# in through depends_on expansion), and phlo-api runs natively — its
# dev.command subprocess uses the project venv, so the API serves this
# repository's current source and can exec project workflows whose imports
# (dagster, dlt, pandera) have no musl-compatible install path.

ACCEPTANCE_NATIVE_SERVICES = ("phlo-api",)

ACCEPTANCE_DEV_PACKAGES = (*BUNDLED_STACK_DEV_PACKAGES, "phlo-pandera")
# Workspace packages editable-installed into dev-mode service containers. The
# bundled list plus phlo-pandera, which the lab's strict contract needs but the
# generic bundled stack does not install.

ACCEPTANCE_VENV_PACKAGES = ("phlo-iceberg", "phlo-pandera")
# Workspace packages the native API needs beyond setup_project_venv's core
# set: phlo-iceberg for catalog/table-store capability discovery and
# phlo-pandera (plus its pandas/pandera deps) for workflow imports.

COMPOSE_API_OVERRIDE = """\
# Written by the acceptance bootstrap: the packaged Observatory proxies to a
# natively-run phlo-api, so it must reach the host rather than a compose
# service, and must not pull the (unused) phlo-api container up as a
# dependency. host.docker.internal resolves on Docker Desktop; the extra_hosts
# entry keeps it working on Linux too.
services:
  observatory:
    depends_on: !reset []
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      PHLO_API_URL: http://host.docker.internal:${PHLO_API_PORT:-4000}
"""

OPERATOR_TOKEN = "acc-operator-7f4c2e19a8b3d6f0"
VIEWER_TOKEN = "acc-viewer-31e9b0c5a7d24f68"
UNKNOWN_TOKEN = "acc-unknown-0000000000000000"
# Static bearer credentials configured into the disposable stack. They are
# fixture secrets for a throwaway project, never real credentials.

OPERATOR_SUBJECT = "acceptance-operator"
VIEWER_SUBJECT = "acceptance-viewer"

ROLES_YAML = """\
version: 1
roles:
  operator:
    inherits: []
    description: Full Observatory operator surface
  viewer:
    inherits: []
    description: Read-only observability surface
subjects:
  users:
    acceptance-operator: [operator]
    acceptance-viewer: [viewer]
"""

POLICIES_YAML = """\
version: 1
policies:
  - policy_id: operator_full
    effect: allow
    principal:
      roles: [operator]
    action: "*"
    resource:
      type: "*"
      id_pattern: "*"
  - policy_id: viewer_read_only
    effect: allow
    principal:
      roles: [viewer]
    action: "*.read"
    resource:
      type: "*"
      id_pattern: "*"
"""


def acceptance_enabled() -> bool:
    """Return True when the opt-in acceptance gate is set."""
    value = os.environ.get("PHLO_RUN_OBSERVATORY_ACCEPTANCE", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def acceptance_artifact_root() -> Path:
    """Artifact root for acceptance evidence (env-overridable)."""
    configured = os.environ.get("PHLO_ACCEPTANCE_ARTIFACT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return _repo_root() / ".tmp" / "observatory-acceptance"


def _fixture_git_sha() -> str:
    """Return the git SHA of the fixture path (or working-tree state)."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "examples/lakehouses/wap-failure-lab"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    sha = result.stdout.strip()
    return sha or "unknown"


def _copy_fixture(project_dir: Path) -> None:
    """Copy the committed failure-lab fixture into the disposable project dir."""
    ignore = shutil.ignore_patterns(
        ".venv",
        ".phlo",
        "generated-data",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        "*.egg-info",
    )
    shutil.copytree(FIXTURE_PATH, project_dir, ignore=ignore)


def _rewrite_fixture_pyproject(project_dir: Path) -> None:
    """Remove git-pinned phlo dependencies from the copied project.

    The committed example pins phlo packages to a git SHA for standalone
    checkouts. Inside the dev-mode stack the repository is editable-installed
    (and PYTHONPATH-shadowed) into every service container, so the copied
    project declares only its third-party dependencies; a git pin would make
    the container install resolve a different phlo over the mounted source.
    The ``[tool.uv] override-dependencies`` block that re-pins phlo is emptied
    for the same reason.
    """
    pyproject = project_dir / "pyproject.toml"
    lines = pyproject.read_text().splitlines()
    kept: list[str] = []
    in_override_block = False
    for line in lines:
        stripped = line.strip()
        if in_override_block:
            if stripped in {"]", "],"}:
                in_override_block = False
            continue
        if stripped.startswith("override-dependencies"):
            kept.append("override-dependencies = []")
            if not stripped.endswith("]"):
                in_override_block = True
            continue
        if stripped.startswith(('"phlo', "phlo")):
            # Any phlo/phlo-* dependency entry, pinned or bare.
            continue
        kept.append(line)
    pyproject.write_text("\n".join(kept) + "\n")


def _unique_project_name() -> str:
    return f"phlo-accept-{uuid.uuid4().hex[:8]}"


def _rewrite_fixture_phlo_yaml(project_dir: Path, project_name: str) -> None:
    """Give the disposable copy a unique compose/project identity and auth config."""
    phlo_yaml = project_dir / "phlo.yaml"
    text = phlo_yaml.read_text()
    text = text.replace("name: wap-failure-lab", f"name: {project_name}", 1)
    text = text.replace("PHLO_PROJECT: wap-failure-lab", f"PHLO_PROJECT: {project_name}")
    text += (
        "\n"
        "# Acceptance-suite auth: real static provider + required authorization.\n"
        "authentication:\n"
        "  provider: static\n"
        "  static:\n"
        "    enabled: true\n"
        "    dev_mode: false\n"
        "api:\n"
        "  authorization:\n"
        "    backend: default\n"
        "    mode: required\n"
    )
    phlo_yaml.write_text(text)


def _write_authorization_files(project_dir: Path) -> None:
    """Write the RBAC roles/policies the required-mode backend enforces."""
    auth_dir = project_dir / ".phlo" / "authorization"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "roles.yaml").write_text(ROLES_YAML)
    (auth_dir / "policies.yaml").write_text(POLICIES_YAML)


def _write_auth_secrets(project_dir: Path) -> None:
    """Configure static bearer users through the project's secrets env file."""
    secrets_file = env_secrets_path(project_dir / ".phlo")
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    users = {
        OPERATOR_TOKEN: {
            "subject": OPERATOR_SUBJECT,
            "principal_type": "user",
            "groups": ["operators"],
            "claims": {"scopes": ["admin"]},
        },
        VIEWER_TOKEN: {
            "subject": VIEWER_SUBJECT,
            "principal_type": "user",
            "groups": ["viewers"],
            "claims": {"scopes": ["lakehouse:read"]},
        },
    }
    upsert_env_file(
        secrets_file,
        {
            "PHLO_AUTH_STATIC_ENABLED": "true",
            "PHLO_AUTH_STATIC_USERS": json.dumps(users),
        },
    )


def _write_service_credentials(project_dir: Path) -> None:
    """Provision the phlo-api → phlo-dagster workload credential ring.

    Under ``requires_http_authorization()`` the API must mint a scoped phlo1
    workload token before any Dagster control-plane call, and Dagster's
    authorization middleware must verify it — both sides read the same key
    ring from ``PHLO_SERVICE_CREDENTIALS_FILE``. The file lives under the
    project's ``.phlo/secrets`` so the Dagster container sees it through its
    ``/app`` mount while the native API reads the host path; the two env
    values differ (set separately), which is why the file is shared rather
    than the setting.
    """
    secrets_dir = project_dir / ".phlo" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    credentials_file = secrets_dir / "workload-credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "phlo-api": {
                    "phlo-dagster": {
                        "scp": ["dagster:control"],
                        "keys": {
                            "acceptance-k1": {
                                "secret": secrets.token_hex(32),
                                "state": "active",
                                "activated_at": 0,
                            }
                        },
                    }
                }
            }
        )
        + "\n"
    )
    credentials_file.chmod(0o600)
    # The env-file value is the container path: Dagster mounts the project at
    # /app. The native API overrides it with the host path in
    # _start_native_services.
    upsert_env_file(
        env_secrets_path(project_dir / ".phlo"),
        {"PHLO_SERVICE_CREDENTIALS_FILE": "/app/.phlo/secrets/workload-credentials.json"},
    )


def _write_compose_override(project_dir: Path) -> None:
    """Point the packaged Observatory at the natively-run phlo-api.

    The layer is a generated part of the disposable topology and must stay on
    an auto-loaded override path so every ``phlo services`` invocation applies
    it — the API action surface strips its own ``PHLO_ENVIRONMENT`` when it
    shells out, so the production override guard never fires here.
    """
    overrides_dir = project_dir / ".phlo" / "overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    (overrides_dir / "compose.yaml").write_text(COMPOSE_API_OVERRIDE)


def _start_native_services(project_dir: Path) -> None:
    """Spawn native dev services with the acceptance security env overlay.

    ``phlo services start --native`` cannot carry PHLO_ENVIRONMENT=staging: the
    CLI validates compose override layers against process env first, and a
    production environment value rejects every override. Spawning through
    NativeProcessManager directly keeps the gate inside the API process —
    where requires_http_authorization() must see it — while recording the same
    native-process state file so ``services stop --native`` still manages it.
    """
    import asyncio
    import threading

    project_dir = project_dir.resolve()
    # A relative project_dir yields a relative PATH prepend for the venv, which
    # resolves against the child's cwd and silently misses — resolve up front.

    from phlo.cli.commands.services.start import _load_native_env_overrides
    from phlo.cli.commands.services.utils import _load_native_state, _save_native_state
    from phlo.plugins.compose.native import NativeProcessManager
    from phlo.plugins.discovery import ServiceDiscovery

    discovery = ServiceDiscovery()
    available = discovery.discover()
    manager = NativeProcessManager(project_dir, log_dir=project_dir / ".phlo" / "native-logs")
    env_overrides = {
        **_load_native_env_overrides(project_dir),
        "PHLO_ENVIRONMENT": "staging",
        "PHLO_PROJECT_PATH": str(project_dir),
        "ENV_FILE_PATH": str(env_defaults_path(project_dir / ".phlo")),
        # Host-side path — the env-file value is the container /app path.
        "PHLO_SERVICE_CREDENTIALS_FILE": str(
            project_dir / ".phlo" / "secrets" / "workload-credentials.json"
        ),
    }
    state = _load_native_state(project_dir)

    async def _start_all() -> None:
        for service_name in ACCEPTANCE_NATIVE_SERVICES:
            service = available.get(service_name)
            if service is None or not manager.can_run_dev(service):
                raise RuntimeError(f"{service_name} has no native dev command")
            process = await manager.start_service(service, env_overrides=env_overrides)
            if process is None or not process.is_running:
                raise RuntimeError(f"native {service_name} failed to start")
            state[service_name] = {
                "pid": process.pid,
                "started_at": time.time(),
                "log": str(project_dir / ".phlo" / "native-logs" / f"{service_name}.log"),
            }
            _save_native_state(project_dir, state)

    # Playwright's sync API keeps an event loop running on this thread, so a
    # bare asyncio.run raises "cannot be called from a running event loop".
    # A worker thread has no running loop and can own the async start.
    failure: list[BaseException] = []

    def _runner() -> None:
        try:
            asyncio.run(_start_all())
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            failure.append(exc)

    worker = threading.Thread(target=_runner, daemon=True, name="native-start")
    worker.start()
    worker.join(timeout=180)
    if worker.is_alive():
        raise TimeoutError("native service start did not finish within 180s")
    if failure:
        raise RuntimeError(f"native service start failed: {failure[0]}") from failure[0]


def _extend_project_venv(project_dir: Path, phlo_source: Path, python_executable: Path) -> None:
    """Install the lab's remaining runtime needs into the project venv.

    ``setup_project_venv`` covers the core service packages; the native API
    additionally needs phlo-iceberg/phlo-pandera (capability discovery and
    workflow imports) and the project itself for its third-party deps
    (dlt/pandas/pyarrow). The copied pyproject no longer pins phlo to git, so
    this resolves only PyPI wheels.
    """
    args = ["uv", "pip", "install", "--python", str(python_executable)]
    for package in ACCEPTANCE_VENV_PACKAGES:
        package_dir = phlo_source / "packages" / package
        if package_dir.is_dir():
            args += ["-e", str(package_dir)]
    args += ["-e", str(project_dir)]
    result = subprocess.run(
        args, cwd=project_dir, capture_output=True, text=True, timeout=600, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"acceptance venv extension failed: {result.stdout}\n{result.stderr}")


def _compose_base_args(project_dir: Path, project_name: str) -> list[str]:
    """Compose invocation prefix replaying the CLI's generated layer order."""
    phlo_dir = project_dir / ".phlo"
    cmd = ["docker", "compose", "-p", project_name, "-f", str(phlo_dir / "docker-compose.yml")]
    layers = [phlo_dir / "compose.shared.yaml"]
    platform_name = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(
        platform.system()
    )
    if platform_name:
        layers.append(phlo_dir / f"compose.{platform_name}.yaml")
    layers += [
        phlo_dir / "overrides" / "compose.host.yaml",
        phlo_dir / "overrides" / "compose.yaml",
        phlo_dir / "compose.local.yaml",
    ]
    for layer in layers:
        if layer.is_file():
            cmd += ["-f", str(layer)]
    for env_file in (path for path in project_env_paths(phlo_dir) if path.is_file()):
        cmd += ["--env-file", str(env_file)]
    cmd += ["--profile", "api"]
    return cmd


def _observatory_compose(
    project_dir: Path,
    project_name: str,
    action: list[str],
    *,
    stream_output: bool,
    timeout: int = 1800,
) -> None:
    """Run a compose action for the packaged Observatory service only.

    ``services start --service observatory`` expands the service definition's
    ``depends_on: phlo-api`` and would start the API container — which must not
    exist here because the API runs natively on the same host port. Direct
    compose keeps the CLI out of that expansion; ``--no-deps`` is belt and
    suspenders on top of the ``depends_on: !reset`` override.
    """
    cmd = _compose_base_args(project_dir, project_name) + action
    result = subprocess.run(
        cmd,
        cwd=project_dir,
        capture_output=not stream_output,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = "" if stream_output else f"\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(f"observatory compose {' '.join(action)} failed:{detail}")


def _generate_scenario_fixtures(project_dir: Path, python_executable: Path) -> None:
    """Regenerate the deterministic scenario batches inside the copied project."""
    result = subprocess.run(
        [str(python_executable), "scripts/generate_fixtures.py"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"generate_fixtures failed: {result.stdout}\n{result.stderr}")


@dataclass(slots=True)
class ObservatoryAcceptanceStack:
    """Runtime handle for the disposable acceptance stack."""

    project_dir: Path
    phlo_source: Path
    python_executable: Path
    ports: BundledStackPorts
    artifact_root: Path
    project_name: str = ""
    keep_running: bool = False
    fixture_sha: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)

    # -- URLs -------------------------------------------------------------

    @property
    def observatory_url(self) -> str:
        """Browser entrypoint: the packaged Observatory app."""
        return f"http://127.0.0.1:{self.ports.observatory}"

    @property
    def api_url(self) -> str:
        """Direct phlo-api host URL for independent assertions."""
        return f"http://127.0.0.1:{self.ports.phlo_api}"

    @property
    def dagster_url(self) -> str:
        return f"http://127.0.0.1:{self.ports.dagster}"

    @property
    def nessie_url(self) -> str:
        return f"http://127.0.0.1:{self.ports.nessie}"

    @property
    def trino_url(self) -> str:
        return f"http://127.0.0.1:{self.ports.trino}"

    # -- CLI / process helpers -------------------------------------------

    def run_phlo(
        self,
        args: list[str],
        *,
        timeout: int | None = None,
        check: bool = True,
        stream_output: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run_phlo(
            args,
            cwd=self.project_dir,
            timeout=timeout,
            check=check,
            stream_output=stream_output,
            python_exe=self.python_executable,
            env=env,
        )

    def materialize(
        self,
        asset: str,
        *,
        partition: str | None = None,
        timeout: int = 900,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Launch a materialization via the phlo CLI; return the completed process."""
        args = ["materialize", asset, "--json"]
        if partition is not None:
            args += ["--partition", partition]
        return self.run_phlo(args, timeout=timeout, stream_output=False, check=check)

    def materialize_run_id(
        self, asset: str, *, partition: str | None = None, timeout: int = 900
    ) -> str:
        """Launch a materialization and return the logical run id."""
        result = self.materialize(asset, partition=partition, timeout=timeout)
        payload = json.loads(result.stdout)
        run_id = payload.get("data", {}).get("logical_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError(f"materialize returned no logical_run_id: {result.stdout}")
        return run_id

    # -- scenario staging ---------------------------------------------------

    def stage_scenario(self, scenario: str) -> list[Path]:
        """Stage one scenario's fixture files into ``generated-data/inbound``.

        Mirrors ``scripts/run_scenario.py``'s ``stage_inbound``: wipe the
        inbound dir, then copy the scenario's ``*.ndjson.gz`` deliveries.
        The dagster container sees the files under ``/app/generated-data``.
        """
        source_dir = self.project_dir / "generated-data" / "scenarios" / scenario
        files = sorted(source_dir.glob("*.ndjson.gz"))
        if not files:
            raise RuntimeError(f"scenario {scenario!r} has no fixtures under {source_dir}")
        inbound = self.project_dir / "generated-data" / "inbound"
        if inbound.exists():
            shutil.rmtree(inbound)
        inbound.mkdir(parents=True)
        staged = []
        for path in files:
            target = inbound / path.name
            shutil.copyfile(path, target)
            staged.append(target)
        return staged

    def run_scenario(self, scenario: str, *, timeout: int = 1200) -> None:
        """Drive a scenario through the lab's own runner script."""
        result = subprocess.run(
            [str(self.python_executable), "scripts/run_scenario.py", scenario],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"run_scenario {scenario!r} failed:\n{result.stdout}\n{result.stderr}"
            )

    # -- WAP report helpers ---------------------------------------------------

    def wap_reports(self) -> dict[str, dict[str, Any]]:
        """Return every WAP report keyed by logical run id."""
        reports: dict[str, dict[str, Any]] = {}
        reports_dir = self.wap_reports_dir()
        if not reports_dir.is_dir():
            return reports
        for path in sorted(reports_dir.glob("*.json")):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                payload = json.loads(path.read_text())
                if isinstance(payload, dict):
                    reports[path.stem] = payload
        return reports

    def wap_report(self, run_id: str) -> dict[str, Any] | None:
        """Return one run's WAP report, or None when not yet written."""
        return self.wap_reports().get(run_id)

    def wait_for_wap_report(self, run_id: str, *, timeout: float = 900.0) -> dict[str, Any]:
        """Poll until a run's WAP report reaches a terminal classification."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self.wap_report(run_id)
            if payload and self.classify_wap_report(payload) != "in_flight":
                return payload
            time.sleep(2)
        raise TimeoutError(f"no terminal WAP report for {run_id} after {timeout}s")

    @staticmethod
    def classify_wap_report(payload: dict[str, Any] | None) -> str:
        """Classify one report the same way the lab's runner does.

        Mirrors ``scripts/run_scenario.py``'s ``classify_report``:
        ``promoted``/``blocked``/``failed``/``in_flight``/``missing``.
        """
        if payload is None:
            return "missing"
        status = str(payload.get("status", ""))
        failure_reason = payload.get("failure_reason")
        if status == "promoted":
            return "promoted"
        if status in {"promotion_blocked", "promotion_failed"}:
            return "blocked"
        if status == "failed" or failure_reason == "dagster_run_failed":
            return "failed"
        return "in_flight"

    # -- independent provider assertions ----------------------------------

    def trino_query(self, sql: str, *, timeout: int = 60) -> list[tuple]:
        """Run a Trino query from the host and return rows."""
        import trino

        env = self.env_vars
        connection = trino.dbapi.connect(
            host="127.0.0.1",
            port=self.ports.trino,
            user=env.get("TRINO_USER", "phlo-acceptance"),
            catalog="iceberg",
        )
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        finally:
            connection.close()

    def nessie_branches(self) -> list[str]:
        """List live Nessie branch names."""
        from phlo_nessie.resource import NessieResource

        nessie = NessieResource(base_url=self.nessie_url)
        return [branch.name for branch in nessie.list_branches()]

    def psql(self, sql: str, params: tuple | None = None, *, timeout: int = 30) -> list[tuple]:
        """Run a read query against the stack Postgres from the host."""
        import psycopg2

        env = self.env_vars
        connection = psycopg2.connect(
            host="127.0.0.1",
            port=self.ports.postgres,
            user=env.get("POSTGRES_USER", "phlo"),
            password=env.get("POSTGRES_PASSWORD", "phlo"),
            dbname=env.get("POSTGRES_DB", "phlo"),
            connect_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        finally:
            connection.close()

    def dagster_run_status(self, run_id: str) -> str | None:
        """Read a Dagster run's persisted status from the metadata DB."""
        rows = self.psql("SELECT status FROM runs WHERE run_id = %s", (run_id,))
        return str(rows[0][0]) if rows else None

    def dagster_run_tags(self, run_id: str) -> dict[str, str]:
        """Read persisted Dagster run tags for run_id."""
        rows = self.psql("SELECT key, value FROM run_tags WHERE run_id = %s", (run_id,))
        return {str(k): str(v) for k, v in rows}

    def dagster_graphql(
        self, query: str, variables: dict[str, Any] | None = None, *, timeout: int = 30
    ) -> dict[str, Any]:
        """Execute a GraphQL operation against the stack's Dagster webserver."""
        import requests

        response = requests.post(
            f"{self.dagster_url}/graphql",
            json={"query": query, "variables": variables or {}},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    _SENSOR_SELECTOR = {
        "repositoryLocationName": "phlo_dagster.framework.definitions",
        "repositoryName": "__repository__",
    }
    # From the fixture phlo.yaml ``wap.repository_location_name`` — the shared
    # WAP repository where the branch/promotion sensors are defined.

    def _sensor_instigation_id(self, name: str, *, timeout: int = 30) -> str:
        """Resolve a sensor's persisted instigation-state id for stopSensor."""
        payload = self.dagster_graphql(
            "query($sel: RepositorySelector!) { sensorsOrError(repositorySelector: $sel) { "
            "__typename ... on Sensors { results { name id } } "
            "... on RepositoryNotFoundError { message } } }",
            {"sel": self._SENSOR_SELECTOR},
            timeout=timeout,
        )
        sensors = payload.get("data", {}).get("sensorsOrError", {}).get("results") or []
        for sensor in sensors:
            if sensor.get("name") == name:
                return str(sensor["id"])
        raise RuntimeError(f"sensor not found in repository: {name}")

    def set_sensor(self, name: str, *, running: bool, timeout: int = 30) -> dict[str, Any]:
        """Start or stop one Dagster sensor; returns the mutation payload.

        Used to hold ``wap_auto_promotion_sensor`` stopped while a scenario
        exercises the manual promotion path, then resume it so the lab's own
        auto-promotion scenarios keep their fast sensor cadence. This Dagster
        version starts sensors by selector but stops them by instigation id.
        """
        if running:
            payload = self.dagster_graphql(
                "mutation($sel: SensorSelector!) { startSensor(sensorSelector: $sel) { "
                "__typename ... on Sensor { sensorState { status } } "
                "... on PythonError { message } } }",
                {"sel": {**self._SENSOR_SELECTOR, "sensorName": name}},
                timeout=timeout,
            )
            result = payload.get("data", {}).get("startSensor") or {}
        else:
            payload = self.dagster_graphql(
                "mutation($sid: String) { stopSensor(id: $sid) { __typename "
                "... on StopSensorMutationResult { instigationState { status } } "
                "... on PythonError { message } } }",
                {"sid": self._sensor_instigation_id(name, timeout=timeout)},
                timeout=timeout,
            )
            result = payload.get("data", {}).get("stopSensor") or {}
        typename = result.get("__typename")
        if typename == "PythonError" or payload.get("errors"):
            raise RuntimeError(f"set_sensor {name} running={running} failed: {payload}")
        return payload.get("data", {})

    def wait_for_report_status(
        self, run_id: str, statuses: set[str], *, timeout: float = 900.0
    ) -> dict[str, Any]:
        """Poll until a run's WAP report reaches one of ``statuses``.

        Unlike ``wait_for_wap_report`` this accepts mid-lifecycle states
        (``success`` = audited, pre-promotion), which is the stable window a
        ``review_hold`` launch rests in awaiting operator promotion.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self.wap_report(run_id)
            if payload and str(payload.get("status", "")) in statuses:
                return payload
            time.sleep(2)
        raise TimeoutError(f"report for {run_id} never reached {sorted(statuses)} after {timeout}s")

    def api_get(
        self,
        path: str,
        *,
        token: str | None = OPERATOR_TOKEN,
        timeout: int = 30,
    ) -> Any:
        """GET the API directly (not through the Observatory proxy)."""
        import requests

        headers = {"accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return requests.get(f"{self.api_url}{path}", headers=headers, timeout=timeout)

    def api_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        token: str | None = OPERATOR_TOKEN,
        timeout: int = 60,
    ) -> Any:
        """POST to the API directly with the CSRF marker (bearer-authed)."""
        import requests

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-phlo-request": "observatory",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return requests.post(
            f"{self.api_url}{path}", headers=headers, json=payload, timeout=timeout
        )

    def proxied_get(self, path: str, *, token: str | None = OPERATOR_TOKEN, timeout: int = 30):
        """GET through the packaged Observatory same-origin proxy."""
        import requests

        # Browser-like negotiation: a JSON-only Accept makes the packaged app
        # correctly refuse HTML routes with 406.
        headers = {"accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return requests.get(f"{self.observatory_url}{path}", headers=headers, timeout=timeout)

    # -- lifecycle ---------------------------------------------------------

    def stop_service(self, name: str, *, timeout: int = 180) -> None:
        if name in ACCEPTANCE_NATIVE_SERVICES:
            self.run_phlo(
                ["services", "stop", "--native", "--service", name],
                timeout=timeout,
                check=False,
                stream_output=True,
            )
            return
        if name == "observatory":
            _observatory_compose(
                self.project_dir,
                self.project_name,
                ["stop", "observatory"],
                stream_output=True,
                timeout=timeout,
            )
            return
        self.run_phlo(
            ["services", "stop", "--service", name],
            timeout=timeout,
            check=False,
            stream_output=True,
        )

    def start_service(self, name: str, *, timeout: int = 600) -> None:
        if name in ACCEPTANCE_NATIVE_SERVICES:
            _start_native_services(self.project_dir)
            return
        if name == "observatory":
            _observatory_compose(
                self.project_dir,
                self.project_name,
                ["up", "-d", "--no-deps", "observatory"],
                stream_output=True,
                timeout=timeout,
            )
            return
        self.run_phlo(
            ["services", "start", "--service", name],
            timeout=timeout,
            check=False,
            stream_output=True,
        )

    def restart_service(self, name: str, *, timeout: int = 600) -> None:
        if name in ACCEPTANCE_NATIVE_SERVICES:
            self.stop_service(name, timeout=min(timeout, 180))
            self.start_service(name, timeout=timeout)
            return
        if name == "observatory":
            _observatory_compose(
                self.project_dir,
                self.project_name,
                ["restart", "--no-deps", "observatory"],
                stream_output=True,
                timeout=timeout,
            )
            return
        self.run_phlo(
            ["services", "restart", "--service", name],
            timeout=timeout,
            check=False,
            stream_output=True,
        )

    def wait_for_service(self, url: str, *, name: str, timeout: int = 180) -> None:
        if not wait_for_http(url, name=name, timeout=timeout):
            raise RuntimeError(f"{name} did not become ready: {url}")

    def wap_reports_dir(self) -> Path:
        return self.project_dir / ".phlo" / "wap-reports"

    def cleanup(self, *, force: bool = False) -> None:
        if self.keep_running and not force:
            return
        with contextlib.suppress(Exception):
            self.run_phlo(
                ["services", "stop", "--native"],
                timeout=120,
                check=False,
                stream_output=True,
            )
        with contextlib.suppress(Exception):
            self.run_phlo(["services", "stop"], timeout=300, check=False, stream_output=True)
        with contextlib.suppress(Exception):
            force_remove_directory(self.project_dir)


def _cleanup_prior_acceptance_projects(base_dir: Path, *, stream_output: bool) -> None:
    """Stop and remove acceptance projects left behind by earlier runs."""
    for project_dir in sorted(base_dir.glob("phlo-accept-*")):
        python_executable = project_dir / ".venv" / "bin" / "python"
        if (project_dir / ".phlo").exists():
            for stop_args in (["services", "stop", "--native"], ["services", "stop"]):
                with contextlib.suppress(Exception):
                    run_phlo(
                        stop_args,
                        cwd=project_dir,
                        timeout=180,
                        check=False,
                        stream_output=stream_output,
                        python_exe=(
                            python_executable if python_executable.exists() else sys.executable
                        ),
                    )
        with contextlib.suppress(Exception):
            force_remove_directory(project_dir)

    with contextlib.suppress(Exception):
        container_result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=phlo-accept-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        container_ids = [
            line.strip() for line in container_result.stdout.splitlines() if line.strip()
        ]
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
    with contextlib.suppress(Exception):
        network_result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        network_names = [
            line.strip()
            for line in network_result.stdout.splitlines()
            if line.strip().startswith("phlo-accept-")
        ]
        if network_names:
            subprocess.run(
                ["docker", "network", "rm", *network_names],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )


def _wait_for_acceptance_services(stack: ObservatoryAcceptanceStack) -> None:
    """Block until every acceptance service answers its readiness probe."""
    import requests

    stack.wait_for_service(
        f"http://127.0.0.1:{stack.ports.minio_api}/minio/health/live",
        name="MinIO",
        timeout=120,
    )
    stack.wait_for_service(f"{stack.nessie_url}/api/v2/config", name="Nessie", timeout=180)
    stack.wait_for_service(
        f"http://127.0.0.1:{stack.ports.trino}/v1/info", name="Trino", timeout=300
    )
    stack.wait_for_service(f"{stack.api_url}/health", name="Phlo API", timeout=600)
    # Dagster GraphQL: a version query must answer.
    deadline = time.time() + 300
    while True:
        try:
            response = requests.post(
                f"{stack.dagster_url}/graphql",
                json={"query": "query Version { version }"},
                timeout=5,
            )
            if response.ok and response.json().get("data", {}).get("version"):
                break
        except Exception:
            pass
        if time.time() >= deadline:
            raise RuntimeError("Dagster GraphQL did not become ready")
        time.sleep(2)
    stack.wait_for_service(f"{stack.observatory_url}/", name="Observatory", timeout=600)


def bootstrap_observatory_stack(
    *,
    stream_output: bool = True,
    keep_running: bool | None = None,
) -> ObservatoryAcceptanceStack:
    """Boot the disposable acceptance stack and return its handle.

    Sequence: copy the fixture, make it workspace-safe, create the project
    venv, generate services with dev-mode phlo-api + the api profile
    (observatory), configure auth/RBAC/ports, stage scenario fixtures, start
    and health-check every service.
    """
    if not FIXTURE_PATH.is_dir():
        raise RuntimeError(f"Acceptance fixture missing: {FIXTURE_PATH}")

    docker_info = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False, timeout=30
    )
    if docker_info.returncode != 0:
        raise RuntimeError("Docker daemon is unavailable for the acceptance stack")

    phlo_source = _repo_root()
    project_name = _unique_project_name()
    project_dir = _repo_root() / ".tmp" / project_name
    should_keep = keep_bundled_stack_running() if keep_running is None else keep_running
    artifact_root = acceptance_artifact_root()

    _cleanup_prior_acceptance_projects(project_dir.parent, stream_output=stream_output)
    _verify_bind_mount_parent(project_dir.parent)

    python_executable: Path | None = None
    try:
        _copy_fixture(project_dir)
        _rewrite_fixture_pyproject(project_dir)
        _rewrite_fixture_phlo_yaml(project_dir, project_name)
        # The copied uv.lock pins phlo to a git SHA; dropping it keeps image
        # builds off the lock-aware path (dev mode supplies the real code).
        (project_dir / "uv.lock").unlink(missing_ok=True)

        python_executable = Path(setup_project_venv(project_dir, phlo_source))
        _extend_project_venv(project_dir, phlo_source, python_executable)

        run_phlo(
            [
                "services",
                "init",
                "--dev",
                "--phlo-source",
                str(phlo_source),
                "--profile",
                "api",
                "--force",
            ],
            cwd=project_dir,
            timeout=300,
            stream_output=stream_output,
            python_exe=python_executable,
        )

        env_updates = build_bundled_stack_env_updates(resolve_port, project_name=project_name)
        env_updates["PHLO_DEV_EXTRA_PACKAGES"] = ",".join(ACCEPTANCE_DEV_PACKAGES)
        env_updates["PHLO_AUTHORIZATION_MODE"] = "required"
        env_updates["PHLO_AUTHENTICATION_PROVIDER"] = "static"
        env_updates["PHLO_OBSERVATORY_SETTINGS_BACKEND"] = "postgres"
        apply_env_updates(project_dir / ".phlo", env_updates)
        # The native API reaches stack Postgres over the published host port
        # rather than the compose-network DSN baked into the container spec.
        # The password is generated into secrets/.env by services init.
        secrets = read_env_file(env_secrets_path(project_dir / ".phlo"))
        apply_env_updates(
            project_dir / ".phlo",
            {
                "PHLO_RUN_EVIDENCE_DB_URL": (
                    "postgresql://"
                    f"{secrets.get('POSTGRES_USER', 'phlo')}:"
                    f"{secrets.get('POSTGRES_PASSWORD', 'phlo')}"
                    f"@127.0.0.1:{env_updates.get('POSTGRES_PORT', '5432')}"
                    f"/{secrets.get('POSTGRES_DB', 'phlo')}"
                )
            },
        )
        _write_compose_override(project_dir)

        _write_authorization_files(project_dir)
        _write_auth_secrets(project_dir)
        _write_service_credentials(project_dir)
        _generate_scenario_fixtures(project_dir, python_executable)

        start_args = ["services", "start", "--build"]
        for service_name in ACCEPTANCE_SERVICES:
            start_args.extend(["--service", service_name])
        run_phlo(
            start_args,
            cwd=project_dir,
            timeout=3600,
            stream_output=stream_output,
            python_exe=python_executable,
        )

        # phlo-api runs natively: its dev.command spawns uvicorn from the
        # project venv, so the API serves this repository's current source and
        # can exec project workflow files during capability discovery.
        _start_native_services(project_dir)

        # Packaged Observatory container: built from the staged .phlo/web
        # context, proxies to the native API via host.docker.internal.
        _observatory_compose(
            project_dir,
            project_name,
            ["up", "-d", "--build", "--no-deps", "observatory"],
            stream_output=stream_output,
        )

        env_vars: dict[str, str] = {}
        for env_path in project_env_paths(project_dir / ".phlo"):
            if env_path.is_file():
                env_vars.update(read_env_file(env_path))
        ports = BundledStackPorts.from_env(env_vars)
        stack = ObservatoryAcceptanceStack(
            project_dir=project_dir,
            phlo_source=phlo_source,
            python_executable=python_executable,
            ports=ports,
            artifact_root=artifact_root,
            project_name=project_name,
            keep_running=should_keep,
            fixture_sha=_fixture_git_sha(),
            env_vars=env_vars,
        )
        _wait_for_acceptance_services(stack)
        return stack
    except Exception:
        if not should_keep:
            for stop_args in (["services", "stop", "--native"], ["services", "stop"]):
                with contextlib.suppress(Exception):
                    run_phlo(
                        stop_args,
                        cwd=project_dir,
                        timeout=300,
                        check=False,
                        stream_output=stream_output,
                        python_exe=python_executable or sys.executable,
                    )
            with contextlib.suppress(Exception):
                force_remove_directory(project_dir)
        raise


__all__ = [
    "ACCEPTANCE_SERVICES",
    "FIXTURE_PATH",
    "OPERATOR_SUBJECT",
    "OPERATOR_TOKEN",
    "ObservatoryAcceptanceStack",
    "UNKNOWN_TOKEN",
    "VIEWER_SUBJECT",
    "VIEWER_TOKEN",
    "acceptance_artifact_root",
    "acceptance_enabled",
    "bootstrap_observatory_stack",
]
