"""Real Trino 483 and Nessie ref binding for observed query inputs."""

from __future__ import annotations

import json
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
def test_trino_query_selected_catalog_version_proves_direct_nessie_ref(tmp_path: Path) -> None:
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
                    DockerContainer("bitnamilegacy/minio:2025.7.23-debian-12-r5")
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
                    config = tmp_path / "config.properties"
                    config.write_text(
                        "coordinator=true\nnode-scheduler.include-coordinator=true\n"
                        "http-server.http.port=8080\ndiscovery.uri=http://localhost:8080\n"
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
                        .with_volume_mapping(listener, "/etc/trino/listener.properties")
                        .with_volume_mapping(
                            tmp_path / "catalog" / "usage_prod.properties",
                            "/etc/trino/catalog/usage_prod.properties",
                        )
                        .with_volume_mapping(
                            tmp_path / "catalog" / "usage_stage.properties",
                            "/etc/trino/catalog/usage_stage.properties",
                        )
                        .with_exposed_ports(8080)
                    ) as trino:
                        port = trino.get_exposed_port(8080)
                        _ready(f"http://localhost:{port}/v1/info", trino=True)
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
                            query_ids[env] = cursor.query_id
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
