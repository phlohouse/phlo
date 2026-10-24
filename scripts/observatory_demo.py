#!/usr/bin/env python3
"""Run opt-in Mission Control acceptance on disposable, loopback-only services.

Builds this checkout's wheels and generated images, grants only the owned demo
identities, and checks real query, CSV and native candidate results. Credentials
stay in private files. Cleanup removes only this run's services and volumes.
This source-build journey is not released-artifact or production qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import release_golden_path as golden
import release_observatory_acceptance as acceptance
import yaml


class DemoClient:
    """Call only this run's loopback API without emitting credentials or error bodies."""

    def __init__(self, config: golden.RunConfig, token: str):
        self.url = golden.service_url(config, "phlo-api", 4000) + "/api/v1/"
        self.token = token

    def request(
        self,
        path: str,
        payload=None,
        *,
        raw: bytes | None = None,
        expected: tuple[int, ...] = (200,),
        headers: dict | None = None,
        method: str | None = None,
    ) -> dict:
        suffix = "&" if "?" in path else "?"
        outgoing = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if raw is not None:
            outgoing.update({"Content-Type": "text/csv", "X-Phlo-Upload-Name": "stability.csv"})
        outgoing.update(headers or {})
        data = (
            raw
            if raw is not None
            else json.dumps(payload).encode()
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            self.url + path + suffix + "env=sandbox",
            data=data,
            headers=outgoing,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                status, body = response.status, response.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read(8 * 1024 * 1024)
        if status not in expected:
            raise golden.CandidateError(f"{path}: HTTP {status}, expected {expected}")
        return json.loads(body) if body else {}

    def query(self, sql: str) -> dict:
        query = self.request("queries", {"sql": sql, "row_limit": 25}, expected=(202,))
        for _ in range(300):
            query = self.request("queries/" + query["id"])
            if query["status"] == "completed":
                return query
            if query["status"] in {"failed", "cancelled"}:
                raise golden.CandidateError("Demo query did not complete successfully")
            time.sleep(0.1)
        raise golden.CandidateError("Demo query exceeded bounded polling")


def prepare(config: golden.RunConfig, binary: Path, digest: str) -> tuple[str, str]:
    """Install this checkout and configure only the newly scaffolded private project."""
    golden.build_wheelhouse(config)
    golden.install_operator(config)
    golden.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(config.operator_python),
            "--constraint",
            str(config.wheelhouse / "constraints.txt"),
            "--find-links",
            str(config.wheelhouse),
            "phlo-iceberg",
            "phlo-transform",
        ],
        cwd=config.repo_root,
    )
    golden.force_local_install(
        config,
        config.operator_python,
        "phlo",
        "phlo-api",
        "phlo-dlt",
        "phlo-iceberg",
        "phlo-transform",
        "phlo-trino",
        "phlo-postgres",
        "phlo-minio",
        "phlo-nessie",
        "phlo-core-plugins",
    )
    golden.create_project(config)
    golden.configure_non_dev_compose(config)
    root = config.project_dir
    example = config.repo_root / "examples/lakehouses/mission-control"
    shutil.rmtree(root / "workflows")
    for name in ("workflows", "transforms"):
        shutil.copytree(example / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    golden.run(
        [
            str(config.operator_python),
            str(example / "scripts/generate_fixtures.py"),
            "--output",
            str(root),
            "--as-of",
            "2026-10-22T18:00:00+00:00",
        ],
        cwd=root,
    )
    target = root / ".phlo/bin/phlo-transform"
    target.parent.mkdir()
    shutil.copyfile(binary, target)
    target.chmod(0o755)
    versions = acceptance.installed(
        config,
        "import json;from importlib.metadata import version;"
        "print(json.dumps({name:version(name) for name in ['phlo-api','phlo-dagster','phlo-dbt']}))",
    )
    document = yaml.safe_load((example / "phlo.yaml").read_text())
    document.update(
        authentication={"provider": "service_token"},
        services={
            "enabled": [
                "postgres",
                "minio",
                "nessie",
                "trino",
                "dagster",
                "dagster-daemon",
                "phlo-api",
            ]
        },
    )
    document["environments"]["sandbox"]["compose_project"] = config.project_name
    document["env"].update(
        {
            "PHLO_API_EXTRAS": "dagster,iceberg,dlt,trino,transform",
            "PHLO_API_VERSION": versions["phlo-api"],
            "PHLO_DAGSTER_VERSION": versions["phlo-dagster"],
            "PHLO_DBT_VERSION": versions["phlo-dbt"],
            "PHLO_UV_LOCKED": "false",
            "PHLO_TRANSFORM_BINARY": "bin/phlo-transform",
            "PHLO_TRANSFORM_SHA256": digest,
            "PHLO_V1_ACTIONS_SINGLE_REPLICA": "1",
            "PHLO_V1_ACTIONS_SINGLE_PROCESS": "1",
            "PHLO_V1_ACTIONS_REF_TAG_CONTRACT": "1",
        }
    )
    (root / "phlo.yaml").write_text(yaml.safe_dump(document))
    wheel = next(config.wheelhouse.glob("phlo_transform-*.whl"))
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text().replace(
            '    "phlo-pandera",',
            f'    "phlo-pandera",\n    "phlo-transform @ file:///opt/phlo-wheelhouse/{wheel.name}",',
        )
    )
    policy = root / ".phlo/authorization"
    policy.mkdir(exist_ok=True)
    (policy / "roles.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "roles": {
                    "admin": {"description": "Disposable demo operator"},
                    "viewer": {"description": "Disposable demo reader; no query or mutation"},
                },
            }
        )
    )
    (policy / "policies.yaml").write_text(
        yaml.safe_dump(
            {
                "policies": [
                    {
                        "policy_id": "disposable-demo-operator",
                        "effect": "allow",
                        "principal": {"roles": ["admin"]},
                        "action": "*",
                        "resource": {"type": "*", "id_pattern": "*"},
                    },
                    {
                        "policy_id": "disposable-demo-viewer",
                        "effect": "allow",
                        "principal": {"roles": ["viewer"]},
                        "action": "asset.read",
                        "resource": {"type": "asset", "id_pattern": "env=sandbox*"},
                    },
                ]
            }
        )
    )
    operator, viewer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    credentials = {
        operator: {
            "subject": "demo-operator",
            "groups": ["admin"],
            "claims": {"scopes": ["admin"]},
        },
        viewer: {"subject": "demo-viewer", "groups": ["viewer"], "claims": {"scopes": []}},
    }
    path = golden.env_secrets_path(root / ".phlo")
    lines = [
        line
        for line in path.read_text().splitlines()
        if not line.startswith("PHLO_AUTH_SERVICE_TOKENS=")
    ]
    lines.append("PHLO_AUTH_SERVICE_TOKENS=" + json.dumps(credentials))
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    acceptance.private_file(
        root / ".phlo/demo-credentials.json", json.dumps({"operator": operator, "viewer": viewer})
    )
    golden.run(
        [str(config.operator_bin), "services", "init", "--no-dev", "--profile", "api", "--force"],
        cwd=root,
    )
    loopback_ports(config)
    return operator, viewer


def loopback_ports(config: golden.RunConfig) -> None:
    """Replace published ports in this generated disposable Compose before startup."""
    document = yaml.safe_load(config.compose_file.read_text())
    for service in document["services"].values():
        service["ports"] = [
            {
                "target": int(str(port).rsplit(":", 1)[-1].split("/")[0]),
                "published": "0",
                "host_ip": "127.0.0.1",
            }
            for port in service.get("ports", [])
        ]
    config.compose_file.write_text(yaml.safe_dump(document))
    verify_loopback(config)


def verify_loopback(config: golden.RunConfig) -> None:
    """Reject any generated or override service exposing non-loopback host ports."""
    for service in golden.compose_config_json(config)["services"].values():
        if service.get("network_mode") == "host" or any(
            port.get("host_ip") != "127.0.0.1" for port in service.get("ports", [])
        ):
            raise golden.CandidateError("Demo requires loopback-only published ports")


def fixture_run(config: golden.RunConfig) -> str:
    """Launch and await the real fourteen-table fixture job with its actual origin."""
    location, repository = golden.discover_dagster_selector(config, "")
    url = golden.service_url(config, "dagster", 3000, "/graphql")
    result = golden.graphql(
        url,
        """mutation Run($params:ExecutionParams!){launchRun(executionParams:$params){
        __typename ... on LaunchRunSuccess{run{runId}}
        ... on PythonError{message stack}}}""",
        {
            "params": {
                "selector": {
                    "repositoryLocationName": location,
                    "repositoryName": repository,
                    "pipelineName": "mission_control_full",
                },
                "runConfigData": {},
                "executionMetadata": {"tags": [{"key": "phlo/ref", "value": "main"}]},
            }
        },
        "",
    )
    launched = result["data"]["launchRun"]
    if launched["__typename"] != "LaunchRunSuccess":
        raise golden.CandidateError(f"Fixture job was not launched: {json.dumps(launched)}")
    run_id = launched["run"]["runId"]
    for _ in range(180):
        result = golden.graphql(
            url,
            "query Run($id:ID!){runOrError(runId:$id){__typename ... on Run{status}}}",
            {"id": run_id},
            "",
        )
        status = result["data"]["runOrError"].get("status")
        if status == "SUCCESS":
            return run_id
        if status in {"FAILURE", "CANCELED"}:
            raise golden.CandidateError("Fixture materialization failed; no acceptance claimed")
        time.sleep(2)
    raise golden.CandidateError("Fixture run exceeded bounded polling")


def journeys(config: golden.RunConfig, client: DemoClient, viewer: str) -> dict:
    """Verify real source data and isolated candidates without changing main or SQL."""
    denied = DemoClient(config, viewer)
    denied.request("queries", {"sql": "SELECT 1", "row_limit": 1}, expected=(403,))
    query = client.query(
        "SELECT count(*) AS batches, sum(results_held) AS held FROM gold.batch_release_metrics"
    )
    if query["result"]["rows"] != [{"batches": 24, "held": 37}]:
        raise golden.CandidateError("Mission Control source-backed query results differ")
    nessie = golden.service_url(config, "nessie", 19120) + "/api/v1/trees/tree/main"
    base = acceptance.request(nessie)["hash"]
    upload = client.request(
        "uploads/csv",
        raw=(config.project_dir / "data/stability_CGT-04_T6M.csv").read_bytes(),
        expected=(201,),
    )
    path = "uploads/csv/" + upload["upload_id"]
    columns = upload["columns"]
    for column in columns:
        if column["name"] == "pull_date":
            column.update(type="date", date_format="day_first")
        if column["name"] == "result":
            column["qualifiers"] = {"value_name": "result_value", "flag_name": "result_flag"}
    review = client.request(
        path + "/columns",
        {"expected_version": upload["version"], "columns": columns},
        method="PUT",
    )
    client.request(
        path + "/destination", {"expected_version": review["version"], "table": "stability_demo"}
    )
    plan = client.request(path + "/candidate/plan", {})
    client.request(
        path + "/candidate/load",
        {
            "expected_version": plan["review_version"],
            "recipe_digest": plan["recipe_digest"],
            "plan_hash": plan["plan_hash"],
            "confirmed": True,
            "idempotency_key": "demo-" + upload["upload_id"],
        },
        expected=(200, 202),
    )
    for _ in range(180):
        preview = client.request(path + "/candidate/preview", expected=(200, 409, 503))
        if "rows" in preview:
            break
        time.sleep(2)
    else:
        raise golden.CandidateError("CSV candidate did not produce bounded snapshot readback")
    if (
        preview["row_count"] != 1284
        or not preview["receipt"]["main_unchanged"]
        or not preview["snapshot_id"]
    ):
        raise golden.CandidateError("CSV candidate snapshot evidence differs")
    model = "transform/models/review.batch_release_inspection"
    source = client.request(model + "/source")
    draft = client.request(
        model + "/drafts",
        {
            "sql": source["sql"] + "\n-- Immutable disposable review\n",
            "source_digest": source["source_digest"],
            "workspace_digest": source["workspace_digest"],
            "expected_revision": 0,
        },
        expected=(201,),
    )
    revision = model + "/drafts/" + draft["draft_id"] + "/1"
    compiled = client.request(revision + "/compile", {})
    if compiled["status"] != "ready":
        raise golden.CandidateError("Native draft did not compile against the real preview")
    execution = client.request(
        revision + "/executions",
        {"revision_digest": draft["revision_digest"], "base_hash": base},
        headers={"Idempotency-Key": "demo-native-" + draft["draft_id"]},
        expected=(201,),
    )
    detail = client.request(revision + "/executions/" + execution["execution_id"])
    if (
        detail["status"] != "passed"
        or not detail["candidate_snapshot_id"]
        or detail["preview"]["row_count"] != 24
    ):
        raise golden.CandidateError("Native candidate lacks its actual table snapshot/readback")
    if (
        acceptance.request(nessie)["hash"] != base
        or client.request(model + "/source")["sql"] != source["sql"]
    ):
        raise golden.CandidateError("Candidate journey changed main or workspace SQL")
    return {
        "query": query,
        "csv": preview,
        "native": detail,
        "denied_viewer": True,
        "main_unchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-disposable",
        action="store_true",
        help="Explicitly allow local image builds, credentials and fixture writes",
    )
    parser.add_argument(
        "--native-binary",
        type=Path,
        required=True,
        help="Compatible Linux GNU phlo-transform engine",
    )
    parser.add_argument("--native-sha256", required=True)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Credential-free JSON evidence outside temporary runtime",
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Keep private files and loopback services for local inspection",
    )
    args = parser.parse_args(argv)
    if not args.run_disposable:
        parser.error("--run-disposable is required; normal onboarding grants are never changed")
    binary = args.native_binary.resolve()
    if (
        not binary.is_file()
        or hashlib.sha256(binary.read_bytes()).hexdigest() != args.native_sha256
    ):
        parser.error("Native binary is missing or its SHA256 differs")
    golden.run([str(binary), "--version"], cwd=Path.cwd())
    golden.run(["docker", "info"], cwd=Path.cwd(), capture_output=True)
    root = Path(tempfile.mkdtemp(prefix="phlo-observatory-demo-"))
    root.chmod(0o700)
    name = golden.project_name()
    config = golden.RunConfig(
        repo_root=Path(__file__).resolve().parents[1],
        project_dir=root / name,
        wheelhouse=root / "wheelhouse",
        operator_env=root / "operator",
        project_name=name,
    )
    report = {
        "qualification": "disposable source-build demo, not production or released-artifact acceptance",
        "result": "failed",
        "stage": "bootstrap",
    }
    try:
        operator, viewer = prepare(config, binary, args.native_sha256)
        acceptance.compose(config, "build", "phlo-api", "dagster")
        definition = golden.compose_config_json(config)
        inputs = acceptance.preview_config(
            config,
            {"env": "sandbox", "nessie_ref": "main"},
            next(iter(definition["networks"].values()))["name"],
        )
        output = inputs.parent / "generated"
        golden.run(
            [
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
            ],
            cwd=config.project_dir,
        )
        acceptance.preview_layer(config, output, definition["services"]["phlo-api"])
        layer = config.project_dir / ".phlo/overrides/compose.yaml"
        document = yaml.safe_load(layer.read_text())
        document["services"]["phlo-api"]["environment"].update(
            {
                "PHLO_TRANSFORM_ADAPTER": "trino",
                "PHLO_TRANSFORM_TRINO_ENDPOINT": "https://trino-preview:8443",
                "PHLO_TRANSFORM_TRINO_CATALOG": "acceptance_preview",
                "PHLO_TRANSFORM_TRINO_USER": "phlo_api_preview_sandbox",
                "PHLO_TRANSFORM_TRINO_PASSWORD": (inputs.parent / "password").read_text(),
            }
        )
        layer.write_text(yaml.safe_dump(document))
        verify_loopback(config)
        acceptance.probe_private_mounts(config, {"env": "sandbox"})
        report["stage"] = "service-startup"
        acceptance.compose(
            config,
            "up",
            "--detach",
            "--no-build",
            "--wait",
            "postgres",
            "minio",
            "nessie",
            "trino",
            "dagster",
            "dagster-daemon",
            "phlo-api",
        )
        acceptance.compose(config, "run", "--rm", "--no-deps", "-T", "minio-setup")
        # Private staging already completed above. Do not rerun its destructive
        # volume refresh after Docker creates the coordinator's nested mounts.
        acceptance.compose(
            config, "up", "--detach", "--no-build", "--no-deps", "--wait", "trino-preview"
        )
        report["stage"] = "fixture-materialization"
        run_id = fixture_run(config)
        report["fixture_run_id"] = run_id
        report["stage"] = "preview-controls"
        acceptance.trino(config, "CREATE SCHEMA iceberg.release_acceptance")
        acceptance.trino(
            config,
            "CREATE TABLE iceberg.release_acceptance.typed AS SELECT * FROM (VALUES (7,'retained',true),(19,'synthetic',false)) AS t(id,label,active)",
        )
        report["preview_controls"] = acceptance.verify_preview_runtime(config, {"env": "sandbox"})
        acceptance.compose(config, "up", "--detach", "--no-build", "--wait", "phlo-api")
        report["stage"] = "query-csv-native-journeys"
        report.update(
            journeys(config, DemoClient(config, operator), viewer),
            result="passed",
            stage="completed",
        )
        print(
            "PASS Mission Control: real fixture run/query, denied viewer, CSV/native candidate snapshots; main unchanged"
        )
        return 0
    except (subprocess.CalledProcessError, RuntimeError) as error:
        if args.keep_project:
            diagnostics = root / "failure.log"
            detail = (
                (error.stdout or "") + (error.stderr or "")
                if isinstance(error, subprocess.CalledProcessError)
                else str(error)
            )
            acceptance.private_file(diagnostics, detail)
            report["private_diagnostics"] = str(diagnostics)
        raise
    finally:
        report["project_retained"] = args.keep_project
        if args.keep_project:
            report["project_path"] = str(config.project_dir)
            report["cleanup_command"] = golden.compose_command(
                config, "--profile", "api", "down", "--volumes", "--remove-orphans"
            )
            print(f"Private disposable project retained: {config.project_dir}")
        else:
            errors = golden.cleanup(config, owned_paths={root})
            report["cleanup"] = (
                "failed" if errors else "removed owned services, volumes and private files"
            )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        if not args.keep_project and errors:
            raise golden.CandidateError("Owned demo cleanup failed; inspect the private runtime")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        golden.CandidateError,
        subprocess.CalledProcessError,
        urllib.error.URLError,
        RuntimeError,
    ) as error:
        print(
            f"Disposable demo failed: {type(error).__name__}; inspect private runtime diagnostics, not credentials",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
