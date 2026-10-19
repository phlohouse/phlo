"""Real Trino 483 and Nessie ref binding for observed query inputs."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from trino.dbapi import connect
from trino.exceptions import TrinoQueryError

from phlo_api.api.v1_query_sql import bound_workspace_query
from phlo_api.usage import _completed_event, catalog_version


def _ready(url: str, *, trino: bool = False) -> None:
    for _ in range(90):
        try:
            response = httpx.get(url, timeout=2)
            if response.is_success and (not trino or not response.json()["starting"]):
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise AssertionError(f"Disposable service did not start: {url}")


@pytest.mark.integration
def test_trino_query_selected_catalog_version_proves_direct_nessie_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict] = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            events.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with Network() as network:
            with (
                DockerContainer("ghcr.io/projectnessie/nessie:0.108.3")
                .with_network(network)
                .with_network_aliases("nessie")
                .with_env("NESSIE_VERSION_STORE_TYPE", "IN_MEMORY")
                .with_exposed_ports(19120)
            ) as nessie:
                nessie_url = f"http://localhost:{nessie.get_exposed_port(19120)}"
                _ready(nessie_url + "/api/v2/config")
                main = httpx.get(nessie_url + "/api/v1/trees/tree/main").json()
                created = httpx.post(
                    nessie_url + "/api/v1/trees/tree",
                    json={"type": "BRANCH", "name": "candidate", "hash": main["hash"]},
                )
                assert created.is_success
                with (
                    DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.1")
                    .with_network(network)
                    .with_network_aliases("minio")
                    .with_env("MINIO_ROOT_USER", "localtest")
                    .with_env("MINIO_ROOT_PASSWORD", "localpass123")
                    .with_command("server /bitnami/minio/data")
                    .with_exposed_ports(9000)
                ) as minio:
                    _ready(f"http://localhost:{minio.get_exposed_port(9000)}/minio/health/ready")
                    bucket = minio.exec(
                        [
                            "sh",
                            "-c",
                            "mc alias set test http://127.0.0.1:9000 localtest localpass123 >/dev/null "
                            "&& mc mb test/lake",
                        ]
                    )
                    assert bucket.exit_code == 0
                    (tmp_path / "catalog").mkdir()
                    certificate = tmp_path / "server.crt"
                    key = tmp_path / "server.key"
                    subprocess.run(
                        [
                            "openssl",
                            "req",
                            "-x509",
                            "-newkey",
                            "rsa:2048",
                            "-nodes",
                            "-keyout",
                            str(key),
                            "-out",
                            str(certificate),
                            "-days",
                            "1",
                            "-subj",
                            "/CN=localhost",
                            "-addext",
                            "subjectAltName=DNS:localhost,IP:127.0.0.1",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    pem = tmp_path / "server.pem"
                    pem.write_bytes(key.read_bytes() + certificate.read_bytes())
                    passwords = tmp_path / "password.db"
                    password_hash = "$2b$12$UpBcGoQGKhrMHlVXf1jc9eqGr50iY9hHYkGfl0FQLHEnSkkER7za2"
                    passwords.write_text(
                        "".join(
                            f"phlo_api_preview_{env}:{password_hash}\n"
                            for env in ("prod", "staging")
                        )
                    )
                    authenticator = tmp_path / "password-authenticator.properties"
                    authenticator.write_text(
                        "password-authenticator.name=file\nfile.password-file=/etc/trino/password.db\n"
                    )
                    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
                    config = tmp_path / "config.properties"
                    config.write_text(
                        "coordinator=true\nnode-scheduler.include-coordinator=true\n"
                        "http-server.http.port=8080\ndiscovery.uri=http://localhost:8080\n"
                        "http-server.https.enabled=true\nhttp-server.https.port=8443\n"
                        "http-server.https.keystore.path=/etc/trino/server.pem\n"
                        "http-server.authentication.type=PASSWORD,INSECURE\n"
                        "http-server.authentication.allow-insecure-over-http=true\n"
                        "internal-communication.shared-secret=disposable-integration-only\n"
                        "catalog.management=dynamic\n"
                        "event-listener.config-files=/etc/trino/listener.properties\n",
                        encoding="utf-8",
                    )
                    listener = tmp_path / "listener.properties"
                    listener.write_text(
                        "event-listener.name=http\nhttp-event-listener.log-completed=true\n"
                        f"http-event-listener.connect-ingest-uri=http://host.docker.internal:{server.server_port}/events\n",
                        encoding="utf-8",
                    )
                    access_control = tmp_path / "access-control.json"
                    access_control.write_text(
                        json.dumps(
                            {
                                "catalogs": [
                                    {
                                        "user": "phlo_api_preview_prod",
                                        "catalog": "usage_prod",
                                        "allow": "read-only",
                                    },
                                    {
                                        "user": "phlo_api_preview_staging",
                                        "catalog": "usage_stage",
                                        "allow": "read-only",
                                    },
                                    {
                                        "user": "phlo_api_preview_(prod|staging)",
                                        "catalog": ".*",
                                        "allow": "none",
                                    },
                                    {"catalog": ".*", "allow": "all"},
                                ],
                                "system_session_properties": [
                                    {"user": "phlo_api_preview_(prod|staging)", "allow": False},
                                    {"allow": True},
                                ],
                                "queries": [
                                    {
                                        "user": "phlo_api_preview_(prod|staging)",
                                        "allow": ["execute"],
                                    },
                                    {"allow": ["execute"]},
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    access_properties = tmp_path / "access-control.properties"
                    access_properties.write_text(
                        "access-control.name=file\n"
                        "security.config-file=/etc/trino/preview/access-control.json\n",
                        encoding="utf-8",
                    )
                    descriptors = {}
                    for env, ref in (("prod", "main"), ("stage", "candidate")):
                        descriptor = {
                            "connector.name": "iceberg",
                            "iceberg.catalog.type": "nessie",
                            "iceberg.nessie-catalog.uri": "http://nessie:19120/api/v2",
                            "iceberg.nessie-catalog.ref": ref,
                            "iceberg.nessie-catalog.default-warehouse-dir": "s3://lake/warehouse",
                            "fs.s3.enabled": "true",
                            "s3.endpoint": "http://minio:9000",
                            "s3.path-style-access": "true",
                            "s3.region": "us-east-1",
                        }
                        descriptors[env] = descriptor
                        (tmp_path / "catalog" / f"usage_{env}.properties").write_text(
                            "".join(f"{key}={value}\n" for key, value in descriptor.items()),
                            encoding="utf-8",
                        )
                    with (
                        DockerContainer("trinodb/trino:483")
                        .with_network(network)
                        .with_network_aliases("trino")
                        .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
                        .with_env("AWS_ACCESS_KEY_ID", "localtest")
                        .with_env("AWS_SECRET_ACCESS_KEY", "localpass123")
                        .with_env("AWS_REGION", "us-east-1")
                        .with_volume_mapping(config, "/etc/trino/config.properties")
                        .with_volume_mapping(pem, "/etc/trino/server.pem")
                        .with_volume_mapping(passwords, "/etc/trino/password.db")
                        .with_volume_mapping(
                            authenticator, "/etc/trino/password-authenticator.properties"
                        )
                        .with_volume_mapping(listener, "/etc/trino/listener.properties")
                        .with_volume_mapping(
                            access_properties, "/etc/trino/access-control.properties"
                        )
                        .with_volume_mapping(
                            access_control, "/etc/trino/preview/access-control.json"
                        )
                        .with_volume_mapping(
                            tmp_path / "catalog" / "usage_prod.properties",
                            "/etc/trino/catalog/usage_prod.properties",
                        )
                        .with_volume_mapping(
                            tmp_path / "catalog" / "usage_stage.properties",
                            "/etc/trino/catalog/usage_stage.properties",
                        )
                        .with_exposed_ports(8080, 8443)
                    ) as trino:
                        port = trino.get_exposed_port(8080)
                        try:
                            _ready(f"http://localhost:{port}/v1/info", trino=True)
                        except AssertionError:
                            stdout, stderr = trino.get_logs()
                            raise AssertionError((stdout + stderr).decode()) from None
                        connection = connect(host="localhost", port=int(port), user="smoke")
                        query_ids = {}
                        for env, expected in (("prod", 3), ("stage", 7)):
                            cursor = connection.cursor()
                            cursor.execute(
                                f"CREATE SCHEMA usage_{env}.warehouse "
                                f"WITH (location = 's3://lake/warehouse/{env}')"
                            )
                            cursor.fetchall()
                            cursor.execute(f"CREATE TABLE usage_{env}.warehouse.orders (id bigint)")
                            cursor.fetchall()
                            cursor.execute(
                                f"INSERT INTO usage_{env}.warehouse.orders VALUES ({expected})"
                            )
                            cursor.fetchall()
                            cursor.execute(f"SELECT id FROM usage_{env}.warehouse.orders")
                            assert cursor.fetchall() == [[expected]]
                            bounded = bound_workspace_query(
                                f"SELECT id FROM usage_{env}.warehouse.orders ORDER BY id", 1
                            )
                            cursor.execute(bounded)
                            assert cursor.fetchall() == [[expected]]
                            query_ids[env] = cursor.query_id
                        for env, own_catalog, other_catalog, expected in (
                            ("prod", "usage_prod", "usage_stage", 3),
                            ("staging", "usage_stage", "usage_prod", 7),
                        ):
                            protected = connect(
                                host="localhost",
                                port=int(port),
                                user=f"phlo_api_preview_{env}",
                            )
                            cursor = protected.cursor()
                            cursor.execute(f"SELECT id FROM {own_catalog}.warehouse.orders")
                            assert cursor.fetchall() == [[expected]]
                            with pytest.raises(TrinoQueryError):
                                cursor.execute(f"SELECT id FROM {other_catalog}.warehouse.orders")
                            with pytest.raises(TrinoQueryError):
                                cursor.execute(
                                    f"INSERT INTO {own_catalog}.warehouse.orders VALUES (99)"
                                )
                            cursor.execute(f"SELECT id FROM {own_catalog}.warehouse.orders")
                            assert cursor.fetchall() == [[expected]]
                        for _ in range(50):
                            if all(
                                any(e["metadata"]["queryId"] == q for e in events)
                                for q in query_ids.values()
                            ):
                                break
                            time.sleep(0.1)
                        bindings = {
                            (f"usage_{env}", catalog_version(f"usage_{env}", descriptor)): (
                                "prod" if env == "prod" else "staging",
                                ref,
                            )
                            for env, ref, descriptor in (
                                ("prod", "main", descriptors["prod"]),
                                ("stage", "candidate", descriptors["stage"]),
                            )
                        }
                        for env, ref in (("prod", "main"), ("stage", "candidate")):
                            event = next(
                                e for e in events if e["metadata"]["queryId"] == query_ids[env]
                            )
                            query, at, status, matches = _completed_event(event, bindings)
                            assert query == query_ids[env]
                            assert isinstance(at, datetime) and at.tzinfo == UTC
                            assert status == "FINISHED"
                            assert matches == [
                                (
                                    f"usage_{env}",
                                    catalog_version(f"usage_{env}", descriptors[env]),
                                    "prod" if env == "prod" else "staging",
                                    ref,
                                    "warehouse/orders",
                                )
                            ]
                        _workspace_acceptance(
                            monkeypatch, tmp_path, trino.get_exposed_port(8443), descriptors, events
                        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _workspace_acceptance(monkeypatch, tmp_path, port, descriptors, events):
    """Compose the real provider with disposable durable workspace evidence."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from testcontainers.postgres import PostgresContainer

    from phlo.capabilities import AuthPrincipal
    from phlo_api import incidents, usage
    from phlo_api.api import v1_query
    from phlo_api.errors import PhloApiError, error_envelope

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_URL", f"https://127.0.0.1:{port}")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    for env in ("PROD", "STAGING"):
        monkeypatch.setenv(f"PHLO_V1_PREVIEW_TRINO_PASSWORD_{env}", "disposable-test")
    targets = {"prod": ("prod", "main"), "staging": ("stage", "candidate")}
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {env: {"dagster_location": env, "nessie_ref": ref} for env, (_, ref) in targets.items()}
        ),
    )
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                env: {"catalog": f"usage_{key}", "nessie_ref": ref}
                for env, (key, ref) in targets.items()
            }
        ),
    )
    monkeypatch.setenv(
        "PHLO_V1_USAGE_TRINO_SOURCES",
        json.dumps(
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
    )
    actor = "alice"
    monkeypatch.setattr(
        v1_query,
        "get_request_principal",
        lambda _request: AuthPrincipal(subject=actor, principal_type="user"),
    )
    monkeypatch.setattr(
        usage,
        "get_request_principal",
        lambda _request: AuthPrincipal(subject="trino_listener", principal_type="service"),
    )
    app = FastAPI()
    app.include_router(v1_query.router, prefix="/api/v1")
    app.include_router(usage.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def api_error(_request: Request, exc: PhloApiError):
        return JSONResponse(error_envelope(exc), status_code=exc.status_code)

    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        incidents.initialize_incidents()
        usage.initialize_usage()
        ids = {}
        v1_query._QUERY_SESSIONS.clear()
        with TestClient(app) as client:
            for env, expected in (("prod", 3), ("staging", 7)):
                response = client.post(
                    f"/api/v1/queries?env={env}",
                    json={"sql": "SELECT id FROM warehouse.orders", "row_limit": 1},
                )
                assert response.status_code == 202, response.text
                query_id = response.json()["id"]
                for _ in range(300):
                    result = client.get(f"/api/v1/queries/{query_id}?env={env}").json()
                    if result["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.1)
                assert result["status"] == "completed", json.dumps(result)
                assert result["evidence_available"] is True
                assert result["result"]["rows"] == [{"id": expected}]
                provider_id = v1_query._QUERY_SESSIONS[query_id].trino_query_id
                assert provider_id
                ids[env] = (query_id, provider_id)
                for _ in range(100):
                    event = next(
                        (e for e in events if e["metadata"]["queryId"] == provider_id), None
                    )
                    if event:
                        break
                    time.sleep(0.1)
                assert event is not None
                ingested = client.post(
                    "/api/v1/trino/query-completed?source_id=cluster", json=event
                )
                assert ingested.status_code == 202, ingested.text
                assert ingested.json() == {"observed_inputs": 1}
                ref = targets[env][1]
                page = usage.read_query_usage(
                    env, ref, "warehouse/orders", "warehouse/orders", 100, None
                )
                assert [item.query_id for item in page.items] == [provider_id]
                assert (
                    usage.read_query_usage(
                        env, "wrong-ref", "warehouse/orders", "warehouse/orders", 100, None
                    ).items
                    == []
                )
                print(
                    f"workspace evidence env={env} ref={ref} id={query_id} provider={provider_id} rows={expected}"
                )
            for env, other in (("prod", "usage_stage"), ("staging", "usage_prod")):
                for sql in (
                    "INSERT INTO warehouse.orders VALUES (99)",
                    f"SELECT id FROM {other}.warehouse.orders",
                ):
                    rejected = client.post(f"/api/v1/queries?env={env}", json={"sql": sql})
                    assert rejected.status_code == 422, rejected.text
            actor = "bob"
            for env, (query_id, _) in ids.items():
                assert client.get(f"/api/v1/queries/{query_id}?env={env}").status_code == 404
                history = client.get(f"/api/v1/queries/history?env={env}").json()
                assert [item["id"] for item in history["items"]] == [query_id]
                assert history["items"][0]["nessie_ref"] == targets[env][1]
                assert "rows" not in json.dumps(history) and "SELECT" not in json.dumps(history)
        # New client and no process-local sessions: retrieval must come from PostgreSQL.
        v1_query._QUERY_SESSIONS.clear()
        actor = "alice"
        with TestClient(app) as restarted:
            for env, (query_id, provider_id) in ids.items():
                recovered = restarted.get(f"/api/v1/queries/{query_id}?env={env}")
                assert recovered.status_code == 200, recovered.text
                assert recovered.json()["status"] == "completed"
                assert recovered.json()["result"]["rows"] == [{"id": 3 if env == "prod" else 7}]
                assert [
                    item["id"]
                    for item in restarted.get(f"/api/v1/queries/history?env={env}").json()["items"]
                ] == [query_id]
                assert (
                    restarted.get(
                        f"/api/v1/queries/{query_id}?env={'staging' if env == 'prod' else 'prod'}"
                    ).status_code
                    == 404
                )
                assert (
                    usage.read_query_usage(
                        env, targets[env][1], "warehouse/orders", "warehouse/orders", 100, None
                    )
                    .items[0]
                    .query_id
                    == provider_id
                )
        v1_query._QUERY_SESSIONS.clear()
