"""Installed-consumer Observatory acceptance driver (incomplete, fail-closed).

The caller supplies immutable candidate and previous BOMs. Every project and
container belongs to this run. Failures remain acceptance failures, including
missing browser tooling, unavailable prerequisites and unsupported migrations.
Legacy activation and deployed preview enforcement still require completion;
synthetic driver tests are not release qualification.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path

import release_candidate_bom as bom_module
import release_golden_path as golden

ROUTES = ("", "assets", "incidents", "pipelines", "query", "branches")
SEED_COMMITS = 251
ROWS = [
    {"id": 7, "label": "retained", "active": True},
    {"id": 19, "label": "synthetic", "active": False},
]


def request(url: str, *, payload: dict | None = None, expected: tuple[int, ...] = (200,)) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    if status not in expected:
        # Never emit backend error bodies, which may contain credentials or SQL.
        raise golden.CandidateError(f"Observatory request failed with HTTP {status}")
    return json.loads(body) if body else {}


def installed(config: golden.RunConfig, source: str, *args: str) -> dict:
    """Run test-driver code against installed wheels with source imports disabled."""
    result = golden.run(
        golden.command(str(config.operator_python), "-I", "-c", source, *args),
        cwd=config.project_dir,
        capture_output=True,
    )
    return json.loads(result.stdout)


def target(config: golden.RunConfig) -> dict:
    return installed(
        config,
        """
import json
from pathlib import Path
from phlo.config.project import get_environment_targets, get_default_environment
root = Path.cwd()
env = get_default_environment(root)
print(json.dumps({'env':env, **get_environment_targets(root)[env].model_dump(mode='json')}))
""",
    )


def compose(config: golden.RunConfig, *args: str, capture: bool = False):
    return golden.run(
        golden.compose_command(config, *args), cwd=config.project_dir, capture_output=capture
    )


def initialize(config: golden.RunConfig, *, legacy: bool = False) -> None:
    config.project_dir.mkdir()
    options = ("--profile", "api") if legacy else ("--observatory-dev",)
    golden.run(
        golden.command(str(config.operator_bin), "services", "init", "--no-dev", *options),
        cwd=config.project_dir,
    )
    secret_path = golden.env_secrets_path(config.project_dir / ".phlo")
    with secret_path.open("a", encoding="utf-8") as stream:
        stream.writelines(f"{name}=0\n" for name in (*golden.PORT_NAMES, "OBSERVATORY_PORT"))
    # Use the previous version's real API profile, never a candidate-only flag.
    # Legacy environment activation remains an explicit compatibility boundary.
    golden.pin_candidate_images(config)


def private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open(
        "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)
    ) as stream:
        stream.write(content)


def tls_material(root: Path) -> None:
    root.mkdir(mode=0o700)
    private_file(root / "key-password", secrets.token_hex(24))
    private_file(root / "password", secrets.token_hex(24))
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(root / "server.key"),
            "-out",
            str(root / "ca.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=trino-preview",
            "-addext",
            "subjectAltName=DNS:trino-preview,DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    (root / "server.key").chmod(0o600)
    subprocess.run(
        [
            "openssl",
            "pkcs12",
            "-export",
            "-in",
            str(root / "ca.pem"),
            "-inkey",
            str(root / "server.key"),
            "-out",
            str(root / "server.p12"),
            "-passout",
            f"file:{root / 'key-password'}",
        ],
        check=True,
        capture_output=True,
    )
    (root / "server.p12").chmod(0o600)


def preview_config(config: golden.RunConfig, binding: dict, network: str) -> Path:
    root = config.project_dir / ".phlo" / "acceptance-private"
    tls_material(root)
    # This is project configuration for a disposable test, not framework source
    # copied into the image. The provider validates and generates the server.
    installed(
        config,
        """
import json, sys, yaml
from pathlib import Path
from phlo.config.layout import project_env_paths
from phlo.infrastructure.utils import parse_env_file
root=Path.cwd(); binding=json.loads(sys.argv[1]); private=root/'.phlo/acceptance-private'
values={}
for path in project_env_paths(root/'.phlo'):
    values.update(parse_env_file(path))
props=('connector.name=iceberg\\niceberg.catalog.type=rest\\n'
       'iceberg.rest-catalog.uri=http://nessie:19120/iceberg\\n'
       'iceberg.rest-catalog.warehouse=warehouse\\n'
       'iceberg.rest-catalog.vended-credentials-enabled=false\\n'
       'fs.s3.enabled=true\\ns3.endpoint=http://minio:9000\\ns3.path-style-access=true\\n'
       's3.region=us-east-1\\n'
       + 'iceberg.rest-catalog.prefix='+binding['nessie_ref']+'\\n'
       + 's3.aws-access-key='+values['MINIO_ROOT_USER']+'\\n'
       + 's3.aws-secret-key='+values['MINIO_ROOT_PASSWORD']+'\\n')
catalog=private/'catalog.properties'; catalog.touch(mode=0o600); catalog.write_text(props)
path=root/'phlo.yaml'; document=yaml.safe_load(path.read_text())
document['environments'][binding['env']]['preview']={
    'catalog':'acceptance_preview','catalog_file':str(catalog.relative_to(root)),
    'password_file':'.phlo/acceptance-private/password'}
path.write_text(yaml.safe_dump(document))
policy=root/'.phlo/authorization/policies.yaml'; document=yaml.safe_load(policy.read_text())
document['policies'].append({'policy_id':'acceptance-query','effect':'allow',
    'principal':{'roles':['admin']},'action':'dataset.query',
    'resource':{'type':'project','id_pattern':'env='+binding['env']}})
policy.write_text(yaml.safe_dump(document))
print('{}')
""",
        json.dumps(binding),
    )
    inputs = root / "preview.json"
    private_file(
        inputs,
        json.dumps(
            {
                "ca_file": "ca.pem",
                "keystore_file": "server.p12",
                "keystore_password_file": "key-password",
                "network": network,
            }
        ),
    )
    return inputs


def provision_preview(config: golden.RunConfig, binding: dict) -> list:
    document = golden.compose_config_json(config)
    networks = document["networks"]
    if len(networks) != 1:
        raise golden.CandidateError("Acceptance requires one explicit shared test network")
    network = next(iter(networks.values()))["name"]
    inputs = preview_config(config, binding, network)
    output = inputs.parent / "generated"
    golden.run(
        golden.command(
            str(config.operator_bin),
            "trino",
            "preview",
            "provision",
            "--config",
            str(inputs),
            "--output",
            str(output),
            "--project-root",
            str(config.project_dir),
        ),
        cwd=config.project_dir,
    )
    # Merge the provider's Compose into the same owned project. Existing BOM
    # pinning verifies this extra image too, before it is ever started.
    compose_file = output / "compose.yaml"
    installed(
        config,
        """
import json, sys, yaml
from pathlib import Path
root=Path.cwd(); output=Path(sys.argv[1]); layer=yaml.safe_load((output/'compose.yaml').read_text())
service=layer['services']['trino-preview']
for index,mount in enumerate(service.get('volumes',[])):
    source,destination=mount.split(':',1)
    service['volumes'][index]=str((output/source).resolve())+':'+destination
service['ports']=['127.0.0.1::8443']
layer.pop('networks',None); service.pop('networks',None)
settings=json.loads((output/'api-settings.json').read_text())
def mount_path(value): return value.replace(str(output),'/opt/phlo-preview')
settings={key:mount_path(value) for key,value in settings.items()}
settings.update({'PHLO_V1_PREVIEW_TRINO_URL':'https://trino-preview:8443','WEB_CONCURRENCY':'1'})
# Do not assert deployment security merely because provider files exist.
# Live process/replica/bounds verification is an unfinished acceptance seam.
layer['services']['phlo-api']={'environment':settings,'deploy':{'replicas':1},
    'command':['python','-m','uvicorn','phlo_api.main:app','--host','0.0.0.0','--port','4000','--workers','1'],
    'volumes':[str(output/'api-secrets')+':/opt/phlo-preview/api-secrets:ro']}
path=root/'.phlo/overrides/compose.yaml'
existing=yaml.safe_load(path.read_text()) if path.exists() else {}
for name,definition in layer['services'].items():
    existing.setdefault('services',{}).setdefault(name,{}).update(definition)
path.touch(mode=0o600); path.write_text(yaml.safe_dump(existing))
print('{}')
""",
        str(output),
    )
    # Retain provider output for private diagnosis. It never goes in evidence.
    if not compose_file.is_file():
        raise golden.CandidateError("Preview provider did not generate Compose")
    (config.project_dir / ".phlo/compose.candidate.json").unlink(missing_ok=True)
    _, images = golden.pin_candidate_images(config)
    services = golden.compose_config_json(config)["services"]
    for service, directory, filename in (
        ("phlo-api", "api-secrets", f"password-{binding['env']}"),
        ("trino-preview", "secrets", "preview-keystore.jks"),
        ("trino-preview", "trino", "config.properties"),
    ):
        definition = services[service]
        command = ["docker", "run", "--rm", "--network", "none"]
        if definition.get("user"):
            command += ["--user", definition["user"]]
        result = subprocess.run(
            [
                *command,
                "--mount",
                f"type=bind,src={output / directory},dst=/private,readonly",
                "--entrypoint",
                "sh",
                definition["image"],
                "-c",
                'test -r "$1"',
                "sh",
                f"/private/{filename}",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise golden.CandidateError(
                "Private preview mount is unreadable by its exact image identity; provision separate API/server ownership without widening permissions"
            )
    return images


def trino(config: golden.RunConfig, statement: str) -> str:
    return compose(
        config,
        "exec",
        "-T",
        "trino",
        "trino",
        "--output-format",
        "JSON",
        "--execute",
        statement,
        capture=True,
    ).stdout


def seed(config: golden.RunConfig, binding: dict) -> dict:
    """Seed real retained services, not API responses or fake version markers."""
    trino(config, "CREATE SCHEMA IF NOT EXISTS iceberg.release_acceptance")
    trino(
        config,
        "CREATE TABLE iceberg.release_acceptance.typed (id BIGINT, label VARCHAR, active BOOLEAN)",
    )
    trino(
        config,
        "INSERT INTO iceberg.release_acceptance.typed VALUES (7, 'retained', true), (19, 'synthetic', false)",
    )
    nessie = golden.service_url(config, "nessie", 19120)
    ref = urllib.parse.quote(binding["nessie_ref"], safe="")
    for index in range(SEED_COMMITS):
        request(
            f"{nessie}/iceberg/v1/{ref}/namespaces",
            payload={"namespace": [f"acceptance_{index:03d}"]},
        )
    location, repository = golden.discover_dagster_selector(config, "")
    if location != binding["dagster_location"]:
        raise golden.CandidateError("Loaded Dagster location differs from the selected environment")
    binding = {**binding, "repository": repository}
    source = """
import json, sys
from dagster import DagsterInstance, DagsterRunStatus
from dagster._core.storage.dagster_run import DagsterRun
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import WorkspaceFileTarget
binding=json.loads(sys.argv[1])
with DagsterInstance.get() as instance, WorkspaceProcessContext(instance,
        WorkspaceFileTarget(paths=['/opt/dagster/workspace.yaml'])) as workspace:
    location=workspace.create_request_context().get_code_location(binding['dagster_location'])
    origin=location.get_repository(binding['repository']).get_full_job('__ASSET_JOB').get_remote_origin()
    instance.add_run(DagsterRun(job_name='__ASSET_JOB',run_id='acceptance-legacy',status=DagsterRunStatus.SUCCESS,tags={}))
    instance.add_run(DagsterRun(job_name='__ASSET_JOB',run_id='acceptance-scoped',status=DagsterRunStatus.SUCCESS,
        tags={'phlo/ref':binding['nessie_ref']},remote_job_origin=origin))
"""
    compose(config, "exec", "-T", "dagster", "python", "-c", source, json.dumps(binding))
    verify_data(config)
    return {
        "nessie_commits_seeded": SEED_COMMITS,
        "dagster_runs": ["acceptance-legacy", "acceptance-scoped"],
        "typed_rows": len(ROWS),
    }


def verify_data(config: golden.RunConfig) -> None:
    lines = trino(
        config, "SELECT id, label, active FROM iceberg.release_acceptance.typed ORDER BY id"
    )
    if [json.loads(line) for line in lines.splitlines() if line.strip()] != ROWS:
        raise golden.CandidateError("Retained typed data changed")


def _browser(session: str, *args: str) -> dict:
    result = subprocess.run(
        ["agent-browser", "--session", session, *args, "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    envelope = json.loads(result.stdout)
    if not envelope.get("success"):
        raise golden.CandidateError("Packaged Observatory browser action failed")
    return envelope.get("data", {})


def browser_journey(config: golden.RunConfig, binding: dict, artifacts: Path) -> dict:
    base = golden.service_url(config, "observatory", 3000)
    env = urllib.parse.quote(binding["env"], safe="")
    session = f"rel-{os.getpid()}"
    artifacts.mkdir(parents=True, exist_ok=True)
    visited = []
    try:
        _browser(session, "open", f"{base}/?env={env}")
        _browser(session, "set", "viewport", "1280", "800", "2")
        for route in ROUTES:
            detail = (
                "&" + urllib.parse.urlencode({"branch": binding["nessie_ref"]})
                if route == "branches"
                else ""
            )
            _browser(session, "open", f"{base}/{route}?env={env}{detail}")
            _browser(session, "wait", "h1")
            state = _browser(
                session,
                "eval",
                "({title:document.querySelector('h1')?.textContent, text:document.querySelector('main')?.textContent})",
            ).get("result", {})
            if not state.get("title") or any(
                message in state.get("text", "").lower()
                for message in (
                    "internal server error",
                    "something went wrong",
                    "unauthorized",
                    "failed to load",
                )
            ):
                raise golden.CandidateError(f"Packaged Observatory route failed: /{route}")
            _browser(session, "screenshot", str(artifacts / f"{route or 'overview'}.png"))
            visited.append(route or "overview")
        # The packaged UI initiates asynchronous branch detail requests. Require
        # their visible result, then advance the real public API cursor as well.
        _browser(session, "wait", "--text", "Commit history")
        _browser(session, "wait", "--text", "Ahead: 0")
        api = golden.service_url(config, "phlo-api", 4000)
        commits = verify_history(api, binding)
        _browser(session, "open", f"{base}/query?env={env}")
        _browser(session, "wait", ".cm-content")
        _browser(session, "click", ".cm-content")
        _browser(session, "press", "Control+A")
        _browser(session, "press", "Backspace")
        _browser(
            session,
            "type",
            ".cm-content",
            "SELECT id, label, active FROM release_acceptance.typed ORDER BY id",
        )
        _browser(session, "find", "role", "button", "click", "--name", "Run", "--exact")
        _browser(session, "wait", 'table[aria-label="Query results"] tbody')
        text = _browser(session, "get", "text", 'table[aria-label="Query results"]').get("text", "")
        if "retained" not in text or "synthetic" not in text or "19" not in text:
            raise golden.CandidateError("Packaged query results do not display the seeded rows")
        _browser(session, "screenshot", str(artifacts / "query-result.png"))
        return {"routes": visited, "history": commits, "query_rows_displayed": len(ROWS)}
    finally:
        _browser(session, "close")


def verify_history(api: str, binding: dict) -> dict:
    ref = urllib.parse.quote(binding["nessie_ref"], safe="")
    env = urllib.parse.urlencode({"env": binding["env"], "limit": 100})
    hashes: set[str] = set()
    cursors: set[str] = set()
    cursor = None
    for _ in range(10):
        url = f"{api}/api/v1/branches/{ref}/commits?{env}"
        if cursor:
            url += "&" + urllib.parse.urlencode({"after": cursor})
        page = request(url)
        for commit in page["items"]:
            if commit["hash"] in hashes:
                raise golden.CandidateError("Branch history repeated a commit")
            hashes.add(commit["hash"])
        cursor = page["next_cursor"]
        if not cursor:
            break
        if cursor in cursors:
            raise golden.CandidateError("Branch history cursor loop")
        cursors.add(cursor)
    else:
        raise golden.CandidateError("Branch history exceeded bounded pagination")
    if len(hashes) < SEED_COMMITS or len(cursors) < 2:
        raise golden.CandidateError("Branch history did not advance through seeded commits")
    compared = request(
        f"{api}/api/v1/branches/{ref}/compare?"
        + urllib.parse.urlencode({"env": binding["env"], "target": binding["nessie_ref"]})
    )
    if (
        compared["status"] != "compared"
        or compared["ahead"] != 0
        or compared["behind"] != 0
        or compared["source_hash"] != compared["target_hash"]
    ):
        raise golden.CandidateError("Pinned self-comparison failed")
    diff = request(
        f"{api}/api/v1/branches/{ref}/diff?"
        + urllib.parse.urlencode({"env": binding["env"], "target": binding["nessie_ref"]})
    )
    if diff["items"] or diff["truncated"]:
        raise golden.CandidateError("Self-comparison returned table changes")
    return {"commits": len(hashes), "cursor_advancements": len(cursors), "self_compare": "pinned"}


def query_contract(api: str, binding: dict) -> dict:
    suffix = urllib.parse.urlencode({"env": binding["env"]})
    submitted = request(
        f"{api}/api/v1/queries?{suffix}",
        payload={
            "sql": "SELECT id, label, active FROM release_acceptance.typed ORDER BY id",
            "row_limit": 2,
        },
        expected=(202,),
    )
    for _ in range(60):
        result = request(f"{api}/api/v1/queries/{submitted['id']}?{suffix}")
        if result["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(1)
    if (
        result["status"] != "completed"
        or sorted(result["result"]["rows"], key=lambda row: row["id"]) != ROWS
        or not result["evidence_available"]
        or result["nessie_ref"] != binding["nessie_ref"]
    ):
        raise golden.CandidateError("Bounded public query or durable evidence failed")
    for sql in ("DELETE FROM release_acceptance.typed", "SET SESSION query_max_run_time = '1h'"):
        request(
            f"{api}/api/v1/queries?{suffix}", payload={"sql": sql, "row_limit": 2}, expected=(422,)
        )
    history = request(f"{api}/api/v1/queries/history?{suffix}")
    if not any(item["id"] == submitted["id"] for item in history["items"]):
        raise golden.CandidateError("Completed query is absent from durable public history")
    return {
        "query_id": submitted["id"],
        "durable_evidence": True,
        "write_denied": True,
        "session_override_denied": True,
    }


def verify_readiness(api: str, binding: dict) -> dict:
    report = request(f"{api}/api/v1/readiness?" + urllib.parse.urlencode({"env": binding["env"]}))
    for name in ("orchestration", "incidents", "query_usage", "query_preview"):
        checks = report["capabilities"][name]
        if not checks or any(check["state"] != "ready" for check in checks):
            raise golden.CandidateError(f"Feature readiness failed: {name}")
    return report


def verify_ports(config: golden.RunConfig) -> None:
    document = golden.compose_config_json(config)
    for definition in document["services"].values():
        for port in definition.get("ports", []):
            if port.get("host_ip") not in ("127.0.0.1", "::1"):
                raise golden.CandidateError("A generated service publishes beyond loopback")


def wait_ready(api: str, binding: dict) -> dict:
    for _ in range(120):
        try:
            return verify_readiness(api, binding)
        except (OSError, golden.CandidateError):
            time.sleep(1)
    raise golden.CandidateError("Feature readiness did not recover within 120 seconds")


def recover(config: golden.RunConfig, binding: dict, query_id: str) -> dict:
    api = golden.service_url(config, "phlo-api", 4000)
    compose(config, "stop", "postgres")
    try:
        request(f"{api}/health")
        report = request(
            f"{api}/api/v1/readiness?" + urllib.parse.urlencode({"env": binding["env"]})
        )
        if all(
            check["state"] == "ready"
            for checks in report["capabilities"].values()
            for check in checks
        ):
            raise golden.CandidateError("Backend outage was reported as fully ready")
    finally:
        compose(config, "start", "postgres")
    wait_ready(api, binding)
    compose(config, "restart", "phlo-api", "observatory")
    wait_ready(api, binding)
    evidence = request(
        f"{api}/api/v1/queries/{query_id}?" + urllib.parse.urlencode({"env": binding["env"]})
    )
    if (
        sorted(evidence["result"]["rows"], key=lambda row: row["id"]) != ROWS
        or not evidence["evidence_available"]
    ):
        raise golden.CandidateError("Query evidence did not survive API restart")
    verify_data(config)
    return {
        "liveness_during_outage": True,
        "feature_outage_detected": True,
        "durable_query_after_restart": True,
    }


def transition(previous: golden.RunConfig, candidate: golden.RunConfig) -> dict:
    """Apply a concrete candidate file plan to the *same* retained project."""
    compose(previous, "stop", "phlo-api", "observatory", "dagster", "dagster-daemon")
    backup = previous.project_dir.parent / "postgres-before.dump"
    dump_command = golden.compose_command(
        previous,
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"',
    )
    private_file(backup, "")
    with backup.open("wb") as stream:
        subprocess.run(
            dump_command,
            cwd=previous.project_dir,
            stdout=stream,
            stderr=subprocess.PIPE,
            check=True,
        )
    subprocess.run(
        golden.compose_command(previous, "exec", "-T", "postgres", "pg_restore", "--list"),
        input=backup.read_bytes(),
        capture_output=True,
        check=True,
    )
    golden.verify_candidate_bom(candidate)
    golden.install_operator_from_bom(candidate)
    plan_file = candidate.project_dir.parent / "reviewed-upgrade.json"
    golden.run(
        golden.command(
            str(candidate.operator_bin),
            "services",
            "upgrade",
            "--dry-run",
            "--output",
            str(plan_file),
        ),
        cwd=candidate.project_dir,
        capture_output=True,
    )
    token = installed(
        candidate,
        """
import json,sys
from pathlib import Path
from phlo.operations.generated_stack import GeneratedStackPlan
plan=GeneratedStackPlan.model_validate_json(Path(sys.argv[1]).read_text())
if plan.migrations or any(change.state=='conflict' for change in plan.files):
    raise SystemExit('Provider migration or customization conflict requires review')
print(json.dumps({'value':plan.token()}))
""",
        str(plan_file),
    )["value"]
    # The source project and its volumes are retained; --force and fixture
    # substrate upgrades are deliberately absent.
    command = golden.command(
        str(candidate.operator_bin),
        "services",
        "upgrade",
        "--apply",
        str(plan_file),
        "--confirm",
        token,
    )
    golden.run(command, cwd=candidate.project_dir, capture_output=True)
    golden.run(command, cwd=candidate.project_dir, capture_output=True)
    (candidate.project_dir / ".phlo/compose.candidate.json").unlink(missing_ok=True)
    golden.pin_candidate_images(candidate)
    migration = subprocess.run(
        golden.compose_command(
            candidate,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "phlo-api",
            "python",
            "-m",
            "phlo_api.migrations",
            "--check",
            "--json",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if migration.returncode or json.loads(migration.stdout)["state"] != "compatible":
        raise golden.CandidateError(
            "Provider schema migration required; retain verified backup and follow installed migration instructions"
        )
    return {
        "reviewed_plan_applied_twice": True,
        "verified_database_backup": True,
        "retained_project": True,
        "schema": "compatible",
    }


def start(config: golden.RunConfig) -> None:
    golden.start_stack_candidate(config)
    compose(
        config,
        "--profile",
        "api",
        "up",
        "--detach",
        "--no-build",
        "--wait",
        "--wait-timeout",
        "180",
    )


def verify_overview(api: str, binding: dict) -> dict:
    report = request(f"{api}/api/v1/overview?" + urllib.parse.urlencode({"env": binding["env"]}))
    if sum(report["run_status_counts"].values()) != 1:
        raise golden.CandidateError(
            "Selected overview did not isolate the single scoped Dagster run"
        )
    return {"scoped_runs": 1, "legacy_unscoped_excluded": True}


def run_one(
    config: golden.RunConfig, *, candidate: golden.RunConfig | None, artifacts: Path
) -> tuple[dict, list]:
    golden.verify_candidate_bom(config)
    _, wheels = golden.install_operator_from_bom(config)
    initialize(config, legacy=candidate is not None)
    try:
        binding = target(config)
    except subprocess.CalledProcessError as exc:
        raise golden.CandidateError(
            "Installed environment binding is unavailable; an explicit previous-release onboarding compatibility seam is required"
        ) from exc
    result = {}
    try:
        verify_ports(config)
        start(config)
        result["seed"] = seed(config, binding)
        if candidate is not None:
            result["transition"] = transition(config, candidate)
            config = candidate
            if target(config) != binding:
                raise golden.CandidateError("Upgrade changed selected environment target binding")
            start(config)
            verify_data(config)
        # Verify the optional disabled state before opting into provider-owned
        # preview. Missing configuration must not become fake success rows.
        api = golden.service_url(config, "phlo-api", 4000)
        disabled = request(
            f"{api}/api/v1/readiness?" + urllib.parse.urlencode({"env": binding["env"]})
        )
        if not any(
            check["state"] == "disabled" for check in disabled["capabilities"]["query_preview"]
        ):
            raise golden.CandidateError("Unconfigured preview lacks an honest disabled setup state")
        images = provision_preview(config, binding)
        verify_ports(config)
        start(config)
        api = golden.service_url(config, "phlo-api", 4000)
        result["readiness"] = wait_ready(api, binding)
        result["overview"] = verify_overview(api, binding)
        result["browser"] = browser_journey(config, binding, artifacts)
        result["query"] = query_contract(api, binding)
        result["recovery"] = recover(config, binding, result["query"]["query_id"])
        return result, wheels + images
    finally:
        # Containers/volumes are owned by this unique run. Private project and
        # screenshots remain on failure, never replace failure with cleanup.
        if config.compose_file.exists():
            compose(config, "down", "--volumes", "--remove-orphans")


def run_journey(
    config: golden.RunConfig, *, previous_bom: Path | None, artifacts: Path
) -> tuple[dict, list]:
    """Called by exact-BOM acceptance, never by source-mode fixture demonstrations."""
    for executable in ("agent-browser", "openssl", "docker"):
        if not shutil.which(executable):
            raise golden.CandidateError(f"Installed Observatory acceptance requires {executable}")
    if previous_bom is None:
        raise golden.CandidateError(
            "Retained-volume Observatory acceptance requires an explicit previous candidate BOM"
        )
    if config.bom is None:
        raise golden.CandidateError(
            "Installed Observatory acceptance requires an exact candidate BOM"
        )
    previous = bom_module.load_bom(previous_bom)
    if previous["canonical_candidate_digest"] == config.bom["canonical_candidate_digest"]:
        raise golden.CandidateError("Previous and candidate BOMs must identify different artifacts")
    temporary = Path(tempfile.mkdtemp(prefix="phlo-observatory-acceptance-"))
    fresh = replace(
        config,
        project_dir=temporary / "fresh",
        operator_env=temporary / "fresh-operator",
        project_name=f"{config.project_name}-fresh",
    )
    old = replace(
        config,
        project_dir=temporary / "upgrade",
        operator_env=temporary / "old-operator",
        project_name=f"{config.project_name}-upgrade",
        bom=previous,
        staging_dir=previous_bom.parent,
        wheelhouse=previous_bom.parent / "distributions",
    )
    candidate = replace(
        old,
        operator_env=temporary / "candidate-operator",
        bom=config.bom,
        staging_dir=config.staging_dir,
        wheelhouse=config.wheelhouse,
    )
    try:
        fresh_result, exercised = run_one(fresh, candidate=None, artifacts=artifacts / "fresh")
        upgrade_result, _ = run_one(old, candidate=candidate, artifacts=artifacts / "upgrade")
    except Exception as exc:
        # Do not copy secrets or raw provider errors to public evidence.
        raise golden.CandidateError(
            f"Observatory acceptance failed ({type(exc).__name__}); private regression state retained at {temporary}"
        ) from exc
    shutil.rmtree(temporary)
    return {
        "fresh": fresh_result,
        "upgrade": upgrade_result,
        "previous_candidate_digest": previous["canonical_candidate_digest"],
    }, exercised
