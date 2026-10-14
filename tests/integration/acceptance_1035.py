"""Hold the existing disposable Trino integration stack for browser acceptance.

Run with the repository virtualenv under an orb supervised service. Uses the
existing static authentication provider and disposable admin policy, not OIDC.
Stop by creating /tmp/phlo-1035-stop, so container context managers clean up.
"""

import importlib.util
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import docker
import httpx
import pytest
import uvicorn
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[2]
STATE = Path("/tmp/phlo-1035-state.json")
STOP = Path("/tmp/phlo-1035-stop")


class StablePortsContainer(DockerContainer):
    """Keep provider URLs stable across real stop/start outage exercises."""

    def with_exposed_ports(self, *ports):
        bindings = {19120: 3140, 9000: 3141, 8080: 3142, 8443: 3143}
        for port in ports:
            self.with_bind_ports(port, bindings[port])
        return self


def workspace(monkeypatch, tmp_path, port, descriptors, events):
    targets = {"prod": ("prod", "main"), "staging": ("stage", "candidate")}
    settings = {
        "PHLO_PROJECT_PATH": str(tmp_path),
        "PHLO_QUERY_ENGINE_URL": f"https://127.0.0.1:{port}",
        "PHLO_V1_QUERY_SINGLE_REPLICA": "1",
        "PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED": "1",
        "PHLO_API_OPERATION_CONTROLS_NAMESPACE": "disposable-1035",
        "PHLO_ENVIRONMENT": "development",
        "PHLO_AUTHORIZATION_MODE": "optional",
        "PHLO_AUTHENTICATION_PROVIDER": "static",
        "PHLO_AUTH_STATIC_ENABLED": "1",
        "PHLO_AUTH_DEV_MODE": "0",
        "PHLO_AUTH_STATIC_USERS": json.dumps(
            {
                "acceptance-alice": {"subject": "alice", "groups": ["admin"]},
                "acceptance-listener": {
                    "subject": "trino_listener",
                    "principal_type": "service",
                    "groups": ["admin"],
                },
            }
        ),
        "DAGSTER_GRAPHQL_URL": "http://127.0.0.1:3137/graphql",
        "PHLO_V1_ENVIRONMENTS": json.dumps(
            {env: {"dagster_location": env, "nessie_ref": ref} for env, (_, ref) in targets.items()}
        ),
        "PHLO_V1_PREVIEW_CATALOGS": json.dumps(
            {
                env: {"catalog": f"usage_{key}", "nessie_ref": ref}
                for env, (key, ref) in targets.items()
            }
        ),
        "PHLO_V1_USAGE_TRINO_SOURCES": json.dumps(
            {
                "cluster": {
                    "service_subject": "trino_listener",
                    "catalogs": {
                        env: {"catalog": f"usage_{key}", "properties": descriptors[key]}
                        for env, (key, _) in targets.items()
                    },
                }
            }
        ),
    }
    for env in ("PROD", "STAGING"):
        settings[f"PHLO_V1_PREVIEW_TRINO_PASSWORD_{env}"] = "disposable-test"
    for key, value in settings.items():
        monkeypatch.setenv(key, value)

    authorization = tmp_path / ".phlo" / "authorization"
    authorization.mkdir(parents=True)
    (authorization / "roles.yaml").write_text(
        "roles: {admin: {}}\nsubjects:\n  users: {alice: [admin]}\n"
        "  services: {trino_listener: [admin]}\n"
    )
    (authorization / "policies.yaml").write_text(
        "policies:\n  - policy_id: disposable-acceptance\n    effect: allow\n"
        '    principal: {roles: [admin]}\n    action: "*"\n'
        '    resource: {type: "*", id_pattern: "*"}\n'
    )

    definitions = tmp_path / "assets.py"
    definitions.write_text(
        "from dagster import asset, Definitions\n"
        '@asset(key=["warehouse", "orders"], metadata={"phlo/relation": "warehouse.orders"})\n'
        "def orders():\n    return None\n"
        "defs = Definitions(assets=[orders])\n"
    )
    workspace_file = tmp_path / "workspace.yaml"
    workspace_file.write_text(
        "load_from:\n"
        + "".join(
            f"  - python_file:\n      relative_path: {definitions}\n      location_name: {env}\n"
            for env in targets
        )
    )
    subprocess.run(
        [
            "amp",
            "orb",
            "service",
            "start",
            "acceptance-1035-dagster",
            "--command",
            f"{ROOT}/.venv/bin/dagster-webserver -w {workspace_file} -h 127.0.0.1 -p 3137",
        ],
        check=True,
    )

    with PostgresContainer("postgres:18-alpine", password="test").with_bind_ports(
        5432, 3144
    ) as postgres:
        for setting in (
            "PHLO_RUN_EVIDENCE_DB_URL",
            "PHLO_OBSERVATORY_SETTINGS_DB_URL",
            "PHLO_API_OPERATION_CONTROLS_DB_URL",
        ):
            monkeypatch.setenv(setting, postgres.get_connection_url(driver=None))
        from phlo_api import incidents, usage
        from phlo_api.main import app

        incidents.initialize_incidents()
        usage.initialize_usage()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=3136))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("API failed to start")
            time.sleep(0.1)
        client = docker.from_env()
        containers = client.containers.list()
        owned_ports = {"3140", "3141", "3142", "3143", "3144"}
        STATE.write_text(
            json.dumps(
                {
                    "api": "http://127.0.0.1:3136",
                    "project": str(tmp_path),
                    "containers": {
                        c.image.tags[0]: c.id
                        for c in containers
                        if c.image.tags
                        and any(
                            binding["HostPort"] in owned_ports
                            for bindings in c.ports.values()
                            for binding in bindings or []
                        )
                    },
                },
                indent=2,
            )
        )
        seen = set()
        try:
            while not STOP.exists():
                for event in list(events):
                    query_id = event["metadata"]["queryId"]
                    if query_id in seen:
                        continue
                    response = httpx.post(
                        "http://127.0.0.1:3136/api/v1/trino/query-completed?source_id=cluster",
                        json=event,
                        headers={"Authorization": "Bearer acceptance-listener"},
                        timeout=10,
                    )
                    if response.status_code == 503:
                        break  # Retry the original listener event after storage recovery.
                    if response.is_error:
                        raise RuntimeError(
                            f"Listener ingestion {response.status_code}: {response.text}"
                        )
                    seen.add(query_id)
                    print(f"listener {query_id} {response.text}", flush=True)
                    with (tmp_path / "listener-events.jsonl").open("a") as evidence:
                        evidence.write(
                            json.dumps({"event": event, "ingestion": response.json()}) + "\n"
                        )
                time.sleep(0.2)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            subprocess.run(
                ["amp", "orb", "service", "stop", "acceptance-1035-dagster"], check=False
            )


if __name__ == "__main__":
    STOP.unlink(missing_ok=True)
    STATE.unlink(missing_ok=True)
    spec = importlib.util.spec_from_file_location(
        "usage_integration", ROOT / "packages/phlo-api/tests/test_usage_trino_integration.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__.update(DockerContainer=StablePortsContainer, _workspace_acceptance=workspace)
    with (
        tempfile.TemporaryDirectory(prefix="phlo-1035-") as directory,
        pytest.MonkeyPatch.context() as patch,
    ):
        module.test_trino_query_selected_catalog_version_proves_direct_nessie_ref(
            Path(directory), patch
        )
