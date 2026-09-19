"""Contract tests for the Mission Control read models.

Covers the payload shape of every Mission Control endpoint, the fail-closed
behaviour for unknown keys, and two invariants the UI depends on: summary
counters agree with the rows they describe, and no provider credential reaches
the response.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from phlo_api.main import app

BASE = "/api/observatory/mission"


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Client with an isolated project root in demo data mode.

    Demo mode is the only mode that serves the reference seed; live mode is
    covered by dedicated outcome tests that assert no seed leaks through.
    """
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_OBSERVATORY_DATA_MODE", "demo")
    # Compose discovery must not resolve through ambient developer env.
    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    return TestClient(app)


@pytest.fixture(name="live_client")
def _live_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Client in the default live data mode against an isolated project root.

    Live mode never serves seed data: derived surfaces answer from their
    provider outcome and stored collections answer from durable state only.
    """
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_OBSERVATORY_DATA_MODE", "live")
    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_mission_read_cache() -> None:
    """Mission sources read providers through the read-model cache; tests that
    re-patch a loader mid-test must not inherit the previous load."""
    from phlo_api.observatory_api import observatory as observatory_module

    observatory_module._clear_read_model_cache()


def test_shell_and_overview_read_models(client: TestClient) -> None:
    alerts = client.get(f"{BASE}/overview/alerts")
    assert alerts.status_code == 200
    body = alerts.json()
    # Demo mode must label its payloads so nothing mistakes fixtures for the
    # lakehouse.
    assert body["evidence"]["status"] == "demo"
    assert body["data"] and {"id", "severity", "title", "detail", "raised_at"} <= set(
        body["data"][0]
    )

    environments = client.get(f"{BASE}/overview/environments").json()["data"]
    assert {env["name"] for env in environments} == {"Production", "Staging", "Development"}

    attention = client.get(f"{BASE}/overview/attention").json()["data"]
    assert len(attention) == 3
    assert attention[0]["severity"] == "danger"

    execution = client.get(f"{BASE}/overview/execution").json()["data"]
    assert len(execution) == 4
    assert execution[0]["run_id"] == "r7e42b"


def test_run_evidence_is_addressable_by_run(client: TestClient) -> None:
    stages = client.get(f"{BASE}/runs/r7e42b/stages").json()["data"]
    assert [stage["name"] for stage in stages][:2] == ["Ingest orders", "Build orders mart"]
    assert sum(1 for stage in stages if stage["flagged"]) == 1

    quality = client.get(f"{BASE}/runs/r7e42b/quality").json()["data"]
    failure = quality["blocking_failure"]
    assert failure["check"] == "order_id must be unique"
    assert failure["sample_total"] == 42
    assert len(failure["sample"]) == 3

    assert len(client.get(f"{BASE}/runs/r7e42b/events").json()["data"]["items"]) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/traces").json()["data"]) == 4
    assert len(client.get(f"{BASE}/runs/r7e42b/artifacts").json()["data"]) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/consumers").json()["data"]) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/configuration").json()["data"]) == 5


def test_unknown_run_and_dataset_fail_closed(client: TestClient) -> None:
    assert client.get(f"{BASE}/runs/nope/quality").status_code == 404
    assert client.get(f"{BASE}/datasets/nope/governance").status_code == 404
    assert client.get(f"{BASE}/releases/candidates/nope").status_code == 404
    assert client.get(f"{BASE}/platform/services/nope").status_code == 404
    assert client.get(f"{BASE}/settings/providers/nope/impact").status_code == 404


def test_dataset_governance_resolves_by_dataset_id(client: TestClient) -> None:
    governance = client.get(f"{BASE}/datasets/marts.orders/governance")
    assert governance.status_code == 200
    body = governance.json()["data"]
    assert body["ownership"]["owner"] == "Data platform"
    assert [grant["principal"] for grant in body["access"]] == [
        "Analytics",
        "Finance",
        "Orders API",
    ]
    assert body["runs"][0]["outcome"] == "Failed validation"


def test_release_summary_agrees_with_its_rows(client: TestClient) -> None:
    candidates = client.get(f"{BASE}/releases/candidates").json()["data"]
    completed = client.get(f"{BASE}/releases/completed").json()["data"]
    summary = {
        metric["label"]: metric for metric in client.get(f"{BASE}/releases/summary").json()["data"]
    }

    assert summary["Pending"]["value"] == f"{len(candidates)} candidates"
    assert summary["Last release"]["hint"] == f"{len(completed)} promotions confirmed"

    detail = client.get(f"{BASE}/releases/candidates/rel-c204").json()["data"]
    assert detail["snapshot_changes"][0]["table"] == "crm.customers"


def test_platform_keeps_runtime_and_readiness_separate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Demo mode serves the stored/seed fixture regardless of live substrate, so
    # the seeded diagnostics record and service rows are what the client sees.
    services = client.get(f"{BASE}/platform/services").json()["data"]

    services = client.get(f"{BASE}/platform/services").json()["data"]
    loki = next(service for service in services if service["name"] == "Loki")
    assert loki["runtime_state"] == "Running"
    assert loki["readiness_state"] == "Not ready"
    assert loki["attention"] is True

    diagnostics = client.get(f"{BASE}/platform/services/loki").json()["data"]
    assert diagnostics["stale"] is True
    assert diagnostics["last_confirmed_at"] == "2026-09-13T09:27:00Z"
    assert any(edge["tone"] == "danger" for edge in diagnostics["dependencies"])


def test_governance_and_workspace_read_models(client: TestClient) -> None:
    assert len(client.get(f"{BASE}/governance/publication-reviews").json()["data"]) == 3
    drift = client.get(f"{BASE}/governance/access-drift").json()["data"]
    assert sum(1 for row in drift[0]["evidence"] if row["drifted"]) == 1
    assert len(client.get(f"{BASE}/governance/ownership-gaps").json()["data"]) == 3
    assert len(client.get(f"{BASE}/governance/audit").json()["data"]) == 3

    assert len(client.get(f"{BASE}/settings/notifications").json()["data"]) == 5
    assert len(client.get(f"{BASE}/settings/members").json()["data"]) == 4
    assert len(client.get(f"{BASE}/settings/defaults").json()["data"]) == 5
    impact = client.get(f"{BASE}/settings/providers/polaris/impact").json()["data"]
    assert impact["degraded"] and impact["unaffected"]


def test_provider_connections_never_leak_credentials(client: TestClient) -> None:
    connections = client.get(f"{BASE}/settings/providers").json()["data"]
    assert connections
    raw = client.get(f"{BASE}/settings/providers").text
    for connection in connections:
        # Only the opaque locator is exposed; no scheme reaches the client.
        assert "vault://" not in raw
        assert "password" not in raw.lower()
        assert connection["endpoint"].startswith(("polaris", "nessie", "s3://"))


def test_overview_data_products_and_rail(client: TestClient) -> None:
    products = client.get(f"{BASE}/overview/data-products").json()["data"]
    assert len(products) == 5
    assert products[0]["name"] == "Orders"
    assert products[0]["target"].startswith("/datasets/")

    rail = client.get(f"{BASE}/overview/rail").json()["data"]
    assert rail["ready_count"] == 11
    assert rail["total_services"] == 12
    assert len(rail["services"]) == 6
    assert rail["release_queue"][0]["state"] == "Blocked"
    assert {row["label"] for row in rail["governance"]} == {
        "Dataset ownership",
        "Access policies",
        "Publication reviews",
    }
    assert len(rail["recovery"]) == 3


def test_platform_services_prefer_live_substrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derived service rows win over the seed, and fall back when nothing is observed."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    class _Health:
        def __init__(self, state: str, message: str) -> None:
            self.state = state
            self.message = message

    class _Service:
        def __init__(self, name: str, kind: str, status: str, state: str) -> None:
            self.name = name
            self.kind = kind
            self.status = status
            self.health = _Health(state, f"{name} probe")

    monkeypatch.setattr(
        sources,
        "_load_services",
        lambda: [
            _Service("phlo-api", "api", "running", "ok"),
            _Service("observatory", "orchestration", "stopped", "warning"),
            _Service("airbyte", "ingestion", "unknown", "unknown"),
        ],
    )

    outcome = sources.derive_platform_services()
    assert outcome.status == "ok"
    rows = outcome.data
    # Unknown-state catalog entries are dropped rather than reported as unready.
    assert [row.name for row in rows] == ["phlo-api", "observatory"]
    assert rows[0].runtime_state == "Running"
    assert rows[0].readiness_state == "Ready"
    assert rows[1].attention is True

    summary = {metric.label: metric for metric in sources.derive_platform_summary(rows)}
    assert summary["Running"].value == "1 of 2"
    assert summary["Ready"].tone == "warning"


def test_platform_derivation_reports_truthful_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-service-managed project with no observed services is an empty ok."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    monkeypatch.setattr(sources, "_load_services", lambda: [])
    outcome = sources.derive_platform_services()
    assert outcome.status == "ok"
    assert outcome.data == []


def test_platform_derivation_unavailable_when_runtime_unreachable(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service-managed project whose container runtime is down is a 503, not
    an empty service list."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    monkeypatch.setattr(sources, "_load_services", lambda: [])
    monkeypatch.setattr(sources, "project_compose_name", lambda _root: "lakehouse")
    monkeypatch.setattr(sources, "docker_reachable", lambda: False)

    outcome = sources.derive_platform_services()
    assert outcome.status == "unavailable"

    response = live_client.get(f"{BASE}/platform/services")
    assert response.status_code == 503


def _stub_catalog_services(
    monkeypatch: pytest.MonkeyPatch, *specs: tuple[str, str, str, str | None]
) -> None:
    """Patch the service catalog with (name, status, health, restart_policy)."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    class _Health:
        def __init__(self, state: str, message: str) -> None:
            self.state = state
            self.message = message

    class _Service:
        def __init__(self, name: str, status: str, state: str, restart_policy: str | None) -> None:
            self.name = name
            self.kind = "service"
            self.status = status
            self.health = _Health(state, f"{name} probe")
            self.metadata = {"restart_policy": restart_policy} if restart_policy is not None else {}

    monkeypatch.setattr(
        sources,
        "_load_services",
        lambda: [_Service(*spec) for spec in specs],
    )


def test_rail_excludes_one_shot_tasks_by_default(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfigured, the rail hides declared one-shot jobs but still offers them."""
    _stub_catalog_services(
        monkeypatch,
        ("dagster", "running", "ok", "unless-stopped"),
        ("minio-setup", "exited", "ok", "no"),
        ("postgres-volume-setup", "exited", "ok", "no"),
    )

    response = live_client.get(f"{BASE}/overview/rail")
    assert response.status_code == 200
    rail = response.json()["data"]
    assert [row["name"] for row in rail["services"]] == ["dagster"]
    assert rail["ready_count"] == 1
    assert rail["total_services"] == 1
    assert rail["service_visibility"] == {"shown": None, "configured": False}
    # The whole observed catalog is offered for explicit inclusion.
    assert {o["name"]: o["lifecycle"] for o in rail["service_options"]} == {
        "dagster": "service",
        "minio-setup": "task",
        "postgres-volume-setup": "task",
    }


def test_service_visibility_roundtrip(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded selection drives the rail, including explicit tasks."""
    _stub_catalog_services(
        monkeypatch,
        ("dagster", "running", "ok", "unless-stopped"),
        ("minio-setup", "exited", "ok", "no"),
        ("trino", "running", "ok", "unless-stopped"),
    )

    # Explicitly include a setup task.
    put = live_client.put(
        f"{BASE}/overview/rail/service-visibility",
        json={"shown": ["dagster", "minio-setup"]},
    )
    assert put.status_code == 200
    assert put.json()["data"] == {
        "shown": ["dagster", "minio-setup"],
        "configured": True,
    }

    # The GET endpoint serves the recorded configuration.
    got = live_client.get(f"{BASE}/overview/rail/service-visibility")
    assert got.json()["data"]["shown"] == ["dagster", "minio-setup"]

    rail = live_client.get(f"{BASE}/overview/rail").json()["data"]
    assert [row["name"] for row in rail["services"]] == ["dagster", "minio-setup"]
    assert rail["total_services"] == 2
    assert rail["service_visibility"]["configured"] is True

    # An empty selection is a valid configuration: the rail shows nothing.
    empty = live_client.put(f"{BASE}/overview/rail/service-visibility", json={"shown": []})
    assert empty.status_code == 200
    rail = live_client.get(f"{BASE}/overview/rail").json()["data"]
    assert rail["services"] == []
    assert rail["total_services"] == 0
    assert rail["ready_count"] == 0

    # Clearing returns the rail to its default view (tasks hidden).
    cleared = live_client.put(f"{BASE}/overview/rail/service-visibility", json={"shown": None})
    assert cleared.status_code == 200
    assert cleared.json()["data"] == {"shown": None, "configured": False}
    rail = live_client.get(f"{BASE}/overview/rail").json()["data"]
    assert [row["name"] for row in rail["services"]] == ["dagster", "trino"]
    assert rail["total_services"] == 2


def test_demo_mode_rejects_service_visibility_write(client: TestClient) -> None:
    """Demo mode cannot record configuration — the write refuses like all mutations."""
    response = client.put(f"{BASE}/overview/rail/service-visibility", json={"shown": ["dagster"]})
    assert response.status_code == 409
    assert "demo" in response.json()["detail"].lower()


def test_dataset_lineage_and_checks_derive_from_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lineage and checks come from the live asset graph; the rest stays seeded."""
    from phlo_api.observatory_api import observatory as observatory_module
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    class _Asset:
        def __init__(self, id, name, group=None, deps=(), checks=(), kinds=()) -> None:
            self.id = id
            self.name = name
            self.group = group
            self.dependencies = list(deps)
            self.checks = list(checks)
            self.kinds = list(kinds)

    assets = [
        _Asset("raw.orders", "raw.orders", group="Source"),
        _Asset("stg_orders", "stg_orders", group="Model", deps=("raw.orders",)),
        _Asset(
            "marts.orders",
            "marts.orders",
            group="Model",
            deps=("stg_orders",),
            checks=("order_id must be unique", "currency in ISO 4217"),
        ),
        _Asset("dash", "Revenue dashboard", group="Analytics", deps=("marts.orders",)),
    ]
    monkeypatch.setattr(sources, "_load_assets", lambda: assets)

    lineage = sources.derive_dataset_lineage("marts.orders")
    assert lineage.status == "ok"
    assert [node.name for node in lineage.data] == [
        "stg_orders",
        "marts.orders",
        "1 consumers",
    ]
    assert [node.current for node in lineage.data] == [False, True, False]

    checks = sources.derive_dataset_checks("marts.orders")
    assert checks.status == "ok"
    assert [check.name for check in checks.data] == [
        "order_id must be unique",
        "currency in ISO 4217",
    ]

    # Unknown assets are absent rather than inventing a chain.
    monkeypatch.setattr(sources, "_load_assets", lambda: [])
    observatory_module._clear_read_model_cache()
    assert sources.derive_dataset_lineage("marts.orders").status == "absent"


def test_ownership_gaps_derive_from_declared_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership gaps name the requirement that is actually missing."""
    from phlo_api.observatory_api import observatory as observatory_module
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    class _Asset:
        def __init__(self, id, group=None, metadata=None) -> None:
            self.id = id
            self.name = id
            self.group = group
            self.metadata = metadata or {}

    assets = [
        _Asset(
            "dlt_sales",
            group="retail",
            metadata={"owner": "retail-finance", "sla": {"freshness_hours": 30}},
        ),
        # Owned, but no declared freshness window.
        _Asset("dlt_stores", group="retail", metadata={"owner": "retail-master-data"}),
        # A dbt model: no owner at all.
        _Asset("sales_facts", group="transform"),
    ]
    monkeypatch.setattr(sources, "_load_assets", lambda: assets)

    gaps = sources.derive_ownership_gaps()
    assert gaps.status == "ok"
    # Unowned assets are the more severe gap, so they sort first.
    assert [(gap.dataset, gap.requirement) for gap in gaps.data] == [
        ("sales_facts", "Accountable owner"),
        ("dlt_stores", "Freshness SLA"),
    ]
    # A gap always records who is accountable and what to do about it.
    assert gaps.data[0].owner == "Unassigned"
    assert gaps.data[1].owner == "retail-master-data"
    assert all(gap.action for gap in gaps.data)

    # Ownership is read back onto the dataset itself, not invented.
    owned = sources._ownership_from_asset(assets[0])
    assert owned.owner == "retail-finance"
    assert owned.domain == "retail"
    assert owned.freshness_target == "30h"
    assert sources._ownership_from_asset(assets[2]).owner is None

    # Nothing observable is a truthful empty, not a fallback.
    monkeypatch.setattr(sources, "_load_assets", lambda: [])
    observatory_module._clear_read_model_cache()
    empty = sources.derive_ownership_gaps()
    assert empty.status == "ok"
    assert empty.data == []


# ---------------------------------------------------------------------------
# Live data mode: typed outcomes instead of seed fallback
# ---------------------------------------------------------------------------


def _store_collection(project_root: Path, collection: str, payload: dict) -> None:
    """Write a raw durable-state payload for one collection."""
    from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
    from phlo_api.observatory_api.observatory_durable_state import state_namespace

    get_settings_service().put(
        SettingsScope.GLOBAL,
        state_namespace(project_root.resolve(), collection),
        payload,
    )


def _items_payload(items: list[dict]) -> dict:
    return {"schema_version": 1, "items": items}


def test_live_mode_never_serves_seed_data(live_client: TestClient) -> None:
    """Absent durable collections answer empty in live mode — never the seed."""
    for path in (
        "/overview/alerts",
        "/overview/environments",
        "/governance/audit",
        "/settings/members",
    ):
        response = live_client.get(f"{BASE}{path}")
        assert response.status_code == 200, path
        body = response.json()
        assert body["data"] == [], path
        # "Nothing recorded" is labelled, not silently indistinguishable from
        # a collection that was recorded empty.
        assert body["evidence"]["reason_code"] == "absent", path
        assert body["evidence"]["status"] == "live", path

    # The rail derives live: services come from the observed catalog and the
    # release queue from WAP reports (none in this env) — never the seed.
    response = live_client.get(f"{BASE}/overview/rail")
    assert response.status_code == 200
    body = response.json()
    rail = body["data"]
    assert body["evidence"]["status"] == "live"
    assert rail["ready_count"] <= rail["total_services"]
    assert rail["total_services"] >= len(rail["services"])
    assert rail["release_queue"] == []
    fixture_labels = {"Dataset ownership", "Access policies", "Publication reviews"}
    assert fixture_labels.isdisjoint(row["label"] for row in rail["governance"])

    # Data products derive live too: one row per table evidenced as a governed
    # release output (none in this env) — never the seed.
    response = live_client.get(f"{BASE}/overview/data-products")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["evidence"]["status"] == "live"
    assert body["evidence"]["source"] == "wap_reports+run_evidence"


def test_live_mode_serves_stored_collection(live_client: TestClient, tmp_path: Path) -> None:
    """Records in durable state are served verbatim in live mode."""
    _store_collection(
        tmp_path,
        "alerts",
        _items_payload(
            [
                {
                    "id": "a1",
                    "severity": "warning",
                    "title": "Recorded alert",
                    "detail": "written by a real producer",
                    "raised_at": "10:00",
                }
            ]
        ),
    )
    body = live_client.get(f"{BASE}/overview/alerts")
    assert body.status_code == 200
    assert [row["id"] for row in body.json()["data"]] == ["a1"]
    assert body.json()["evidence"]["source"] == "durable-state"


def test_live_mode_corrupt_collection_is_503(live_client: TestClient, tmp_path: Path) -> None:
    """A malformed durable payload surfaces as unavailable, not empty."""
    _store_collection(tmp_path, "alerts", {"schema_version": 999, "items": []})
    assert live_client.get(f"{BASE}/overview/alerts").status_code == 503

    # Non-mapping items are structural corruption, not record-level drops.
    _store_collection(tmp_path, "environments", _items_payload(["not-a-mapping"]))
    assert live_client.get(f"{BASE}/overview/environments").status_code == 503


def test_live_mode_malformed_records_dropped_not_served(
    live_client: TestClient, tmp_path: Path
) -> None:
    """Records that fail model validation are dropped; the valid remainder
    still answers so a partially corrupt collection degrades, not vanishes."""
    _store_collection(
        tmp_path,
        "alerts",
        _items_payload(
            [
                {
                    "id": "a1",
                    "severity": "warning",
                    "title": "Valid",
                    "detail": "recorded",
                    "raised_at": "10:00",
                },
                {"id": "broken"},
            ]
        ),
    )
    body = live_client.get(f"{BASE}/overview/alerts")
    assert body.status_code == 200
    assert [row["id"] for row in body.json()["data"]] == ["a1"]
    # The dropped record marks the collection partial rather than silently
    # losing the row.
    assert body.json()["evidence"]["reason_code"] == "partial"
    assert body.json()["evidence"]["dropped_records"] == 1


def test_live_mode_unknown_run_and_dataset_are_404(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider answers that lack the id are absent, never a stored seed."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    monkeypatch.setattr(
        sources,
        "load_runs_strict",
        lambda: [ObservatoryRun(id="r-real", name="orders_daily", status="succeeded")],
    )
    monkeypatch.setattr(sources, "_load_assets", lambda: [])

    assert live_client.get(f"{BASE}/runs/nope").status_code == 404
    assert live_client.get(f"{BASE}/datasets/nope").status_code == 404

    detail = live_client.get(f"{BASE}/runs/r-real")
    assert detail.status_code == 200
    assert detail.json()["data"]["run_id"] == "r-real"


def test_live_mode_orchestrator_down_is_503(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed orchestrator dependency is unavailable — not an empty run list."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    def _boom() -> list:
        raise ConnectionError("orchestrator unreachable")

    monkeypatch.setattr(sources, "load_runs_strict", _boom)

    assert live_client.get(f"{BASE}/overview/execution").status_code == 503
    assert live_client.get(f"{BASE}/overview/attention").status_code == 503
    assert live_client.get(f"{BASE}/overview/summary").status_code == 503
    assert live_client.get(f"{BASE}/runs/r-1").status_code == 503


def test_live_mode_release_report_source_down_is_503(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Governed release surfaces fail closed when the WAP report store is
    unreadable — never an empty list that reads as 'nothing to release'."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    def _boom() -> list:
        raise ConnectionError("report store unreadable")

    monkeypatch.setattr(sources, "load_wap_reports", _boom)
    assert live_client.get(f"{BASE}/releases/candidates").status_code == 503
    assert live_client.get(f"{BASE}/releases/completed").status_code == 503


def test_live_mode_serves_stale_read_model_with_evidence(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Provider down + a previously confirmed read = stale data with evidence.

    The stale envelope must say so — the answer is the last confirmed value,
    never a silent success and never an error that hides recoverable data.
    """
    from phlo_api.observatory_api import observatory as observatory_module
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    monkeypatch.setenv("PHLO_OBSERVATORY_CACHE_DIR", str(tmp_path / "cache"))
    # Zero TTL: every read misses in-memory and re-runs the loader, while the
    # persisted row survives as the stale fallback.
    monkeypatch.setattr(sources, "_MISSION_SOURCE_TTL_SECONDS", 0)
    observatory_module._clear_read_model_cache()

    calls = {"count": 0}

    def _runs() -> list[ObservatoryRun]:
        calls["count"] += 1
        if calls["count"] > 1:
            raise ConnectionError("orchestrator unreachable")
        return [ObservatoryRun(id="r-live", name="orders_daily", status="running")]

    monkeypatch.setattr(sources, "load_runs_strict", _runs)

    first = live_client.get(f"{BASE}/overview/execution")
    assert first.status_code == 200
    assert first.json()["evidence"]["status"] == "live"
    assert [row["run_id"] for row in first.json()["data"]] == ["r-live"]

    second = live_client.get(f"{BASE}/overview/execution")
    assert second.status_code == 200
    evidence = second.json()["evidence"]
    assert evidence["status"] == "stale"
    assert evidence["reason_code"] == "stale"
    assert evidence["last_confirmed_at"]
    assert [row["run_id"] for row in second.json()["data"]] == ["r-live"]


def test_live_mode_no_catalog_provider_degrades_release_readiness(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Candidates derive from governed WAP reports, not the catalog. With no
    catalog provider the list still answers; readiness degrades explicitly
    and the promotion preview gates on the unreadable revisions."""
    import json as _json

    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    reports_dir = tmp_path / ".phlo" / "wap-reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "run-1.json").write_text(
        _json.dumps(
            {
                "schema_version": "phlo.wap_report.v2",
                "run_id": "run-1",
                "status": "success",
                "strategy": "branch",
                "branch": "pipeline-run-run-1",
                "dagster_run_id": "dagster-run-1",
            }
        )
    )

    monkeypatch.setattr(sources, "_catalog_provider", lambda: None)
    monkeypatch.setattr(sources, "_catalog_branch_provider", lambda: None)

    response = live_client.get(f"{BASE}/releases/candidates")
    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["id"] for row in rows] == ["run-1"]

    preview = live_client.get(f"{BASE}/releases/candidates/run-1/preview")
    assert preview.status_code == 200
    body = preview.json()["data"]
    assert body["eligible"] is False
    assert any(check["outcome"] == "unavailable" for check in body["checks"])


def test_live_mode_gets_do_not_write_project_state(live_client: TestClient, tmp_path: Path) -> None:
    """GET-only traversal must not create .phlo directories or files."""
    for path in (
        "/overview/alerts",
        "/overview/summary",
        "/overview/rail",
        "/runs/r-1",
        "/datasets/nope",
        "/releases/candidates",
        "/platform/services",
        "/governance/ownership-gaps",
        "/settings/providers",
    ):
        live_client.get(f"{BASE}{path}")
    # Business-state dir must never be created by a GET. .phlo/logs is the
    # phlo process log location resolved once at logging setup — runtime
    # output, not request-scoped state, so it is outside this contract.
    assert not (tmp_path / ".phlo" / "observatory").exists()


def test_demo_mode_blocks_provider_mutations(client: TestClient) -> None:
    """Demo mode cannot invoke real actions — every mutation route refuses."""
    posts = [
        ("/api/observatory/actions", {"action_id": "service:dagster:restart"}),
        ("/api/observatory/runs/r-1/retry", {}),
        ("/api/observatory/runs/r-1/cancel", {}),
        ("/api/observatory/assets/marts.orders/materialize", {}),
        ("/api/observatory/assets/marts.orders/backfill", {}),
        ("/api/observatory/branches/actions", {"action_id": "merge", "branch": "wap/x"}),
        ("/api/observatory/workflow-wizard/actions", {"action_id": "apply", "proposal_id": "p-1"}),
    ]
    for path, payload in posts:
        response = client.post(path, json=payload)
        assert response.status_code == 409, f"{path} did not refuse in demo mode"
        assert "demo" in response.json()["detail"].lower(), path


def test_demo_mode_serves_seed_for_unbacked_surfaces(client: TestClient) -> None:
    """Demo mode is the explicit fixture mode: seed data answers, labelled by
    the data mode rather than mixed into live responses."""
    alerts = client.get(f"{BASE}/overview/alerts")
    assert alerts.status_code == 200
    assert len(alerts.json()["data"]) == 5

    attention = client.get(f"{BASE}/overview/attention")
    assert attention.status_code == 200
    assert len(attention.json()["data"]) == 3


# ------------------------------------------------------ deployment context (MC-02)


@pytest.fixture(name="project_client")
def _project_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Live-mode client bound to a valid minimal project on env `qa`."""
    (tmp_path / "phlo.yaml").write_text("name: acceptance-project\n", encoding="utf-8")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_OBSERVATORY_DATA_MODE", "live")
    monkeypatch.setenv("PHLO_ENVIRONMENT", "qa")
    monkeypatch.delenv("PHLO_COMPOSE_PROJECT", raising=False)
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    return TestClient(app)


@pytest.fixture(name="offline_providers")
def _offline_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the context dependency probes deterministic: providers down."""
    from phlo_api.observatory_api import observatory as observatory_module
    from phlo_api.observatory_api import observatory_mission_context as context_module
    from phlo_api.observatory_api import observatory_services

    monkeypatch.setattr(context_module, "_probe_http", lambda _url: "endpoint unreachable (test)")
    monkeypatch.setattr(observatory_module, "_catalog_branch_provider", lambda: None)
    monkeypatch.setattr(observatory_services, "docker_reachable", lambda: False)


@pytest.mark.usefixtures("offline_providers")
def test_context_reports_invalid_project(live_client: TestClient) -> None:
    """A bad project path cannot yield a healthy demo or a ready context."""
    response = live_client.get(f"{BASE}/context")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["status"] == "live"
    data = body["data"]
    assert data["project_valid"] is False
    assert data["project_id"] is None
    assert "phlo.yaml" in (data["project_detail"] or "")
    assert data["read_ready"] is False
    assert data["control_ready"] is False
    assert any("phlo.yaml" in blocker for blocker in data["blockers"])
    assert all(not action["available"] for action in data["actions"])


@pytest.mark.usefixtures("offline_providers")
def test_context_reports_configured_project(project_client: TestClient) -> None:
    """Valid project + environment identity; memory backend blocks control."""
    response = project_client.get(f"{BASE}/context")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["environment_id"] == "qa"
    data = body["data"]
    assert data["project_valid"] is True
    assert data["project_id"] == "acceptance-project"
    assert data["environment_id"] == "qa"
    assert data["data_mode"] == "live"
    assert data["read_ready"] is True
    # The conftest forces the in-memory settings backend, which is never
    # control-capable: control readiness must refuse with a stated reason.
    assert data["control_ready"] is False
    assert any("durable" in blocker.lower() for blocker in data["blockers"])
    deps = {dep["name"]: dep for dep in data["dependencies"]}
    assert deps["durable_state"]["status"] == "unsupported"
    assert deps["orchestrator"]["status"] == "unavailable"
    assert deps["catalog"]["status"] == "unconfigured"
    assert deps["container_runtime"]["status"] == "unavailable"
    actions = {action["action"]: action for action in data["actions"]}
    assert actions["service:restart"]["available"] is False
    assert actions["candidate:promote"]["available"] is False
    assert actions["dataset:publish"]["reason"] == "Control plane is not ready"


def test_context_demo_mode_labels_and_blocks_control(
    project_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo mode answers truthfully labelled and cannot present control-ready."""
    monkeypatch.setenv("PHLO_OBSERVATORY_DATA_MODE", "demo")
    response = project_client.get(f"{BASE}/context")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["status"] == "demo"
    data = body["data"]
    assert data["data_mode"] == "demo"
    assert data["control_ready"] is False
    assert any("demo" in blocker.lower() for blocker in data["blockers"])
    assert all(
        not action["available"] and "demo" in (action["reason"] or "").lower()
        for action in data["actions"]
    )


@pytest.mark.usefixtures("offline_providers")
def test_context_control_ready_with_durable_state(
    project_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a durable store and resolvable authz, control readiness holds."""
    from phlo_api.observatory_api import observatory_mission_context as context_module

    monkeypatch.setattr(context_module, "get_settings_service", lambda: object())
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "postgres")
    response = project_client.get(f"{BASE}/context")
    assert response.status_code == 200
    data = response.json()["data"]
    deps = {dep["name"]: dep for dep in data["dependencies"]}
    assert deps["durable_state"]["status"] == "ready"
    assert data["read_ready"] is True
    assert data["control_ready"] is True
    actions = {action["action"]: action for action in data["actions"]}
    # Storage-backed actions are gated by the durable store alone.
    assert actions["dataset:publish"]["available"] is True
    # Provider-backed actions stay blocked on their own dependency.
    assert actions["service:restart"]["available"] is False
    assert actions["service:restart"]["reason"] == "container_runtime is unavailable"


def test_evidence_carries_environment_identity(project_client: TestClient) -> None:
    """Every mission read stamps the configured environment into evidence."""
    response = project_client.get(f"{BASE}/overview/alerts")
    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["environment_id"] == "qa"
    assert evidence["project_id"] == "acceptance-project"


# --------------------------------------------------------------------- MC-03
# Parameterized resource identity: multi-segment, dotted and encoded ids must
# round-trip through the route layer without aliasing or double decoding.


def _observatory_profile(dataset_id: str, name: str):
    """Minimal real profile for a resolved dataset id."""
    from phlo_api.observatory_api.observatory_models import (
        ObservatoryDataset,
        ObservatoryDatasetProfile,
    )

    return ObservatoryDatasetProfile(
        dataset=ObservatoryDataset(
            id=dataset_id,
            name=name,
            description=f"{name} dataset",
            publication_state="published",
            readiness_state="ok",
        )
    )


def test_dataset_detail_round_trips_multi_segment_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slash-joined asset keys resolve through the :path route converter."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    monkeypatch.setattr(
        sources,
        "_load_dataset_profile",
        lambda dataset_id: _observatory_profile(dataset_id, "Orders"),
    )
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    response = live_client.get(f"{BASE}/datasets/marts/orders")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["id"] == "marts/orders"
    assert body["evidence"]["status"] == "live"


def test_dataset_detail_round_trips_encoded_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Percent-encoded ids decode exactly once to the canonical identity."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    seen: list[str] = []

    def _profile(dataset_id: str):
        seen.append(dataset_id)
        return _observatory_profile(dataset_id, "Orders")

    monkeypatch.setattr(sources, "_load_dataset_profile", _profile)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    response = live_client.get(f"{BASE}/datasets/marts%2Forders")
    assert response.status_code == 200
    assert seen == ["marts/orders"]
    assert response.json()["data"]["id"] == "marts/orders"


def test_dataset_detail_does_not_alias_dotted_and_slash_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`marts.orders` and `marts/orders` are distinct identities, not aliases."""
    from fastapi import HTTPException

    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    def _profile(dataset_id: str):
        if dataset_id == "marts.orders":
            return _observatory_profile(dataset_id, "Orders")
        raise HTTPException(status_code=404, detail="dataset not found")

    monkeypatch.setattr(sources, "_load_dataset_profile", _profile)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    assert live_client.get(f"{BASE}/datasets/marts.orders").status_code == 200
    # The slash form must not fall through to the dotted record.
    assert live_client.get(f"{BASE}/datasets/marts/orders").status_code == 404


def test_dataset_detail_two_distinct_ids_resolve(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two distinct datasets resolve to their own payloads by direct URL."""
    from fastapi import HTTPException

    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    names = {"marts/orders": "Orders", "staging/customers": "Customers"}

    def _profile(dataset_id: str):
        if dataset_id in names:
            return _observatory_profile(dataset_id, names[dataset_id])
        raise HTTPException(status_code=404, detail="dataset not found")

    monkeypatch.setattr(sources, "_load_dataset_profile", _profile)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    first = live_client.get(f"{BASE}/datasets/marts/orders").json()["data"]
    second = live_client.get(f"{BASE}/datasets/staging/customers").json()["data"]
    assert first["id"] == "marts/orders" and first["name"] == "Orders"
    assert second["id"] == "staging/customers" and second["name"] == "Customers"


def test_dataset_detail_path_traversal_is_absent(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traversal-shaped ids are lookup keys, never file paths."""
    from fastapi import HTTPException

    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    monkeypatch.setattr(
        sources,
        "_load_dataset_profile",
        lambda dataset_id: (_ for _ in ()).throw(
            HTTPException(status_code=404, detail="dataset not found")
        ),
    )

    response = live_client.get(f"{BASE}/datasets/..%2F..%2Fphlo.yaml")
    assert response.status_code == 404


def test_dataset_detail_reports_schema_preview_and_runs(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema, bounded preview and recent runs come from the resolved profile."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import (
        ObservatoryDataset,
        ObservatoryDatasetProfile,
        ObservatoryResourceRef,
        ObservatoryRun,
        ObservatoryTable,
        ObservatoryTablePreview,
    )

    table = ObservatoryTable(
        id="marts.orders",
        name="orders",
        asset_id="marts/orders",
        metadata={
            "columns": [
                {"name": "order_id", "type": "string", "nullable": False, "primary_key": True},
                {"name": "total", "type": "decimal(12,2)", "nullable": True},
            ]
        },
    )
    profile = ObservatoryDatasetProfile(
        dataset=ObservatoryDataset(
            id="marts/orders",
            name="Orders",
            publication_state="published",
            readiness_state="ok",
        ),
        tables=[table],
    )
    preview = ObservatoryTablePreview(
        table=table,
        columns=["order_id", "total"],
        column_types=["string", "decimal(12,2)"],
        rows=[{"order_id": "ORD-1", "total": 42.5}],
        row_count=1,
        state="ready",
    )
    run = ObservatoryRun(
        id="run-99",
        name="daily_build",
        status="succeeded",
        completed_at="2026-09-13T08:00:00+00:00",
        assets=[ObservatoryResourceRef(kind="asset", id="marts/orders", label="orders")],
    )

    monkeypatch.setattr(sources, "_load_dataset_profile", lambda dataset_id: profile)
    monkeypatch.setattr(sources, "_load_table_preview", lambda *a, **k: preview)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [run])

    body = live_client.get(f"{BASE}/datasets/marts/orders").json()["data"]
    assert [field["field"] for field in body["schema_fields"]] == ["order_id", "total"]
    assert body["schema_fields"][0]["type"] == "string"
    assert body["schema_fields"][0]["nullable"] == "No"
    assert body["preview"]["columns"] == ["order_id", "total"]
    assert body["preview"]["rows"] == [["ORD-1", "42.5"]]
    # A live select is current-state — never claimed snapshot-pinned.
    assert body["preview"]["pinned"] is False
    assert body["runs_state"] == "ready"
    assert [row["run_id"] for row in body["runs"]] == ["run-99"]


def test_dataset_detail_preview_labels_current_state(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no query relation exists the preview says so instead of faking rows."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import (
        ObservatoryDataset,
        ObservatoryDatasetProfile,
    )

    profile = ObservatoryDatasetProfile(
        dataset=ObservatoryDataset(
            id="marts/orders",
            name="Orders",
            publication_state="draft",
            readiness_state="unknown",
        )
    )
    monkeypatch.setattr(sources, "_load_dataset_profile", lambda dataset_id: profile)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    body = live_client.get(f"{BASE}/datasets/marts/orders").json()["data"]
    assert body["preview"]["state"] == "no_table"
    assert body["preview"]["detail"]
    assert body["preview"]["rows"] == []


def test_run_detail_resolves_distinct_ids(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs resolve by direct URL; unknown ids are absent."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    runs = [
        ObservatoryRun(id="run-a", name="nightly", status="succeeded"),
        ObservatoryRun(id="run-b", name="backfill", status="failed"),
    ]
    monkeypatch.setattr(sources, "load_runs_strict", lambda: runs)

    first = live_client.get(f"{BASE}/runs/run-a")
    second = live_client.get(f"{BASE}/runs/run-b")
    missing = live_client.get(f"{BASE}/runs/run-zzz")
    assert first.status_code == 200 and first.json()["data"]["id"] == "run-a"
    assert second.status_code == 200 and second.json()["data"]["id"] == "run-b"
    assert missing.status_code == 404


# ------------------------------------------------------------- MC-04 evidence


def _evidence_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A run-evidence sqlite store isolated to this test's tmp_path."""
    from phlo.run_evidence import SQLiteRunEvidenceStore

    database = tmp_path / "run-evidence.sqlite"
    monkeypatch.setenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", str(database))
    return SQLiteRunEvidenceStore(database)


def _seed_run(
    store,
    *,
    run_id: str,
    project: str,
    attempt: int = 1,
    status: str = "success",
    provider_run_id: str | None = None,
) -> None:
    from phlo.run_evidence import PipelineRun

    store.append_pipeline_run(
        PipelineRun(
            project_id=project,
            run_id=run_id,
            attempt=attempt,
            pipeline_name="orders_daily",
            provider_run_id=provider_run_id,
            status=status,
        )
    )


def test_run_evidence_read_models_from_store(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stages/events/quality/configuration answer from the durable store,
    joined to the orchestrator's run identity."""
    from phlo.run_evidence import RunEvent, RunQualityResult, RunStage
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    project = sources.project_id()
    store = _evidence_store(tmp_path, monkeypatch)
    _seed_run(store, run_id="r-live-1", project=project, provider_run_id="dagster-9")
    store.append_stage(
        RunStage(
            project_id=project,
            run_id="r-live-1",
            stage_id="stage-extract",
            stage_type="extract",
            provider="dbt",
            status="success",
        )
    )
    store.append_event(
        RunEvent(
            project_id=project,
            run_id="r-live-1",
            event_id="ev-1",
            event_type="stage_completed",
            producer="orchestrator",
            payload={"message": "stage-extract finished"},
        )
    )
    store.append_quality_result(
        RunQualityResult(
            project_id=project,
            run_id="r-live-1",
            quality_result_id="qr-1",
            check_id="orders_unique_id",
            asset="marts/orders",
            severity="ERROR",
            blocking=True,
            passed=False,
            evaluated_count=100,
            failed_count=3,
        )
    )
    monkeypatch.setattr(
        sources,
        "load_runs_strict",
        lambda: [ObservatoryRun(id="r-live-1", name="orders_daily", status="failed")],
    )

    detail = live_client.get(f"{BASE}/runs/r-live-1")
    assert detail.status_code == 200
    identity = detail.json()["data"]["identity"]
    assert identity["run_id"] == "r-live-1"
    assert identity["durable_run_id"] == "r-live-1"
    assert identity["provider_run_id"] == "dagster-9"
    assert identity["attempt"] == 1

    stages = live_client.get(f"{BASE}/runs/r-live-1/stages").json()["data"]
    assert [stage["name"] for stage in stages] == ["stage-extract"]

    events = live_client.get(f"{BASE}/runs/r-live-1/events").json()["data"]
    assert events["items"][0]["message"] == "stage-extract finished"
    assert events["total"] == 1

    quality = live_client.get(f"{BASE}/runs/r-live-1/quality").json()["data"]
    assert quality["attempt"] == 1
    assert quality["results"][0]["check"] == "orders_unique_id"
    assert quality["results"][0]["outcome"] == "Executed · failed · blocking"
    assert quality["blocking_failure"]["check"] == "orders_unique_id"
    assert quality["blocking_failure"]["sample_total"] == 3

    config = live_client.get(f"{BASE}/runs/r-live-1/configuration").json()["data"]
    assert {row["label"] for row in config} >= {"Pipeline", "Attempt"}

    logs = live_client.get(f"{BASE}/runs/r-live-1/logs").json()["data"]
    assert logs["total"] == 1
    assert "stage_completed" in logs["items"][0]["message"]


def test_run_evidence_scoped_to_selected_run(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every displayed evidence id belongs to the addressed run — another
    run's rows never bleed across."""
    from phlo.run_evidence import RunEvent
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    project = sources.project_id()
    store = _evidence_store(tmp_path, monkeypatch)
    for run_id, event_id in (("run-a", "ev-a"), ("run-b", "ev-b")):
        _seed_run(store, run_id=run_id, project=project)
        store.append_event(
            RunEvent(
                project_id=project,
                run_id=run_id,
                event_id=event_id,
                event_type="note",
                producer="test",
                payload={"message": f"belongs to {run_id}"},
            )
        )
    monkeypatch.setattr(
        sources,
        "load_runs_strict",
        lambda: [
            ObservatoryRun(id="run-a", name="a", status="succeeded"),
            ObservatoryRun(id="run-b", name="b", status="succeeded"),
        ],
    )

    events_a = live_client.get(f"{BASE}/runs/run-a/events").json()["data"]
    events_b = live_client.get(f"{BASE}/runs/run-b/events").json()["data"]
    assert [item["message"] for item in events_a["items"]] == ["belongs to run-a"]
    assert [item["message"] for item in events_b["items"]] == ["belongs to run-b"]


def test_run_evidence_survives_offline_orchestrator(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreachable orchestrator never erases retained evidence — the run
    detail is served stale and its recorded sections still answer."""
    from phlo.run_evidence import RunStage
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    project = sources.project_id()
    store = _evidence_store(tmp_path, monkeypatch)
    _seed_run(store, run_id="r-retained", project=project, status="failed")
    store.append_stage(
        RunStage(
            project_id=project,
            run_id="r-retained",
            stage_id="stage-load",
            status="failed",
        )
    )

    def _boom() -> list:
        raise ConnectionError("orchestrator unreachable")

    monkeypatch.setattr(sources, "load_runs_strict", _boom)

    detail = live_client.get(f"{BASE}/runs/r-retained")
    assert detail.status_code == 200
    assert detail.json()["evidence"]["status"] == "stale"
    assert detail.json()["data"]["identity"]["durable_run_id"] == "r-retained"

    stages = live_client.get(f"{BASE}/runs/r-retained/stages")
    assert stages.status_code == 200
    assert [row["name"] for row in stages.json()["data"]] == ["stage-load"]

    # A run with no retained evidence stays unavailable, not fabricated.
    assert live_client.get(f"{BASE}/runs/r-nowhere").status_code == 503


def test_run_events_paginate_without_dupes_or_skips(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cursor pages cover the retained event set exactly once — the
    reconnect/resume contract."""
    from phlo.run_evidence import RunEvent
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import ObservatoryRun

    project = sources.project_id()
    store = _evidence_store(tmp_path, monkeypatch)
    _seed_run(store, run_id="r-paged", project=project)
    for index in range(7):
        store.append_event(
            RunEvent(
                project_id=project,
                run_id="r-paged",
                event_id=f"ev-{index}",
                event_type="note",
                producer="test",
                payload={"message": f"line {index}"},
                sequence=index,
            )
        )
    monkeypatch.setattr(
        sources,
        "load_runs_strict",
        lambda: [ObservatoryRun(id="r-paged", name="p", status="succeeded")],
    )

    first = live_client.get(f"{BASE}/runs/r-paged/events?limit=3").json()["data"]
    assert len(first["items"]) == 3
    assert first["next_cursor"] == "3"
    assert first["total"] == 7

    seen = [item["message"] for item in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = live_client.get(f"{BASE}/runs/r-paged/events?limit=3&cursor={cursor}").json()["data"]
        seen += [item["message"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert seen == [f"line {index}" for index in range(7)]


def test_dataset_checks_distinguish_execution_states(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Passed, failed, running and never-evaluated are distinct outcomes —
    and an offline check provider degrades to declared, not fabricated."""
    from phlo_api.observatory_api import observatory_mission_control_sources as sources
    from phlo_api.observatory_api.observatory_models import (
        ObservatoryAsset,
        ObservatoryDataset,
        ObservatoryDatasetProfile,
        ObservatoryQualityCheck,
    )
    from phlo_api.observatory_api.quality import QualityCheck

    asset = ObservatoryAsset(
        id="marts/orders",
        name="orders",
    )
    profile = ObservatoryDatasetProfile(
        dataset=ObservatoryDataset(id="marts/orders", name="orders"),
        asset=asset,
        quality=[
            ObservatoryQualityCheck(
                id="manifest_declared",
                name="manifest_declared",
                asset_id="marts/orders",
                status="unknown",
            )
        ],
    )
    monkeypatch.setattr(sources, "_load_mission_dataset_profile", lambda dataset_id: profile)
    monkeypatch.setattr(sources, "load_runs_strict", lambda: [])

    snapshot = {
        "latest_checks": [
            QualityCheck(
                name="ok_check",
                asset_key=["marts", "orders"],
                severity="ERROR",
                status="PASSED",
            ),
            QualityCheck(
                name="warn_check",
                asset_key=["marts", "orders"],
                severity="WARN",
                status="FAILED",
            ),
            QualityCheck(
                name="running_check",
                asset_key=["marts", "orders"],
                severity="ERROR",
                status="IN_PROGRESS",
            ),
            QualityCheck(
                name="unevaluated",
                asset_key=["marts", "orders"],
                status="NEVER_EVALUATED",
            ),
            QualityCheck(
                name="other_asset_check",
                asset_key=["staging", "other"],
                severity="ERROR",
                status="FAILED",
            ),
        ]
    }
    monkeypatch.setattr(sources, "_load_quality_snapshot", lambda: snapshot)

    checks = live_client.get(f"{BASE}/datasets/marts/orders").json()["data"]["checks"]
    outcomes = {check["name"]: check["outcome"] for check in checks}
    assert outcomes == {
        "ok_check": "Passed",
        "warn_check": "Failed · warning",
        "running_check": "Running",
        "unevaluated": "Never evaluated",
        "manifest_declared": "Unknown",
    }
    assert "other_asset_check" not in outcomes
