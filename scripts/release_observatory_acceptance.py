"""Installed-consumer Observatory acceptance driver (exact BOM, fail-closed).

The caller supplies immutable candidate and previous BOMs. Every project and
container belongs to this run. Failures remain acceptance failures, including
missing browser tooling, unavailable prerequisites and unsupported migrations.
Legacy activation is reviewed explicitly; deployed preview enforcement is
observed before enabling safety assertions. Synthetic driver tests and local
package builds are not released-candidate qualification.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
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

ROUTES = {
    "": "Overview",
    "assets": "Assets",
    "incidents": "Incidents",
    "pipelines": "Jobs",
    "query": "Query",
    "branches": "Branches",
}
SEED_COMMITS = 251
ROWS = [
    {"id": 7, "label": "retained", "active": True},
    {"id": 19, "label": "synthetic", "active": False},
]


def request(url: str, *, payload: dict | None = None, expected: tuple[int, ...] = (200,)) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    if status not in expected:
        # Never emit backend error bodies, which may contain credentials or SQL.
        raise golden.CandidateError(f"Observatory request failed with HTTP {status}")
    return json.loads(body) if body else {}


def installed(config: golden.RunConfig, source: str, *args: str) -> dict:
    """Run test-driver code against installed wheels with source imports disabled."""
    # Provider imports can initialise console logging. Keep it on the private
    # stderr boundary; only the driver's single JSON print is machine output.
    wrapper = (
        "import contextlib,sys\nrecords=[]\n"
        "with contextlib.redirect_stdout(sys.stderr):\n"
        f"    exec({source!r}, {{'print':lambda value: records.append(value)}})\n"
        "assert len(records)==1\nprint(records[0])\n"
    )
    result = golden.run(
        golden.command(str(config.operator_python), "-I", "-c", wrapper, *args),
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


def legacy_target(config: golden.RunConfig) -> dict:
    """Bind the owned legacy project to its running providers, not new APIs."""
    location, _ = golden.discover_dagster_selector(config, "")
    ref = installed(
        config,
        "import json; from phlo_nessie.settings import get_settings; print(json.dumps({'ref':get_settings().nessie_default_ref}))",
    )["ref"]
    request(
        golden.service_url(config, "nessie", 19120)
        + "/api/v2/trees/"
        + urllib.parse.quote(ref, safe="")
    )
    return {
        "env": "retained",
        "dagster_location": location,
        "nessie_ref": ref,
        "compose_project": config.project_name,
        "service_health_urls": {},
        "preview": None,
    }


def compose(config: golden.RunConfig, *args: str, capture: bool = True):
    return golden.run(
        golden.compose_command(config, *args), cwd=config.project_dir, capture_output=capture
    )


def initialize(config: golden.RunConfig, *, legacy: bool = False) -> None:
    if config.project_dir.name != config.project_name:
        raise golden.CandidateError(
            "Acceptance project path must match its explicit Compose identity"
        )
    # A services-only directory has no installed asset job to seed. Reuse the
    # installed project template, never inject framework modules into an image.
    golden.create_project(config)
    installed(
        config,
        """
import json,yaml
from importlib.resources import files
from pathlib import Path
root=Path.cwd(); path=root/'phlo.yaml'; document=yaml.safe_load(path.read_text())
(root/'retained-jvm.config').write_text(files('phlo_trino').joinpath('jvm.config').read_text()+'\\n-Dphlo.acceptance.retained=true\\n')
(root/'retained-dagster.yaml').write_text('telemetry:\\n  enabled: false\\n')
document.setdefault('infrastructure',{}).setdefault('services',{}).update({
    'trino':{'files':{'trino/jvm.config':{'source':'retained-jvm.config','mode':'replace'}}},
    'dagster':{'files':{'dagster/dagster.yaml':{'source':'retained-dagster.yaml','mode':'merge'}}}})
path.write_text(yaml.safe_dump(document))
print('{}')
""",
    )
    options = ("--profile", "api", "--no-dev") if legacy else ("--observatory-dev",)
    golden.run(
        golden.command(str(config.operator_bin), "services", "init", *options),
        cwd=config.project_dir,
    )
    secret_path = golden.env_secrets_path(config.project_dir / ".phlo")
    with secret_path.open("a", encoding="utf-8") as stream:
        for name in (*golden.PORT_NAMES, "OBSERVATORY_PORT"):
            # The configured startup gate needs explicit API/UI URLs. Other
            # providers can use Docker-assigned ephemeral ports and discovery.
            port = 0
            if name in {"PHLO_API_PORT", "OBSERVATORY_PORT"}:
                with socket.socket() as connection:
                    connection.bind(("127.0.0.1", 0))
                    port = connection.getsockname()[1]
            stream.write(f"{name}={port}\n")
    private_file(
        config.project_dir / ".phlo/retained-provider-note.txt", "retain unrelated files\n"
    )
    # Use the previous version's real API profile, never a candidate-only flag.
    # Legacy environment activation remains an explicit compatibility boundary.
    golden.pin_candidate_images(config)


def private_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
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
    preview_layer(config, output, document["services"]["phlo-api"])
    installed_start_pins(config, remove=True)
    (config.project_dir / ".phlo/compose.candidate.json").unlink(missing_ok=True)
    _, images = golden.pin_candidate_images(config)
    probe_private_mounts(config, binding)
    # This opt-in provider bundle is not a registered default stack service.
    # Start it through its generated Compose contract, without a build fallback.
    compose(config, "up", "--detach", "--no-build", "--wait", "trino-preview")
    return images


def preview_layer(config: golden.RunConfig, output: Path, api_definition: dict) -> None:
    """Stage API secrets into a private volume owned by its actual image UID."""
    if not (output / "compose.yaml").is_file():
        raise golden.CandidateError("Preview provider did not generate Compose")
    identity = golden.run(
        golden.command(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            *(("--user", api_definition["user"]) if api_definition.get("user") else ()),
            "--entrypoint",
            "python",
            api_definition["image"],
            "-c",
            "import os,json; print(json.dumps([os.getuid(),os.getgid()]))",
        ),
        cwd=config.project_dir,
        capture_output=True,
    )
    uid, gid = json.loads(identity.stdout)
    if not isinstance(uid, int) or not isinstance(gid, int) or uid <= 0 or gid < 0:
        raise golden.CandidateError("Acceptance requires a nonroot API image identity")
    installed(
        config,
        """
import json, sys, yaml
from pathlib import Path
root=Path.cwd(); output=Path(sys.argv[1]); layer=yaml.safe_load((output/'compose.yaml').read_text())
service=layer['services']['trino-preview']
for definition in layer['services'].values():
    for index,mount in enumerate(definition.get('volumes',[])):
        source,destination=mount.split(':',1)
        if source.startswith('./'):
            definition['volumes'][index]=str((output/source).resolve())+':'+destination
service['ports']=['127.0.0.1::8443']
layer.pop('networks',None); service.pop('networks',None)
settings=json.loads((output/'api-settings.json').read_text())
def mount_path(value): return value.replace(str(output),'/opt/phlo-preview')
settings={key:mount_path(value) for key,value in settings.items()}
settings.update({'PHLO_V1_PREVIEW_TRINO_URL':'https://trino-preview:8443','WEB_CONCURRENCY':'1'})
# Do not assert deployment security merely because provider files exist.
# Independent runtime verification enables these assertions later.
uid,gid=json.loads(sys.argv[2]); image=sys.argv[3]
layer['services']['phlo-api-preview-init']={
    'image':image,'user':'0:0','network_mode':'none','read_only':True,
    'cap_drop':['ALL'],'cap_add':['CHOWN','DAC_OVERRIDE','FOWNER'],
    'entrypoint':['python','-c'],
    'command':['import os,shutil; from pathlib import Path; '
        'source=Path("/source"); target=Path("/target"); '
        'shutil.copytree(source,target,dirs_exist_ok=True); '
        +f'[(os.chown(p,{uid},{gid}),os.chmod(p,0o700 if p.is_dir() else 0o600)) '
        'for p in [target,*target.rglob("*")]]'],
    'volumes':[str(output/'api-secrets')+':/source:ro','preview-api-secrets:/target']}
layer['services']['phlo-api']={'environment':settings,'deploy':{'replicas':1},
    'command':['python','-m','uvicorn','phlo_api.main:app','--host','0.0.0.0','--port','4000','--workers','1'],
    'depends_on':{'phlo-api-preview-init':{'condition':'service_completed_successfully'}},
    'volumes':['preview-api-secrets:/opt/phlo-preview/api-secrets:ro']}
layer.setdefault('volumes',{})['preview-api-secrets']={}
path=root/'.phlo/overrides/compose.yaml'
existing=yaml.safe_load(path.read_text()) if path.exists() else {}
for name,definition in layer['services'].items():
    service=existing.setdefault('services',{}).setdefault(name,{})
    for key,value in definition.items():
        if key in ('environment','depends_on'):
            service.setdefault(key,{}).update(value)
        elif key=='volumes':
            service.setdefault(key,[]).extend(value)
        else:
            service[key]=value
existing.setdefault('volumes',{}).update(layer.get('volumes',{}))
path.touch(mode=0o600); path.write_text(yaml.safe_dump(existing))
print('{}')
""",
        str(output),
        json.dumps([uid, gid]),
        api_definition["image"],
    )


def probe_private_mounts(config: golden.RunConfig, binding: dict) -> None:
    """Run image-identity reads, without widening any operator file permissions."""
    services = golden.compose_config_json(config)["services"]
    # Let the provider stage private server files into its owned volumes. The
    # nonroot coordinator must read the actual runtime mounts, not host binds.
    if "trino-preview-init" in services:
        compose(config, "run", "--rm", "--no-deps", "-T", "trino-preview-init")
    compose(
        config,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "sh",
        "trino-preview",
        "-c",
        "test -r /etc/trino/config.properties && test -r /etc/trino/secrets/preview-keystore.jks",
    )
    compose(config, "run", "--rm", "--no-deps", "-T", "phlo-api-preview-init")
    compose(
        config,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        "python",
        "phlo-api",
        "-c",
        "from pathlib import Path; import sys; "
        "assert all((Path('/opt/phlo-preview/api-secrets')/p).read_bytes() "
        "for p in ['ca.pem',sys.argv[1]])",
        f"password-{binding['env']}",
    )


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


def verify_preview_runtime(config: golden.RunConfig, binding: dict) -> dict:
    """Observe actual processes, private mounts and TLS server enforcement first."""
    containers = compose(config, "ps", "-q", "phlo-api", capture=True).stdout.split()
    if len(containers) != 1:
        raise golden.CandidateError("Preview admission requires exactly one running API replica")
    compose(
        config,
        "exec",
        "-T",
        "phlo-api",
        "python",
        "-c",
        """
import os
from pathlib import Path
command=Path('/proc/1/cmdline').read_bytes().split(b'\\0')
assert b'uvicorn' in command and command.count(b'--workers')==1
assert command[command.index(b'--workers')+1]==b'1'
for path in Path('/proc').iterdir():
    if path.name.isdigit() and int(path.name) not in (1,os.getpid()):
        value=(path/'cmdline').read_bytes()
        assert b'uvicorn' not in value and b'multiprocessing' not in value
""",
    )
    properties = dict(
        line.split("=", 1)
        for line in compose(
            config, "exec", "-T", "trino-preview", "cat", "/etc/trino/config.properties"
        ).stdout.splitlines()
        if "=" in line and not line.startswith("#")
    )
    expected = {
        "query.max-run-time": "20s",
        "query.max-planning-time": "5s",
        "query.max-scan-physical-bytes": "256MB",
        "http-server.authentication.type": "PASSWORD",
    }
    groups = json.loads(
        compose(
            config, "exec", "-T", "trino-preview", "cat", "/etc/trino/preview/resource-groups.json"
        ).stdout
    )
    group = groups["rootGroups"][0]["subGroups"][0]["subGroups"][0]
    if (
        any(properties.get(key) != value for key, value in expected.items())
        or groups["selectors"] != [{"group": "global.preview.api"}]
        or any(
            group.get(key) != value
            for key, value in {
                "name": "api",
                "hardConcurrencyLimit": 2,
                "maxQueued": 0,
                "hardPhysicalDataScanLimit": "1GB",
            }.items()
        )
    ):
        raise golden.CandidateError("Running preview server lacks required bounded admission")
    # Use credentials in the actual API mount, not operator-side host reads.
    # Neither passwords nor error bodies cross the private subprocess boundary.
    compose(
        config,
        "exec",
        "-T",
        "phlo-api",
        "python",
        "-c",
        """
import json,ssl,sys,time
from pathlib import Path
import httpx
env=sys.argv[1]; root=Path('/opt/phlo-preview/api-secrets')
assert (root/('password-'+env)).stat().st_mode & 0o077 == 0
with httpx.Client(verify=ssl.create_default_context(cafile=str(root/'ca.pem')),
        auth=('phlo_api_preview_'+env,(root/('password-'+env)).read_text()),
        follow_redirects=False,timeout=30) as client:
    def query(sql):
        response=client.post('https://trino-preview:8443/v1/statement',content=sql,
            headers={'X-Trino-User':'phlo_api_preview_'+env,'X-Trino-Catalog':'acceptance_preview'})
        response.raise_for_status(); page=response.json(); rows=[]
        for _ in range(120):
            rows.extend(page.get('data',[]))
            if 'nextUri' not in page: return page,rows
            time.sleep(0.1); response=client.get(page['nextUri']); response.raise_for_status(); page=response.json()
        raise AssertionError('Preview query exceeded bounded polling')
    page,rows=query('SELECT id,label,active FROM release_acceptance.typed ORDER BY id')
    assert 'error' not in page and rows==[[7,'retained',True],[19,'synthetic',False]]
    for sql in ('DELETE FROM release_acceptance.typed',
                "SET SESSION query_max_run_time = '1h'",'SELECT * FROM system.runtime.nodes'):
        page,_=query(sql)
        assert page.get('error',{}).get('errorName') in ('PERMISSION_DENIED','ACCESS_DENIED')
""",
        binding["env"],
    )
    installed(
        config,
        """
import yaml
from pathlib import Path
path=Path.cwd()/'.phlo/overrides/compose.yaml'; document=yaml.safe_load(path.read_text())
settings=document['services']['phlo-api']['environment']
settings.update({'PHLO_V1_QUERY_SINGLE_REPLICA':'1','PHLO_V1_QUERY_SINGLE_PROCESS':'1',
                 'PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED':'1'})
path.write_text(yaml.safe_dump(document))
print('{}')
""",
    )
    return {
        "api_replicas": 1,
        "api_workers": 1,
        "private_mount_readable": True,
        "server_budgets_observed": True,
        "tls_read_and_server_denials": True,
    }


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


def database_snapshot(config: golden.RunConfig, database: str | None = None) -> dict:
    """Hash all synthetic table rows without returning credentials or row content."""
    address = urllib.parse.urlsplit(golden.service_url(config, "postgres", 5432))
    return installed(
        config,
        """
import json, sys, psycopg2
from pathlib import Path
from psycopg2 import sql
from phlo.config.layout import project_env_paths
from phlo.infrastructure.utils import parse_env_file
values={}
for path in project_env_paths(Path.cwd()/'.phlo'):
    values.update(parse_env_file(path))
with psycopg2.connect(host=sys.argv[1],port=int(sys.argv[2]),
        user=values.get('POSTGRES_USER','phlo'),password=values['POSTGRES_PASSWORD'],
        dbname=sys.argv[3] or values.get('POSTGRES_DB','phlo')) as connection:
    connection.set_session(readonly=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2")
        tables=cursor.fetchall(); result={}
        for schema,table in tables:
            cursor.execute(sql.SQL("SELECT count(*),md5(coalesce(string_agg(row_to_json(t)::text,chr(10) ORDER BY row_to_json(t)::text),'')) FROM {}.{} t").format(sql.Identifier(schema),sql.Identifier(table)))
            count,digest=cursor.fetchone(); result[schema+'.'+table]={'rows':count,'digest':digest}
print(json.dumps(result))
""",
        address.hostname or "127.0.0.1",
        str(address.port),
        database or "",
    )


def verify_database_restore(config: golden.RunConfig, backup: Path) -> dict:
    """Restore the archive into a new owned database and compare every table."""
    before = database_snapshot(config)
    if not before:
        raise golden.CandidateError("Database backup contains no application tables to verify")
    restored = "phlo_acceptance_restore_" + secrets.token_hex(8)
    compose(
        config,
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'createdb -U "$POSTGRES_USER" "$1"',
        "sh",
        restored,
    )
    try:
        subprocess.run(
            golden.compose_command(
                config,
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$1"',
                "sh",
                restored,
            ),
            input=backup.read_bytes(),
            capture_output=True,
            check=True,
        )
        if database_snapshot(config, restored) != before:
            raise golden.CandidateError("Restored database does not match retained table rows")
        return {"restored_and_compared": True, "tables": len(before)}
    finally:
        compose(
            config,
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'dropdb -U "$POSTGRES_USER" "$1"',
            "sh",
            restored,
        )


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
        for route, title in ROUTES.items():
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
            _browser(session, "screenshot", str(artifacts / f"{route or 'overview'}.png"))
            if state.get("title", "").strip() != title or any(
                message in state.get("text", "").lower()
                for message in (
                    "internal server error",
                    "something went wrong",
                    "unauthorized",
                    "failed to load",
                    "couldn't load this page",
                    "sign in to access phlo",
                )
            ):
                raise golden.CandidateError(f"Packaged Observatory route failed: /{route}")
            visited.append(route or "overview")
        # The packaged UI initiates asynchronous branch detail requests. Require
        # their visible result, then advance both its links and the API cursor.
        _browser(session, "wait", '[aria-label="Commit history"]')
        _browser(session, "wait", "--text", "Ahead: 0")
        _browser(session, "screenshot", str(artifacts / "branches.png"))
        visible_hashes: set[str] = set()
        for page in range(3):
            _browser(session, "wait", "[data-commit-hash]")
            current = _browser(
                session,
                "eval",
                "[...document.querySelectorAll('[data-commit-hash]')].map(e=>e.dataset.commitHash)",
            ).get("result", [])
            if not current or visible_hashes.intersection(current):
                raise golden.CandidateError("Packaged branch history repeated or omitted commits")
            visible_hashes.update(current)
            _browser(session, "screenshot", str(artifacts / f"branch-history-{page + 1}.png"))
            if page < 2:
                _browser(
                    session,
                    "find",
                    "role",
                    "link",
                    "click",
                    "--name",
                    "Next history page",
                    "--exact",
                )
                # Wait for the asynchronous detail replacement, not just URL navigation.
                _browser(
                    session,
                    "wait",
                    "--fn",
                    "!document.querySelector('[data-commit-hash="
                    + json.dumps(current[0])
                    + "]') && !!document.querySelector('[data-commit-hash]')",
                )
        if len(visible_hashes) < SEED_COMMITS:
            raise golden.CandidateError("Packaged history did not traverse the seeded commits")
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
        table = _browser(
            session,
            "eval",
            "(() => { const t=document.querySelector('table[aria-label=\"Query results\"]'); "
            "return {headers:[...t.querySelectorAll('thead th')].map(c=>c.textContent.trim()),"
            "rows:[...t.querySelectorAll('tbody tr')].map(r=>[...r.querySelectorAll('td')].map(c=>c.textContent.trim()))}; })()",
        ).get("result", {})
        if table != {
            "headers": ["#", "id bigint", "label varchar", "active boolean"],
            "rows": [["1", "7", "retained", "true"], ["2", "19", "synthetic", "false"]],
        }:
            raise golden.CandidateError("Packaged query results do not display the seeded rows")
        _browser(session, "screenshot", str(artifacts / "query-result.png"))
        return {
            "routes": visited,
            "history": commits,
            "browser_history_commits": len(visible_hashes),
            "browser_cursor_advancements": 2,
            "query_rows_displayed": len(ROWS),
        }
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
    for name in ("orchestration", "incidents", "query_usage"):
        checks = report["capabilities"][name]
        if not checks or any(check["state"] != "ready" for check in checks):
            raise golden.CandidateError(f"Feature readiness failed: {name}")
    # The API intentionally never promotes preview configuration to deployed
    # enforcement proof. Query/denial/runtime checks must supply that evidence.
    preview = report["capabilities"]["query_preview"]
    if (
        not preview
        or any(check["state"] != "configured" for check in preview)
        or not any(check.get("reason_code") == "backend_reachable" for check in preview)
    ):
        raise golden.CandidateError("Feature readiness failed: query_preview")
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
            report = verify_readiness(api, binding)
            # A loaded Dagster code location alone does not prove its retained
            # database connections recovered after a PostgreSQL outage.
            verify_overview(api, binding)
            return report
        except (OSError, golden.CandidateError):
            time.sleep(1)
    raise golden.CandidateError("Feature readiness did not recover within 120 seconds")


def recover(config: golden.RunConfig, binding: dict, query_id: str) -> dict:
    api = golden.service_url(config, "phlo-api", 4000)
    identity = compose(config, "ps", "-q", "phlo-api", capture=True).stdout.strip()
    if not identity:
        raise golden.CandidateError("Recovery requires a running API container")
    compose(config, "stop", "postgres")
    try:
        request(f"{api}/health")
        report = request(
            f"{api}/api/v1/readiness?" + urllib.parse.urlencode({"env": binding["env"]}),
            expected=(200, 503),
        )
        authorization_unavailable = (
            report.get("error", {}).get("code") == "authorization_unavailable"
        )
        if authorization_unavailable:
            # Durable identity shares PostgreSQL. Keep HTTP fail-closed: do not
            # bypass authentication to expose readiness during that outage.
            # The operator separately observes the installed read-only storage
            # checks inside the unchanged API container, never a fixture result.
            probe = compose(
                config,
                "exec",
                "-T",
                "phlo-api",
                "python",
                "-c",
                "import contextlib,json,sys\n"
                "with contextlib.redirect_stdout(sys.stderr):\n"
                "    from phlo_api.api.v1_readiness import _storage_readiness\n"
                "    checks=_storage_readiness()\n"
                "print(json.dumps({'capabilities':{name:[item.model_dump() for item in items] for name,items in checks.items()}}))",
            )
            report = json.loads(probe.stdout)
        for name in ("incidents", "query_usage"):
            checks = report.get("capabilities", {}).get(name, [])
            if not checks or any(
                check["state"] not in {"unavailable", "degraded"}
                or check.get("reason_code") != "backend_unavailable"
                for check in checks
            ):
                raise golden.CandidateError(f"Database outage was not detected by {name}")
    finally:
        compose(config, "start", "postgres")
    wait_ready(api, binding)
    if compose(config, "ps", "-q", "phlo-api", capture=True).stdout.strip() != identity:
        raise golden.CandidateError("Database recovery replaced the API container")
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
        "public_authorization_failed_closed": authorization_unavailable,
        "storage_probe": "installed_read_only" if authorization_unavailable else "public_readiness",
        "durable_query_after_restart": True,
    }


def transition(
    previous: golden.RunConfig, candidate: golden.RunConfig, binding: dict, pristine: Path
) -> dict:
    """Apply a concrete candidate file plan to the *same* retained project."""
    retained = {
        path: path.read_bytes()
        for path in (
            *golden.project_env_paths(previous.project_dir / ".phlo"),
            previous.project_dir / "retained-jvm.config",
            previous.project_dir / "retained-dagster.yaml",
            previous.project_dir / ".phlo/retained-provider-note.txt",
        )
    }
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
    restore = verify_database_restore(previous, backup)
    golden.verify_candidate_bom(candidate)
    golden.install_operator_from_bom(candidate)
    # The acceptance operator explicitly configures the discovered real target.
    # Keep the old bytes, validate the new document before appending, and let
    # reviewed services upgrade own authentication, roles and generated files.
    private_file(
        candidate.project_dir.parent / "legacy-project.before.yaml",
        (candidate.project_dir / "phlo.yaml").read_text(),
    )
    installed(
        candidate,
        """
import json,sys,yaml
from pathlib import Path
from phlo.config_schema import ProjectConfig
path=Path.cwd()/'phlo.yaml'; text=path.read_text(); document=yaml.safe_load(text)
if any(document.get(name) for name in ('environments','authentication','api')):
    raise SystemExit('Existing legacy bindings/authentication require explicit operator reconciliation')
binding=json.loads(sys.argv[1]); name=binding.pop('env')
addition={'environments':{name:binding},'default_environment':name}
ProjectConfig.model_validate({**document,**addition})
path.write_text(text+chr(10)+yaml.safe_dump(addition))
print('{}')
""",
        json.dumps(binding),
    )
    plan_file = candidate.project_dir.parent / "reviewed-upgrade.json"
    golden.run(
        golden.command(
            str(candidate.operator_bin),
            "services",
            "upgrade",
            "--observatory-dev",
            "--previous-generated",
            str(pristine),
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
        "--observatory-dev",
        "--previous-generated",
        str(pristine),
        "--apply",
        str(plan_file),
        "--confirm",
        token,
    )
    golden.run(command, cwd=candidate.project_dir, capture_output=True)
    golden.run(command, cwd=candidate.project_dir, capture_output=True)
    if any(path.read_bytes() != content for path, content in retained.items()):
        raise golden.CandidateError("Upgrade changed retained customisation sources or credentials")
    installed(
        candidate,
        """
import json,yaml
from pathlib import Path
root=Path.cwd()
assert (root/'.phlo/trino/jvm.config').read_bytes()==(root/'retained-jvm.config').read_bytes()
assert yaml.safe_load((root/'.phlo/dagster/dagster.yaml').read_text())['telemetry']['enabled'] is False
assert 'postgres-data' in yaml.safe_load((root/'.phlo/docker-compose.yml').read_text())['volumes']
print('{}')
""",
    )
    installed_start_pins(previous, remove=True)
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
    state = json.loads(migration.stdout)
    if migration.returncode == 1 and state == {
        "migration": "004_configurable_environments",
        "state": "required",
    }:
        # Archive restore and writer shutdown above are mandatory preconditions.
        # The installed provider owns the transaction, constraints and validation.
        compose(
            candidate,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "phlo-api",
            "python",
            "-m",
            "phlo_api.migrations",
            "--apply",
            "--backup-confirmed",
            "--json",
            capture=True,
        )
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
        "database_restore": restore,
        "retained_project": True,
        "schema": "compatible",
    }


def installed_start_pins(config: golden.RunConfig, *, remove: bool = False) -> None:
    """Put verified pins in the ordinary CLI's final layer, disabling builds.

    This layer belongs only to the uniquely owned acceptance project. A user
    layer is never overwritten, including while switching candidate artifacts.
    """
    layer = config.project_dir / ".phlo/compose.local.yaml"
    if remove and not layer.exists():
        return
    pins = json.loads((config.project_dir / ".phlo/compose.candidate.json").read_text())["services"]
    assert config.bom is not None
    allowed = {f"{item['name']}@{item['digest']}" for item in config.bom["artifacts"]}
    lines = ["# Owned installed-consumer acceptance pins", "services:"]
    for name, definition in sorted(pins.items()):
        image_name, _, digest = bom_module.parse_image_reference(definition["image"])
        if digest is None or f"{image_name}@{digest}" not in allowed:
            raise golden.CandidateError("Installed startup image is absent from the exact BOM")
        lines += [
            f"  {json.dumps(name)}:",
            f"    image: {json.dumps(definition['image'])}",
            "    build: !reset null",
        ]
    content = "\n".join(lines) + "\n"
    if layer.exists() and layer.read_text() != content:
        raise golden.CandidateError("Refusing to replace a non-owned local Compose layer")
    if remove:
        layer.unlink()
    elif not layer.exists():
        private_file(layer, content)


def start(config: golden.RunConfig, *, legacy: bool = False) -> None:
    installed_start_pins(config)
    document = golden.compose_config_json(config)
    if any(definition.get("build") for definition in document["services"].values()):
        raise golden.CandidateError("Installed startup must not have a build fallback")
    compose(config, "--profile", "api", "pull")
    golden.run(
        golden.command(str(config.operator_bin), "services", "start"),
        cwd=config.project_dir,
        capture_output=True,
    )
    if legacy:
        # The previous CLI's --profile targets only that profile, not the
        # default data stack. Both invocations are required by its contract.
        golden.run(
            golden.command(str(config.operator_bin), "services", "start", "--profile", "api"),
            cwd=config.project_dir,
            capture_output=True,
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
    pristine = config.project_dir.parent / (config.project_name + "-previous-generated")
    if candidate is not None:
        shutil.copytree(
            config.project_dir, pristine, ignore=shutil.ignore_patterns(".git", ".venv")
        )
        metadata = installed(
            config,
            "import json; from importlib.metadata import distributions; print(json.dumps({d.metadata['Name']:d.version for d in distributions() if d.metadata['Name'].lower()=='phlo' or d.metadata['Name'].lower().startswith('phlo-')}))",
        )
        baseline = pristine / ".phlo/upgrade-baseline.json"
        if not baseline.exists():
            private_file(baseline, json.dumps({"files": {}, "distributions": metadata}))
    result = {}
    try:
        verify_ports(config)
        start(config, legacy=candidate is not None)
        binding = legacy_target(config) if candidate is not None else target(config)
        result["seed"] = seed(config, binding)
        if candidate is not None:
            result["transition"] = transition(config, candidate, binding, pristine)
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
        result["preview_enforcement"] = verify_preview_runtime(config, binding)
        start(config)
        api = golden.service_url(config, "phlo-api", 4000)
        result["readiness"] = wait_ready(api, binding)
        result["overview"] = verify_overview(api, binding)
        result["browser"] = browser_journey(config, binding, artifacts)
        result["query"] = query_contract(api, binding)
        result["recovery"] = recover(config, binding, result["query"]["query_id"])
        from release_observatory_oidc import run as oidc_journey

        assert config.bom is not None
        proxy = [
            item
            for item in config.bom["artifacts"]
            if item["kind"] == bom_module.KIND_PROVIDER_IMAGE
            and item["name"] == "quay.io/oauth2-proxy/oauth2-proxy"
        ]
        if len(proxy) != 1:
            raise golden.CandidateError("Authenticated journey requires the exact BOM proxy image")
        result["authenticated"] = oidc_journey(
            config, binding, artifacts, f"{proxy[0]['name']}@{proxy[0]['digest']}"
        )
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
    for executable in ("agent-browser", "openssl", "docker", "certutil"):
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
        project_dir=temporary / f"{config.project_name}-fresh",
        operator_env=temporary / "fresh-operator",
        project_name=f"{config.project_name}-fresh",
    )
    old = replace(
        config,
        project_dir=temporary / f"{config.project_name}-upgrade",
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
