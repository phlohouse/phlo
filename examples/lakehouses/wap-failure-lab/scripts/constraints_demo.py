"""Disposable Trino/Nessie/Iceberg lakehouse and durable Dataset policy demo.

Run from the repository root with its locked development environment. All four
containers and their data are disposable; no existing lakehouse is contacted.
The same script runs on the pre-feature checkout using only its existing rules.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx
from phlo import quality_rules
from phlo.capabilities import AssetSpec
from phlo.capabilities.registry import CapabilityRegistry
from phlo.dataset import (
    DatasetPolicy,
    DatasetService,
    EvidenceCondition,
    PolicyRule,
    StaticPolicySource,
    TransitionRequest,
    candidate_dataset_id,
)
from phlo.dataset_projection import RunReportQualityEvidenceSource
from phlo.hooks import get_hook_bus
from phlo.logging import bind_context, clear_context, get_logger
from phlo.quality import rules
from phlo.references import ref
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo.run_evidence.report import build_run_report
from phlo.run_evidence.store import SQLiteRunEvidenceStore
from phlo_pandera import clear_quality_checks, get_quality_checks
from phlo_postgres.dataset_state_store import SettingsDatasetStateStore
from phlo_postgres.settings_store import PostgresSettingsStore
from phlo_trino.resource import TrinoResource
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.postgres import PostgresContainer


def ready(url: str, *, trino: bool = False) -> None:
    for _ in range(120):
        try:
            response = httpx.get(url, timeout=2)
            if response.is_success and (not trino or not response.json()["starting"]):
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Disposable service did not start: {url}")


def lakehouse(stack: ExitStack, directory: Path) -> tuple[TrinoResource, str]:
    network = stack.enter_context(Network())
    nessie = stack.enter_context(
        DockerContainer("ghcr.io/projectnessie/nessie:0.108.3")
        .with_network(network)
        .with_network_aliases("nessie")
        .with_env("NESSIE_VERSION_STORE_TYPE", "IN_MEMORY")
        .with_exposed_ports(19120)
    )
    nessie_url = f"http://localhost:{nessie.get_exposed_port(19120)}"
    ready(nessie_url + "/api/v2/config")
    minio = stack.enter_context(
        DockerContainer("ghcr.io/phlohouse/phlo-minio:0.17.2")
        .with_network(network)
        .with_network_aliases("minio")
        .with_env("MINIO_ROOT_USER", "localtest")
        .with_env("MINIO_ROOT_PASSWORD", "localpass123")
        .with_command("server /bitnami/minio/data")
        .with_exposed_ports(9000)
    )
    ready(f"http://localhost:{minio.get_exposed_port(9000)}/minio/health/ready")
    created = minio.exec(
        [
            "sh",
            "-c",
            "mc alias set test http://127.0.0.1:9000 localtest localpass123 >/dev/null "
            "&& mc mb test/lake",
        ]
    )
    assert created.exit_code == 0
    catalog = directory / "catalog"
    catalog.mkdir()
    for name, branch in (("iceberg", "main"), ("iceberg_candidate", "candidate")):
        (catalog / f"{name}.properties").write_text(
            "connector.name=iceberg\niceberg.catalog.type=nessie\n"
            "iceberg.nessie-catalog.uri=http://nessie:19120/api/v2\n"
            f"iceberg.nessie-catalog.ref={branch}\n"
            "iceberg.nessie-catalog.default-warehouse-dir=s3://lake/warehouse\n"
            "fs.native-s3.enabled=true\ns3.endpoint=http://minio:9000\n"
            "s3.path-style-access=true\ns3.region=us-east-1\n"
            "s3.aws-access-key=localtest\ns3.aws-secret-key=localpass123\n"
        )
    trino = stack.enter_context(
        DockerContainer("trinodb/trino:477")
        .with_network(network)
        .with_volume_mapping(catalog, "/etc/trino/catalog")
        .with_exposed_ports(8080)
    )
    ready(f"http://localhost:{trino.get_exposed_port(8080)}/v1/info", trino=True)
    return TrinoResource(
        host="localhost", port=int(trino.get_exposed_port(8080)), catalog="iceberg", ref="main"
    ), nessie_url


def seed(resource: TrinoResource, nessie_url: str) -> None:
    for sql in [
        "CREATE SCHEMA raw WITH (location = 's3://lake/warehouse/raw')",
        "CREATE SCHEMA gold WITH (location = 's3://lake/warehouse/gold')",
        "CREATE TABLE raw.orders (tenant BIGINT, customer BIGINT, amount DECIMAL(18,2), "
        "status VARCHAR, sold_on DATE)",
        "INSERT INTO raw.orders VALUES (1,1,11,'settled',DATE '2026-10-25'), "
        "(1,2,23,'settled',DATE '2026-10-25'), (2,1,7,'settled',DATE '2026-10-25'), "
        "(9,9,900,'void',DATE '2026-10-25'), (8,8,700,'settled',DATE '2026-10-24')",
        "CREATE TABLE raw.customers (tenant BIGINT, customer BIGINT)",
        "INSERT INTO raw.customers VALUES (1,1),(1,1),(1,2),(2,1),(9,9),(8,8)",
        "CREATE TABLE gold.summary AS SELECT tenant, customer, SUM(amount) AS revenue, "
        "sold_on AS day, status FROM raw.orders GROUP BY tenant, customer, sold_on, status",
    ]:
        resource.execute(sql)
    main = httpx.get(nessie_url + "/api/v1/trees/tree/main").json()
    response = httpx.post(
        nessie_url + "/api/v1/trees/tree",
        json={
            "type": "BRANCH",
            "name": "candidate",
            "hash": main["hash"],
        },
    )
    response.raise_for_status()
    candidate = resource.for_ref("candidate")
    candidate.execute("DELETE FROM gold.summary")
    candidate.execute(
        "INSERT INTO gold.summary SELECT o.tenant, o.customer, SUM(o.amount), o.sold_on, o.status "
        "FROM raw.orders o JOIN raw.customers c ON o.tenant=c.tenant AND o.customer=c.customer "
        "GROUP BY o.tenant, o.customer, o.sold_on, o.status"
    )


def demonstrate(resource: TrinoResource, directory: Path) -> dict:
    enabled = hasattr(quality_rules, "grain")
    registry = CapabilityRegistry()
    for key, schema, table in (("orders", "raw", "orders"), ("revenue", "gold", "summary")):
        registry.register(
            "asset",
            AssetSpec(
                key=key,
                group="retail",
                description=None,
                metadata={"catalog": "iceberg", "schema": schema, "table": table},
            ),
        )
    source = ref("orders", registry=registry, discover=False)
    target = ref("revenue", registry=registry, discover=False)
    declared = [quality_rules.range_between("revenue", min_value=0)]
    if enabled:
        declared += [
            quality_rules.grain("tenant", "customer"),
            quality_rules.reconcile_aggregate(
                name="revenue_conserved",
                source=source,
                source_column="amount",
                target_column="revenue",
                source_filters={"status": "settled"},
                target_filters={"status": "settled"},
                source_partition_column="sold_on",
            ),
        ]
    else:
        declared += [
            quality_rules.unique("tenant", "customer"),
            quality_rules.not_null("tenant", "customer"),
        ]
    store = SQLiteRunEvidenceStore(directory / "evidence.sqlite")
    get_hook_bus().register_provider(
        CoreRunEvidenceHookProvider(store), plugin_name="constraints-demo"
    )
    modules = [
        "phlo.quality_rules",
        "phlo.quality",
        "phlo_pandera.plugin",
        "phlo_pandera.decorator",
        "phlo_pandera.decorator_helpers",
    ]
    if enabled:
        modules.append("phlo_pandera.dataset_constraints")
    hashes = {
        name: hashlib.sha256(Path(importlib.util.find_spec(name).origin).read_bytes()).hexdigest()
        for name in modules
    }
    observations = {
        "feature_available": enabled,
        "implementation_sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
        "module_sha256": hashes,
        "source_revision": subprocess.check_output(
            [
                "git",
                "-C",
                str(Path(quality_rules.__file__).parents[2]),
                "rev-parse",
                "HEAD",
            ],
            text=True,
        ).strip(),
        "procedure_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": {},
    }

    def check(run_id, branch, *, advisory=False):
        clear_quality_checks()

        @rules(
            table=target if enabled else "gold.summary",
            rules=declared,
            backend="trino",
            asset_key="revenue",
            partition_column="day",
            rolling_window_days=None,
            blocking=not advisory,
            warn_threshold=1.0 if advisory else 0.0,
        )
        def revenue_quality():
            pass

        bind_context(project_id="constraints-demo", run_id=run_id, attempt=1)
        runtime = SimpleNamespace(
            run_id=run_id,
            partition_key="2026-10-25",
            tags={"phlo/ref": branch},
            resources={"trino": resource if enabled else resource.for_ref(branch)},
            logger=get_logger("constraints-demo"),
        )
        result = get_quality_checks()[-1].fn(runtime)
        clear_context()
        report = build_run_report(store, "constraints-demo", run_id, 1)
        observations["cases"][run_id] = {
            "passed": result.passed,
            "severity": result.severity,
            "orchestrator_blocking": get_quality_checks()[-1].blocking,
            "quality": store.list_quality_results("constraints-demo", run_id, attempt=1),
        }
        return report

    check("correct-main", "main")
    check("multiplied-candidate", "candidate")
    check("advisory-candidate", "candidate", advisory=True)
    expected_names = (
        ["range_check", "grain_tenant_customer", "revenue_conserved"]
        if enabled
        else [
            "range_check",
            "unique_check",
            "null_check",
        ]
    )
    expected = [("revenue", name) for name in expected_names]
    evidence = RunReportQualityEvidenceSource(
        store,
        project_id="constraints-demo",
        run_id="multiplied-candidate",
        attempt=1,
        checks_by_table={"gold.summary": expected},
    )
    for severity in ("blocker", "warning"):
        state = SettingsDatasetStateStore(PostgresSettingsStore(), f"constraints-{severity}")
        service = DatasetService(
            store=state,
            evidence_source=evidence,
            policy_source=StaticPolicySource(
                DatasetPolicy(
                    policy_version="constraints-v1",
                    rules=(
                        PolicyRule(
                            control="revenue_checks",
                            evidence_kind="quality_checks",
                            severity=severity,
                            condition=EvidenceCondition("passed", "true"),
                            applies_to=frozenset({"publish"}),
                        ),
                    ),
                )
            ),
        )
        candidate_id = candidate_dataset_id("gold.summary")
        for action in ("claim", "review", "promote"):
            assert (
                service.transition(TransitionRequest(candidate_id, action, action)).status.value
                == "committed"
            )
        outcome = service.transition(TransitionRequest("gold.summary", "publish", "publish"))
        reopened = SettingsDatasetStateStore(PostgresSettingsStore(), f"constraints-{severity}")
        observations[f"publication_{severity}"] = {
            "status": outcome.status.value,
            "state": reopened.load("gold.summary").current_state,
            "record": reopened.load("gold.summary").to_read_model(),
            "audit": [
                event.to_read_model()
                for event in reopened.audit_events()
                if event.action == "publish"
            ],
        }
    candidate = resource.for_ref("candidate")
    candidate.execute("INSERT INTO gold.summary SELECT * FROM gold.summary WHERE tenant=2")
    check("duplicate-business-identity", "candidate")
    candidate.execute("INSERT INTO raw.orders VALUES (3,3,13,'settled',DATE '2026-10-25')")
    candidate.execute("DELETE FROM gold.summary")
    candidate.execute(
        "INSERT INTO gold.summary SELECT tenant,customer,SUM(amount),sold_on,status "
        "FROM raw.orders GROUP BY tenant,customer,sold_on,status"
    )
    check("recovered-candidate", "candidate")
    observations["main_total"] = str(
        resource.execute(
            "SELECT SUM(revenue) FROM gold.summary WHERE day=DATE '2026-10-25' AND status='settled'"
        )[0][0]
    )
    observations["candidate_total_after_recovery"] = str(
        candidate.execute(
            "SELECT SUM(revenue) FROM gold.summary WHERE day=DATE '2026-10-25' AND status='settled'"
        )[0][0]
    )
    assert observations["cases"]["correct-main"]["passed"]
    assert observations["cases"]["multiplied-candidate"]["passed"] is not enabled
    assert not observations["cases"]["duplicate-business-identity"]["passed"]
    assert observations["cases"]["recovered-candidate"]["passed"]
    assert observations["publication_blocker"]["state"] == ("draft" if enabled else "published")
    assert observations["publication_warning"]["state"] == "published"
    assert observations["main_total"] == "41.00"
    assert observations["candidate_total_after_recovery"] == "54.00"
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with ExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="phlo-constraints-")))
        postgres = stack.enter_context(
            PostgresContainer("postgres:18-alpine", password="disposable")
        )
        os.environ["PHLO_OBSERVATORY_SETTINGS_DB_URL"] = postgres.get_connection_url(driver=None)
        os.environ["PHLO_OBSERVATORY_SETTINGS_BACKEND"] = "memory"
        os.environ["PHLO_LOG_LEVEL"] = "WARNING"
        resource, nessie_url = lakehouse(stack, directory)
        seed(resource, nessie_url)
        observations = demonstrate(resource, directory)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(observations, indent=2))
        print(json.dumps(observations, indent=2))


if __name__ == "__main__":
    main()
