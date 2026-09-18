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
    """Client with an isolated project root so seeding does not leak across tests."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    return TestClient(app)


def test_shell_and_overview_read_models(client: TestClient) -> None:
    alerts = client.get(f"{BASE}/overview/alerts")
    assert alerts.status_code == 200
    body = alerts.json()
    assert body and {"id", "severity", "title", "detail", "raised_at"} <= set(body[0])

    environments = client.get(f"{BASE}/overview/environments").json()
    assert {env["name"] for env in environments} == {"Production", "Staging", "Development"}

    attention = client.get(f"{BASE}/overview/attention").json()
    assert len(attention) == 3
    assert attention[0]["severity"] == "danger"

    execution = client.get(f"{BASE}/overview/execution").json()
    assert len(execution) == 4
    assert execution[0]["run_id"] == "r7e42b"


def test_run_evidence_is_addressable_by_run(client: TestClient) -> None:
    stages = client.get(f"{BASE}/runs/r7e42b/stages").json()
    assert [stage["name"] for stage in stages][:2] == ["Ingest orders", "Build orders mart"]
    assert sum(1 for stage in stages if stage["flagged"]) == 1

    failure = client.get(f"{BASE}/runs/r7e42b/quality").json()
    assert failure["check"] == "order_id must be unique"
    assert failure["sample_total"] == 42
    assert len(failure["sample"]) == 3

    assert len(client.get(f"{BASE}/runs/r7e42b/events").json()) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/traces").json()) == 4
    assert len(client.get(f"{BASE}/runs/r7e42b/artifacts").json()) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/consumers").json()) == 3
    assert len(client.get(f"{BASE}/runs/r7e42b/configuration").json()) == 5


def test_unknown_run_and_dataset_fail_closed(client: TestClient) -> None:
    assert client.get(f"{BASE}/runs/nope/quality").status_code == 404
    assert client.get(f"{BASE}/datasets/nope/governance").status_code == 404
    assert client.get(f"{BASE}/releases/candidates/nope").status_code == 404
    assert client.get(f"{BASE}/platform/services/nope").status_code == 404
    assert client.get(f"{BASE}/settings/providers/nope/impact").status_code == 404


def test_dataset_governance_resolves_by_dataset_id(client: TestClient) -> None:
    governance = client.get(f"{BASE}/datasets/marts.orders/governance")
    assert governance.status_code == 200
    body = governance.json()
    assert body["ownership"]["owner"] == "Data platform"
    assert [grant["principal"] for grant in body["access"]] == [
        "Analytics",
        "Finance",
        "Orders API",
    ]
    assert body["runs"][0]["outcome"] == "Failed validation"


def test_release_summary_agrees_with_its_rows(client: TestClient) -> None:
    candidates = client.get(f"{BASE}/releases/candidates").json()
    completed = client.get(f"{BASE}/releases/completed").json()
    summary = {metric["label"]: metric for metric in client.get(f"{BASE}/releases/summary").json()}

    assert summary["Pending"]["value"] == f"{len(candidates)} candidates"
    assert summary["Released · 24h"]["value"] == f"{len(completed)} completed"

    detail = client.get(f"{BASE}/releases/candidates/rel-c204").json()
    assert detail["snapshot_changes"][0]["table"] == "crm.customers"


def test_platform_keeps_runtime_and_readiness_separate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the seeded fallback: the observed-substrate path is covered by
    # test_platform_services_prefer_live_substrate. Patch the router's binding,
    # not the source module, since the name is imported directly.
    from phlo_api.observatory_api import observatory_mission_platform as platform

    monkeypatch.setattr(platform, "derive_platform_services", lambda: None)

    services = client.get(f"{BASE}/platform/services").json()
    loki = next(service for service in services if service["name"] == "Loki")
    assert loki["runtime_state"] == "Running"
    assert loki["readiness_state"] == "Not ready"
    assert loki["attention"] is True

    diagnostics = client.get(f"{BASE}/platform/services/loki").json()
    assert diagnostics["stale"] is True
    assert diagnostics["last_confirmed_at"] == "2026-09-13T09:27:00Z"
    assert any(edge["tone"] == "danger" for edge in diagnostics["dependencies"])


def test_governance_and_workspace_read_models(client: TestClient) -> None:
    assert len(client.get(f"{BASE}/governance/publication-reviews").json()) == 3
    drift = client.get(f"{BASE}/governance/access-drift").json()
    assert sum(1 for row in drift[0]["evidence"] if row["drifted"]) == 1
    assert len(client.get(f"{BASE}/governance/ownership-gaps").json()) == 3
    assert len(client.get(f"{BASE}/governance/audit").json()) == 3

    assert len(client.get(f"{BASE}/settings/notifications").json()) == 5
    assert len(client.get(f"{BASE}/settings/members").json()) == 4
    assert len(client.get(f"{BASE}/settings/defaults").json()) == 5
    impact = client.get(f"{BASE}/settings/providers/polaris/impact").json()
    assert impact["degraded"] and impact["unaffected"]


def test_provider_connections_never_leak_credentials(client: TestClient) -> None:
    connections = client.get(f"{BASE}/settings/providers").json()
    assert connections
    raw = client.get(f"{BASE}/settings/providers").text
    for connection in connections:
        # Only the opaque locator is exposed; no scheme reaches the client.
        assert "vault://" not in raw
        assert "password" not in raw.lower()
        assert connection["endpoint"].startswith(("polaris", "nessie", "s3://"))


def test_overview_data_products_and_rail(client: TestClient) -> None:
    products = client.get(f"{BASE}/overview/data-products").json()
    assert len(products) == 5
    assert products[0]["name"] == "Orders"
    assert products[0]["target"].startswith("/datasets/")

    rail = client.get(f"{BASE}/overview/rail").json()
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

    rows = sources.derive_platform_services()
    assert rows is not None
    # Unknown-state catalog entries are dropped rather than reported as unready.
    assert [row.name for row in rows] == ["phlo-api", "observatory"]
    assert rows[0].runtime_state == "Running"
    assert rows[0].readiness_state == "Ready"
    assert rows[1].attention is True

    summary = {metric.label: metric for metric in sources.derive_platform_summary(rows)}
    assert summary["Running"].value == "1 of 2"
    assert summary["Ready"].tone == "warning"


def test_platform_derivation_falls_back_when_nothing_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    monkeypatch.setattr(sources, "_load_services", lambda: [])
    assert sources.derive_platform_services() is None


def test_dataset_lineage_and_checks_derive_from_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lineage and checks come from the live asset graph; the rest stays seeded."""
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
    assert lineage is not None
    assert [node.name for node in lineage] == ["stg_orders", "marts.orders", "1 consumers"]
    assert [node.current for node in lineage] == [False, True, False]

    checks = sources.derive_dataset_checks("marts.orders")
    assert checks is not None
    assert [check.name for check in checks] == [
        "order_id must be unique",
        "currency in ISO 4217",
    ]

    # Unknown assets fall back rather than inventing a chain.
    monkeypatch.setattr(sources, "_load_assets", lambda: [])
    assert sources.derive_dataset_lineage("marts.orders") is None
