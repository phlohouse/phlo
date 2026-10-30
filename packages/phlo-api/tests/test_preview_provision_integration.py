"""Generated preview bundle against disposable TLS Trino, Nessie and object storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pyarrow as pa
import pytest
import yaml
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.io import load_file_io
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.postgres import PostgresContainer
from trino.auth import BasicAuthentication
from trino.dbapi import connect
from trino.exceptions import TrinoQueryError

from phlo_api import incidents
from phlo_api.api import v1_query
from phlo_api.observatory_api.v1_preview import execute_preview
from phlo_trino.cli import trino_group


@pytest.fixture
def mock_dns() -> None:
    """Use real DNS for disposable containers and the asynchronous HTTP client."""


def _server_owner(output: Path, image: str, owner: str) -> None:
    """Simulate a different operator UID without changing file permissions."""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "chown",
            "-v",
            f"{output}:/inputs",
            image,
            "-R",
            owner,
            "/inputs/trino",
            "/inputs/secrets",
        ],
        check=True,
        capture_output=True,
    )


def _ready(url: str, **kwargs) -> None:
    for _ in range(120):
        try:
            response = httpx.get(url, timeout=2, **kwargs)
            if response.is_success and (
                "json" not in response.headers.get("content-type", "")
                or response.json().get("starting") is not True
            ):
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    raise AssertionError(f"Disposable service did not start: {url}")


def _tls(root: Path, password: str = "disposable-preview-password") -> None:
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
    (root / "key-password").write_text("disposable-keystore-password", encoding="utf-8")
    (root / "key-password").chmod(0o600)
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
    (root / "password").write_text(password, encoding="utf-8")
    (root / "password").chmod(0o600)
    (root / "server.p12").chmod(0o600)


@pytest.mark.integration
def test_generated_preview_reads_denies_writes_and_recovers_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("PHLO_V1_ENVIRONMENTS", raising=False)
    monkeypatch.delenv("PHLO_V1_PREVIEW_CATALOGS", raising=False)
    monkeypatch.delenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_FILES", raising=False)
    _tls(tmp_path)
    with (
        Network() as network,
        (
            DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.3")
            .with_network(network)
            .with_network_aliases("minio")
            .with_env("MINIO_ROOT_USER", "localtest")
            .with_env("MINIO_ROOT_PASSWORD", "localpass123")
            .with_command("server /bitnami/minio/data")
            .with_exposed_ports(9000)
        ) as minio,
    ):
        endpoint = f"http://localhost:{minio.get_exposed_port(9000)}"
        _ready(endpoint + "/minio/health/ready")
        assert (
            minio.exec(
                [
                    "sh",
                    "-c",
                    "mc alias set test http://localhost:9000 localtest localpass123 >/dev/null && mc mb test/lake",
                ]
            ).exit_code
            == 0
        )
        with (
            DockerContainer("ghcr.io/projectnessie/nessie:0.108.3")
            .with_network(network)
            .with_network_aliases("nessie")
            .with_env("NESSIE_VERSION_STORE_TYPE", "IN_MEMORY")
            .with_env("nessie.catalog.default-warehouse", "warehouse")
            .with_env("nessie.catalog.warehouses.warehouse.location", "s3://lake/warehouse")
            .with_env("nessie.catalog.service.s3.default-options.endpoint", "http://minio:9000")
            .with_env("nessie.catalog.service.s3.default-options.path-style-access", "true")
            .with_env("nessie.catalog.service.s3.default-options.region", "us-east-1")
            .with_env(
                "nessie.catalog.service.s3.default-options.access-key",
                "urn:nessie-secret:quarkus:nessie.catalog.secrets.access-key",
            )
            .with_env("nessie.catalog.secrets.access-key.name", "localtest")
            .with_env("nessie.catalog.secrets.access-key.secret", "localpass123")
            .with_exposed_ports(19120)
        ) as nessie:
            nessie_url = f"http://localhost:{nessie.get_exposed_port(19120)}"
            _ready(nessie_url + "/api/v2/config")
            seed = RestCatalog(
                "seed",
                uri=nessie_url + "/iceberg",
                warehouse="warehouse",
                prefix="main",
                **{
                    "s3.endpoint": endpoint,
                    "s3.access-key-id": "localtest",
                    "s3.secret-access-key": "localpass123",
                    "s3.region": "us-east-1",
                },
            )
            seed.create_namespace("warehouse")
            table = seed.create_table("warehouse.orders", pa.schema([pa.field("id", pa.int64())]))
            # Nessie advertises the container endpoint; the seed writer runs on the host.
            table.io = load_file_io(
                {
                    "s3.endpoint": endpoint,
                    "s3.access-key-id": "localtest",
                    "s3.secret-access-key": "localpass123",
                    "s3.region": "us-east-1",
                }
            )
            table.append(pa.table({"id": [7, 19, 31]}))
            (tmp_path / "catalog.properties").write_text(
                "connector.name=iceberg\niceberg.catalog.type=rest\n"
                "iceberg.rest-catalog.uri=http://nessie:19120/iceberg\n"
                "iceberg.rest-catalog.prefix=main\niceberg.rest-catalog.warehouse=warehouse\n"
                "iceberg.rest-catalog.vended-credentials-enabled=false\n"
                "fs.s3.enabled=true\ns3.endpoint=http://minio:9000\ns3.path-style-access=true\n"
                "s3.region=us-east-1\ns3.aws-access-key=localtest\ns3.aws-secret-key=localpass123\n",
                encoding="utf-8",
            )
            (tmp_path / "catalog.properties").chmod(0o600)
            (tmp_path / "phlo.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "preview-contract",
                        "environments": {
                            "analytics": {
                                "dagster_location": "primary",
                                "nessie_ref": "main",
                                "preview": {
                                    "catalog": "preview_primary",
                                    "catalog_file": "catalog.properties",
                                    "password_file": "password",
                                },
                            },
                            "etl": {"dagster_location": "etl", "nessie_ref": "etl"},
                        },
                        "default_environment": "analytics",
                    }
                ),
                encoding="utf-8",
            )
            inputs = tmp_path / "preview.json"
            inputs.write_text(
                json.dumps(
                    {
                        "ca_file": "ca.pem",
                        "keystore_file": "server.p12",
                        "keystore_password_file": "key-password",
                        "network": network.name,
                    }
                ),
                encoding="utf-8",
            )
            output = tmp_path / "preview"
            generated = CliRunner().invoke(
                trino_group,
                ["preview", "provision", "--config", str(inputs), "--output", str(output)],
            )
            assert generated.exit_code == 0, generated.output
            assert not (output / "trino/catalog/etl.properties").exists()
            compose = [
                "docker",
                "compose",
                "-p",
                "phlo-preview-contract",
                "-f",
                str(output / "compose.yaml"),
            ]
            subprocess.run([*compose, "config", "--quiet"], check=True)
            manifest = yaml.safe_load((output / "compose.yaml").read_text())
            image = manifest["services"]["trino-preview"]["image"]
            host_owner = f"{os.getuid()}:{os.getgid()}"
            original = {
                str(path.relative_to(output)): (
                    path.stat().st_mode,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                for directory in ("trino", "secrets")
                for path in (output / directory).rglob("*")
                if path.is_file()
            }
            _server_owner(output, image, "12345:12345")
            denied = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "test",
                    "-v",
                    f"{output / 'trino'}:/etc/trino:ro",
                    image,
                    "-r",
                    "/etc/trino/jvm.config",
                ],
                capture_output=True,
            )
            assert denied.returncode != 0  # A direct private bind is not portable.
            try:
                # A real missing-input failure must prevent coordinator startup.
                failed = yaml.safe_load((output / "compose.yaml").read_text())
                failed["services"]["trino-preview-init"]["volumes"][1] = (
                    "./empty:/source/secrets:ro"
                )
                failed_file = output / "failed.yaml"
                failed_file.write_text(yaml.safe_dump(failed))
                failure_compose = compose[:3] + [
                    "phlo-preview-init-failure",
                    "-f",
                    str(failed_file),
                ]
                try:
                    failed_start = subprocess.run(
                        [*failure_compose, "up", "-d"], capture_output=True
                    )
                    assert failed_start.returncode != 0
                    running = subprocess.run(
                        [*failure_compose, "ps", "--status", "running", "--services"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    assert "trino-preview" not in running.stdout
                finally:
                    subprocess.run(
                        [*failure_compose, "down", "--volumes"], check=True, capture_output=True
                    )
                started = subprocess.run([*compose, "up", "-d"], capture_output=True, text=True)
                assert started.returncode == 0, started.stderr
                subprocess.run([*compose, "up", "-d"], check=True, capture_output=True)
                ownership = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--user",
                        "0:0",
                        "--entrypoint",
                        "find",
                        "-v",
                        f"{output}:/inputs:ro",
                        image,
                        "/inputs/trino",
                        "/inputs/secrets",
                        "-printf",
                        "%U:%G\\n",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                assert set(ownership.stdout.splitlines()) == {"12345:12345"}
                _server_owner(output, image, host_owner)
                assert original == {
                    str(path.relative_to(output)): (
                        path.stat().st_mode,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                    for directory in ("trino", "secrets")
                    for path in (output / directory).rglob("*")
                    if path.is_file()
                }
                ca = str(tmp_path / "ca.pem")
                tls = ssl.create_default_context(cafile=ca)
                auth = ("phlo_api_preview_analytics", "disposable-preview-password")
                try:
                    _ready("https://localhost:10025/v1/info", verify=tls, auth=auth)
                except AssertionError:
                    logs = subprocess.run(
                        [*compose, "logs", "--no-color"], capture_output=True, text=True
                    )
                    raise AssertionError(logs.stdout[-15000:]) from None
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "trino-preview",
                        "/bin/sh",
                        "-c",
                        'test "$(id -u)" -ne 0 && '
                        'test "$(stat -c %a /etc/trino/secrets)" = 700 && '
                        'test "$(stat -c %a /etc/trino/secrets/preview-keystore.jks)" = 600 && '
                        "test -r /etc/trino/secrets/preview-password.db && "
                        "! test -e /etc/trino/secrets/password-analytics",
                    ],
                    check=True,
                    capture_output=True,
                )
                connection = connect(
                    host="localhost",
                    port=10025,
                    http_scheme="https",
                    user=auth[0],
                    auth=BasicAuthentication(*auth),
                    verify=ca,
                )
                cursor = connection.cursor()
                cursor.execute("SELECT id FROM preview_primary.warehouse.orders ORDER BY id")
                assert cursor.fetchall() == [[7], [19], [31]]
                for sql in (
                    "CREATE TABLE preview_primary.warehouse.denied (id bigint)",
                    "INSERT INTO preview_primary.warehouse.orders VALUES (99)",
                    "DELETE FROM preview_primary.warehouse.orders",
                    "SELECT * FROM system.runtime.nodes",
                    "SET SESSION query_max_run_time = '1h'",
                    "SET SESSION preview_primary.target_max_file_size = '1GB'",
                ):
                    with pytest.raises(TrinoQueryError):
                        cursor.execute(sql)
                        cursor.fetchall()
                assert (
                    httpx.post(
                        "https://localhost:10025/v1/statement",
                        verify=tls,
                        headers={"X-Trino-User": auth[0]},
                        content="SELECT 1",
                    ).status_code
                    == 401
                )
                assert (
                    httpx.get("https://localhost:10025/v1/query", verify=tls, auth=auth).status_code
                    == 200
                )
                assert httpx.get("https://localhost:10025/v1/query", verify=tls).status_code == 401
                imposter = connect(
                    host="localhost",
                    port=10025,
                    http_scheme="https",
                    user="phlo_api_preview_etl",
                    auth=BasicAuthentication(*auth),
                    verify=ca,
                ).cursor()
                with pytest.raises(TrinoQueryError, match="impersonate"):
                    imposter.execute("SELECT 1")
                    imposter.fetchall()
                with pytest.raises(httpx.ConnectError):
                    httpx.get("https://localhost:10025/v1/info/state")
                for key, value in json.loads((output / "api-settings.json").read_text()).items():
                    monkeypatch.setenv(key, value)
                monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_URL", "https://localhost:10025")
                monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
                result = asyncio.run(
                    execute_preview(
                        "SELECT table_name FROM preview_primary.information_schema.tables WHERE table_schema = 'warehouse'",
                        catalog="preview_primary",
                        disconnected=lambda: asyncio.sleep(0, result=False),
                        limit=10,
                    )
                )
                assert result["rows"] == [{"table_name": "orders"}]
                with PostgresContainer("postgres:18-alpine", password="disposable") as postgres:
                    monkeypatch.setenv(
                        "PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None)
                    )
                    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_REPLICA", "1")
                    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_PROCESS", "1")
                    incidents.initialize_incidents()
                    # Exercise public query contracts with the real executor and evidence store.
                    # This isolated router does not assert production API authentication readiness.
                    app = FastAPI()
                    app.include_router(v1_query.router, prefix="/api/v1")
                    with TestClient(app) as client:
                        catalog = client.get("/api/v1/query/catalog?env=analytics")
                        assert catalog.status_code == 200, catalog.text
                        assert any(
                            schema == {"name": "warehouse", "tables": ["orders"]}
                            for schema in catalog.json()["catalogs"][0]["schemas"]
                        )
                        for sql in (
                            "INSERT INTO preview_primary.warehouse.orders VALUES (99)",
                            "SET SESSION query_max_run_time = '1h'",
                        ):
                            assert (
                                client.post(
                                    "/api/v1/queries?env=analytics", json={"sql": sql}
                                ).status_code
                                == 422
                            )
                        submitted = client.post(
                            "/api/v1/queries?env=analytics",
                            json={
                                "sql": "SELECT sum(id) AS total, count(*) AS n FROM preview_primary.warehouse.orders",
                                "row_limit": 2,
                            },
                        )
                        assert submitted.status_code == 202, submitted.text
                        query_id = submitted.json()["id"]
                        route = f"/api/v1/queries/{query_id}?env=analytics"
                        for _ in range(100):
                            completed = client.get(route).json()
                            if completed["status"] not in {"queued", "running"}:
                                break
                            time.sleep(0.05)
                        assert completed["status"] == "completed", completed
                        assert completed["evidence_available"] is True
                        assert completed["result"]["rows"] == [{"total": 57, "n": 3}]
                        history = client.get("/api/v1/queries/history?env=analytics").json()
                        assert [item["id"] for item in history["items"]] == [query_id]
                        subprocess.run([*compose, "restart"], check=True, capture_output=True)
                        _ready("https://localhost:10025/v1/info", verify=tls, auth=auth)
                        v1_query._QUERY_SESSIONS.clear()
                        recovered = client.get(route).json()
                        assert recovered["status"] == "completed"
                        assert recovered["result"]["rows"] == [{"total": 57, "n": 3}]
                # Renew both credentials and trust using newly generated configuration.
                _tls(tmp_path, password="renewed-disposable-password")
                renewed = tmp_path / "renewed"
                generated = CliRunner().invoke(
                    trino_group,
                    ["preview", "provision", "--config", str(inputs), "--output", str(renewed)],
                )
                assert generated.exit_code == 0, generated.output
                subprocess.run([*compose, "stop", "trino-preview"], check=True, capture_output=True)
                compose[-1] = str(renewed / "compose.yaml")
                subprocess.run([*compose, "up", "-d"], check=True, capture_output=True)
                for key, value in json.loads((renewed / "api-settings.json").read_text()).items():
                    if key != "PHLO_V1_PREVIEW_TRINO_URL":
                        monkeypatch.setenv(key, value)
                renewed_tls = ssl.create_default_context(cafile=ca)
                renewed_auth = (auth[0], "renewed-disposable-password")
                _ready("https://localhost:10025/v1/info", verify=renewed_tls, auth=renewed_auth)
                assert (
                    httpx.post(
                        "https://localhost:10025/v1/statement",
                        verify=renewed_tls,
                        auth=auth,
                        headers={"X-Trino-User": auth[0]},
                        content="SELECT 1",
                    ).status_code
                    == 401
                )
                with pytest.raises(httpx.ConnectError):
                    httpx.get("https://localhost:10025/v1/info", verify=tls, auth=renewed_auth)
                result = asyncio.run(
                    execute_preview(
                        "SELECT id FROM preview_primary.warehouse.orders ORDER BY id",
                        catalog="preview_primary",
                        disconnected=lambda: asyncio.sleep(0, result=False),
                        limit=3,
                    )
                )
                assert result["rows"] == [{"id": 7}, {"id": 19}, {"id": 31}]
            finally:
                subprocess.run([*compose, "down", "--volumes"], check=True, capture_output=True)
                _server_owner(output, image, host_owner)
