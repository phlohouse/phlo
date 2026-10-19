"""Generated preview bundle against disposable TLS Trino, Nessie and object storage."""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pyarrow as pa
import pytest
import yaml
from click.testing import CliRunner
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
            DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.1")
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
                            "prod": {
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
                        "default_environment": "prod",
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
            try:
                started = subprocess.run([*compose, "up", "-d"], capture_output=True, text=True)
                assert started.returncode == 0, started.stderr
                ca = str(tmp_path / "ca.pem")
                tls = ssl.create_default_context(cafile=ca)
                auth = ("phlo_api_preview_prod", "disposable-preview-password")
                try:
                    _ready("https://localhost:10025/v1/info", verify=tls, auth=auth)
                except AssertionError:
                    logs = subprocess.run(
                        [*compose, "logs", "--no-color"], capture_output=True, text=True
                    )
                    raise AssertionError(logs.stdout[-15000:]) from None
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
                    incidents.initialize_incidents()
                    session = v1_query.QuerySession(
                        query_id="preview-integration",
                        actor="analyst",
                        env="prod",
                        nessie_ref="main",
                        sql="SELECT id FROM preview_primary.warehouse.orders ORDER BY id",
                        catalog="preview_primary",
                        row_limit=2,
                    )
                    asyncio.run(v1_query._execute_session(session))
                    assert session.status == "completed", session.error
                    assert session.evidence_available
                    assert session.result["rows"] == [{"id": 7}, {"id": 19}]
                    assert session.trino_query_id
                    assert (
                        incidents.load_query_execution(session.id, "prod", "analyst")["statement"]
                        == session.sql
                    )
                    subprocess.run([*compose, "restart"], check=True, capture_output=True)
                    _ready("https://localhost:10025/v1/info", verify=tls, auth=auth)
                    assert incidents.load_query_execution(session.id, "prod", "analyst")["result"][
                        "rows"
                    ] == [{"id": 7}, {"id": 19}]
                # Renew both credentials and trust using newly generated configuration.
                _tls(tmp_path, password="renewed-disposable-password")
                renewed = tmp_path / "renewed"
                generated = CliRunner().invoke(
                    trino_group,
                    ["preview", "provision", "--config", str(inputs), "--output", str(renewed)],
                )
                assert generated.exit_code == 0, generated.output
                shutil.copytree(renewed / "trino", output / "trino", dirs_exist_ok=True)
                shutil.copytree(renewed / "secrets", output / "secrets", dirs_exist_ok=True)
                subprocess.run([*compose, "restart"], check=True, capture_output=True)
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
