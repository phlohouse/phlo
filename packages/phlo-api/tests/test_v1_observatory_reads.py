"""HTTP contracts for the v1 replacements of read-only /api/observatory capabilities."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from phlo.plugins.observatory import ObservatoryExtensionPlugin
from phlo.run_evidence.models import PipelineRun, RunResource
from phlo_api.api import v1_asset_insights, v1_assets, v1_jobs
from phlo_api.main import app
from phlo_api.observatory_api import extension_settings, extensions
from phlo_api.observatory_api.observatory_models import ObservatoryHealth, ObservatoryOperation
from phlo_api.run_evidence import default_run_evidence_store
from security_test_support import authenticated_client

LOCATIONS = {"prod": ("prod_loc", "main"), "staging": ("stage_loc", "candidate")}


@pytest.fixture(autouse=True)
def environments(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                env: {"dagster_location": location, "nessie_ref": ref}
                for env, (location, ref) in LOCATIONS.items()
            }
        ),
    )


# --- Extensions -------------------------------------------------------------


class _Extension(ObservatoryExtensionPlugin):
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def metadata(self) -> Any:
        return SimpleNamespace(name="demo", version="1.0.0")

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "name": "demo",
            "version": "1.0.0",
            "compat": {"observatory_min": "0.0.0"},
            "settings": {
                "settings_schema": {"type": "object"},
                "defaults": {"theme": "dark"},
            },
        }

    @property
    def asset_root(self) -> Any:
        return self._root


@pytest.fixture
def extension(monkeypatch, tmp_path):
    root = tmp_path / "assets"
    (root / "js").mkdir(parents=True)
    (root / "js" / "main.js").write_text("export const demo = 1;\n", encoding="utf-8")
    plugin = _Extension(root)
    monkeypatch.setattr(extensions, "discover_observatory_extensions", lambda: [plugin])
    monkeypatch.setattr(extensions, "_get_observatory_version", lambda: None)
    monkeypatch.setattr(
        extension_settings,
        "get_observatory_extension",
        lambda name: plugin if name == "demo" else None,
    )
    monkeypatch.setattr(extensions, "_cached_extensions", None)
    return plugin


def test_extension_manifests_assets_and_settings_are_served_from_v1(extension):
    client = authenticated_client("admin")
    listing = client.get("/api/v1/extensions")
    assert listing.status_code == 200, listing.text
    [item] = listing.json()["items"]
    assert listing.json()["scope"] == "project"
    assert item["name"] == "demo"
    assert item["manifest"]["settings"]["defaults"] == {"theme": "dark"}
    assert item["assets_base_path"] == "/api/v1/extensions/demo/assets"

    # The URL the legacy manifest route advertises must resolve on v1.
    legacy = client.get("/api/observatory/extension-manifests").json()["extensions"][0]
    assert legacy["assets_base_path"] == item["assets_base_path"]
    asset = client.get(f"{legacy['assets_base_path']}/js/main.js")
    assert asset.status_code == 200
    assert asset.text == "export const demo = 1;\n"

    traversal = client.get("/api/v1/extensions/demo/assets/js/..%2F..%2Fsecret")
    assert traversal.status_code == 400
    assert traversal.json()["error"]["code"] == "bad_input"
    assert client.get("/api/v1/extensions/demo/assets/missing.js").status_code == 404
    assert client.get("/api/v1/extensions/other/assets/js/main.js").status_code == 404

    settings = client.get("/api/v1/extensions/demo/settings")
    assert settings.status_code == 200
    assert settings.json() == {"name": "demo", "settings": {"theme": "dark"}, "updated_at": None}
    missing = client.get("/api/v1/extensions/other/settings")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/extensions?env=prod").status_code == 400


def test_extension_reads_keep_legacy_authorization_actions(extension):
    viewer = authenticated_client("viewer")
    # Manifests and assets are admin reads; settings are settings reads, as on the legacy routes.
    assert viewer.get("/api/v1/extensions").status_code == 403
    assert viewer.get("/api/v1/extensions/demo/assets/js/main.js").status_code == 403
    assert viewer.get("/api/v1/extensions/demo/settings").status_code == 200
    assert TestClient(app).get("/api/v1/extensions/demo/settings").status_code == 401


# --- Operations -------------------------------------------------------------


def _operation(op_id: str, status: str, kind: str, started: str) -> ObservatoryOperation:
    return ObservatoryOperation(
        id=op_id,
        name=f"{kind} {op_id}",
        kind=kind,
        status=status,
        health=ObservatoryHealth(state="error" if status == "failed" else "ok"),
        started_at=started,
        metadata={"trace_id": f"trace-{op_id}"},
    )


def test_operations_filter_before_paging_and_bind_cursor_to_filters(observatory_loaders):
    observatory_loaders(
        operations=[
            _operation("op-1", "failed", "materialize", "2026-10-30T10:00:00+00:00"),
            _operation("op-2", "succeeded", "materialize", "2026-10-30T09:00:00+00:00"),
            _operation("op-3", "failed", "backfill", "2026-10-30T08:00:00+00:00"),
            _operation("op-4", "failed", "materialize", "2026-10-30T07:00:00+00:00"),
        ],
        logs=[],
    )
    client = authenticated_client("admin")
    first = client.get("/api/v1/operations?status=failed&limit=2")
    assert first.status_code == 200, first.text
    assert [item["id"] for item in first.json()["items"]] == ["op-1", "op-3"]
    cursor = first.json()["next_cursor"]
    second = client.get(f"/api/v1/operations?status=failed&limit=2&cursor={cursor}").json()
    assert [item["id"] for item in second["items"]] == ["op-4"]
    assert second["next_cursor"] is None

    narrowed = client.get("/api/v1/operations?status=failed&kind=materialize&q=op-4").json()
    assert [item["id"] for item in narrowed["items"]] == ["op-4"]
    reused = client.get(f"/api/v1/operations?status=succeeded&limit=2&cursor={cursor}")
    assert reused.status_code == 400
    assert client.get("/api/v1/operations?status=broken").status_code == 422
    assert client.get("/api/v1/operations?env=prod").status_code == 400
    assert authenticated_client("viewer").get("/api/v1/operations").status_code == 403


def test_operation_agent_context_returns_versioned_investigation_context(observatory_loaders):
    observatory_loaders(
        operations=[_operation("ns/op-1", "failed", "materialize", "2026-10-30T10:00:00+00:00")],
        logs=[],
    )
    client = authenticated_client("admin")
    response = client.get("/api/v1/operations/ns/op-1/agent-context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "phlo.operation_observability.v1"
    assert body["operation"]["id"] == "ns/op-1"
    assert body["identifiers"]["trace_ids"] == ["trace-ns/op-1"]
    assert body["incident"]["status"] == "open"
    assert body["logs"] == [] and body["related"] == []
    missing = client.get("/api/v1/operations/absent/agent-context")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


# --- Dagster-backed asset reads ---------------------------------------------


def _node(location: str, key: list[str], deps: list[list[str]], **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{location}:{'/'.join(key)}",
        "assetKey": {"path": key},
        "description": f"{location} {'/'.join(key)}",
        "computeKind": None,
        "groupName": key[0],
        "isMaterializable": True,
        "isPartitioned": False,
        "repository": {"name": "repo", "location": {"name": location}},
        "hasAssetChecks": False,
        "jobNames": ["orders_job"],
        "dependencyKeys": [{"path": dep} for dep in deps],
        "metadataEntries": [],
        "assetMaterializations": [],
        **extra,
    }


def _inventory(nodes: list[dict[str, Any]], selector: dict[str, str] | None = None) -> dict:
    repositories: dict[tuple[str, str], list[dict[str, Any]]] = {
        ("repo", location): [] for location, _ in LOCATIONS.values()
    }
    for node in nodes:
        repositories[(node["repository"]["name"], node["repository"]["location"]["name"])].append(
            node
        )
    return {
        "data": {
            "repositoriesOrError": {
                "__typename": "RepositoryConnection",
                "nodes": [
                    {"name": name, "location": {"name": location}, "assetNodes": items}
                    for (name, location), items in repositories.items()
                    if selector is None
                    or (name, location)
                    == (selector["repositoryName"], selector["repositoryLocationName"])
                ],
            }
        }
    }


def _materialization(run_id: str, ms: int, location: str, ref: str, status="SUCCESS") -> dict:
    return {
        "timestamp": str(ms),
        "runId": run_id,
        "partition": None,
        "runOrError": {
            "__typename": "Run",
            "runId": run_id,
            "status": status,
            "pipelineName": "orders_job",
            "tags": [{"key": "phlo/ref", "value": ref}],
            "repositoryOrigin": {"repositoryLocationName": location},
        },
        "metadataEntries": [],
    }


@pytest.fixture
def dagster(monkeypatch):
    state: dict[str, Any] = {"nodes": [], "handlers": {}}

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            return _inventory(state["nodes"])
        for marker, handler in state["handlers"].items():
            if marker in query:
                return handler(variables)
        raise AssertionError(f"unexpected Dagster query: {query[:40]}")

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    return state


def test_quality_checks_report_latest_evaluation_from_the_selected_environment(dagster):
    dagster["nodes"] = [
        _node(location, ["warehouse", "orders"], []) for location, _ in LOCATIONS.values()
    ]
    executions = {
        # (run, timestamp, passed, location)
        "freshness": [("p-old", 100, False, "prod_loc"), ("p-new", 200, True, "prod_loc")],
        "volume": [("p-fail", 300, False, "prod_loc"), ("s-pass", 400, True, "stage_loc")],
        "uniqueness": [("s-only", 500, True, "stage_loc")],
    }
    dagster["handlers"]["V1AssetChecks"] = lambda variables: _inventory(
        [
            {
                "assetKey": {"path": ["warehouse", "orders"]},
                "repository": {"name": "repo", "location": {"name": location}},
                "assetChecksOrError": {
                    "__typename": "AssetChecks",
                    "checks": [{"name": name, "description": None} for name in executions],
                },
            }
            for location, _ in LOCATIONS.values()
        ],
        variables["repositorySelector"],
    )
    refs = {location: ref for location, ref in LOCATIONS.values()}
    dagster["handlers"]["V1AssetCheckExecutions"] = lambda variables: {
        "data": {
            "assetCheckExecutions": [
                {
                    "status": "SUCCEEDED",
                    "runId": run_id,
                    "timestamp": timestamp,
                    "evaluation": {"success": passed, "severity": "ERROR", "metadataEntries": []},
                    "run": {
                        "runId": run_id,
                        "tags": [{"key": "phlo/ref", "value": refs[location]}],
                        "repositoryOrigin": {
                            "repositoryLocationName": location,
                            "repositoryName": "repo",
                        },
                    },
                }
                for run_id, timestamp, passed, location in reversed(
                    executions[variables["checkName"]]
                )
            ]
        }
    }
    client = authenticated_client("viewer")
    response = client.get("/api/v1/quality-checks?env=prod")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unverified_assets"] == [] and body["next_cursor"] is None
    statuses = {item["check_name"]: (item["status"], item["latest"]) for item in body["items"]}
    assert statuses["freshness"][0] == "passed"
    assert statuses["freshness"][1]["run_id"] == "p-new"
    assert statuses["volume"][0] == "failed"
    assert statuses["volume"][1]["run_id"] == "p-fail"
    assert statuses["uniqueness"] == ("unevaluated", None)

    staging = client.get("/api/v1/quality-checks?env=staging").json()
    assert {item["check_name"]: item["status"] for item in staging["items"]} == {
        "freshness": "unevaluated",
        "uniqueness": "passed",
        "volume": "passed",
    }
    page = client.get("/api/v1/quality-checks?env=prod&limit=2").json()
    assert [item["check_name"] for item in page["items"]] == ["freshness", "uniqueness"]
    rest = client.get(f"/api/v1/quality-checks?env=prod&limit=2&cursor={page['next_cursor']}")
    assert [item["check_name"] for item in rest.json()["items"]] == ["volume"]
    cross = client.get(f"/api/v1/quality-checks?env=staging&cursor={page['next_cursor']}")
    assert cross.status_code == 400
    assert client.get("/api/v1/quality-checks").status_code == 422


def test_asset_partitions_page_keys_from_the_selected_code_location(dagster):
    dagster["nodes"] = [
        _node("prod_loc", ["daily"], [], isPartitioned=True),
        _node("prod_loc", ["plain"], []),
    ]
    keys = ["2026-10-03", "2026-10-02", "2026-10-01"]
    calls: list[dict[str, Any]] = []

    def partitions(variables):
        calls.append(variables)
        start = int(variables["cursor"] or 0)
        end = start + variables["limit"]
        return {
            "data": {
                "repositoryOrError": {
                    "__typename": "Repository",
                    "name": "repo",
                    "location": {"name": "prod_loc"},
                    "assetNodes": [
                        {
                            "assetKey": {"path": [name]},
                            "isPartitioned": name == "daily",
                            "repository": {"name": "repo", "location": {"name": "prod_loc"}},
                            "partitionDefinition": {"type": "TIME_WINDOW"}
                            if name == "daily"
                            else None,
                            "partitionKeyConnection": {
                                "results": keys[start:end] if name == "daily" else [],
                                "cursor": str(end),
                                "hasMore": name == "daily" and end < len(keys),
                            },
                        }
                        for name in ("daily", "plain")
                    ],
                }
            }
        }

    dagster["handlers"]["V1AssetPartitions"] = partitions
    client = authenticated_client("viewer")
    first = client.get("/api/v1/assets/daily/partitions?env=prod&limit=2")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["partitioned"] is True and body["partition_type"] == "TIME_WINDOW"
    assert [item["partition_key"] for item in body["items"]] == keys[:2]
    assert calls[-1]["selector"] == {"repositoryLocationName": "prod_loc", "repositoryName": "repo"}
    second = client.get(
        f"/api/v1/assets/daily/partitions?env=prod&limit=2&cursor={body['next_cursor']}"
    ).json()
    assert [item["partition_key"] for item in second["items"]] == keys[2:]
    assert second["next_cursor"] is None
    foreign = client.get(f"/api/v1/assets/plain/partitions?env=prod&cursor={body['next_cursor']}")
    assert foreign.status_code == 400

    plain = client.get("/api/v1/assets/plain/partitions?env=prod").json()
    assert plain["partitioned"] is False and plain["items"] == []
    # The asset exists only in the prod code location.
    assert client.get("/api/v1/assets/daily/partitions?env=staging").status_code == 404


def test_asset_materializations_exclude_other_locations_refs_and_failures(dagster):
    dagster["nodes"] = [
        _node(
            "prod_loc",
            ["orders"],
            [],
            assetMaterializations=[
                _materialization("newest", 3_000, "prod_loc", "main"),
                _materialization("wrong-ref", 2_500, "prod_loc", "candidate"),
                _materialization("failed", 2_200, "prod_loc", "main", status="FAILURE"),
                _materialization("older", 2_000, "prod_loc", "main"),
                _materialization("oldest", 1_000, "prod_loc", "main"),
            ],
        )
    ]
    client = authenticated_client("viewer")
    first = client.get("/api/v1/assets/orders/materializations?env=prod&limit=2")
    assert first.status_code == 200, first.text
    body = first.json()
    assert [item["run_id"] for item in body["items"]] == ["newest", "older"]
    assert body["items"][0]["timestamp"] == "1970-01-01T00:00:03Z"
    assert body["history_truncated"] is False
    rest = client.get(
        f"/api/v1/assets/orders/materializations?env=prod&limit=2&cursor={body['next_cursor']}"
    ).json()
    assert [item["run_id"] for item in rest["items"]] == ["oldest"]
    assert rest["next_cursor"] is None


def test_asset_neighbors_walk_ancestors_and_descendants_separately(dagster):
    # raw -> orders -> revenue -> report, and raw -> customers (a sibling of orders).
    dagster["nodes"] = [
        _node("prod_loc", ["raw"], []),
        _node("prod_loc", ["orders"], [["raw"]]),
        _node("prod_loc", ["customers"], [["raw"]]),
        _node("prod_loc", ["revenue"], [["orders"]]),
        _node("prod_loc", ["report"], [["revenue"]]),
    ]
    client = authenticated_client("viewer")
    both = client.get("/api/v1/assets/orders/neighbors?env=prod")
    assert both.status_code == 200, both.text
    body = both.json()
    assert {node["id"]: node["distance"] for node in body["nodes"]} == {
        "orders": 0,
        "raw": 1,
        "revenue": 1,
    }
    assert body["edges"] == [
        {"source": "raw", "target": "orders"},
        {"source": "orders", "target": "revenue"},
    ]
    downstream = client.get(
        "/api/v1/assets/raw/neighbors?env=prod&direction=downstream&depth=2"
    ).json()
    assert {node["id"]: node["distance"] for node in downstream["nodes"]} == {
        "raw": 0,
        "orders": 1,
        "customers": 1,
        "revenue": 2,
    }
    upstream = client.get("/api/v1/assets/report/neighbors?env=prod&direction=upstream&depth=10")
    assert [node["id"] for node in upstream.json()["nodes"]] == [
        "orders",
        "raw",
        "report",
        "revenue",
    ]
    assert client.get("/api/v1/assets/orders/neighbors?env=prod&depth=11").status_code == 422
    assert client.get("/api/v1/assets/orders/neighbors?env=prod&direction=x").status_code == 422
    assert client.get("/api/v1/assets/orders/neighbors?env=staging").status_code == 404


# --- Schema diff ------------------------------------------------------------


def _field(field_id: int, name: str, field_type: str, required: bool = False):
    return SimpleNamespace(field_id=field_id, name=name, field_type=field_type, required=required)


class _Table:
    def __init__(self) -> None:
        self._schemas = {
            1: SimpleNamespace(
                schema_id=1,
                fields=[
                    _field(1, "id", "long", True),
                    _field(2, "amt", "int"),
                    _field(4, "legacy", "string"),
                ],
            ),
            2: SimpleNamespace(
                schema_id=2,
                fields=[
                    _field(1, "id", "long", True),
                    _field(2, "amount", "long"),
                    _field(3, "currency", "string", True),
                ],
            ),
        }
        self._snapshots = {
            101: SimpleNamespace(snapshot_id=101, schema_id=1),
            202: SimpleNamespace(snapshot_id=202, schema_id=2),
        }

    def schemas(self):
        return self._schemas

    def snapshot_by_id(self, snapshot_id):
        return self._snapshots.get(snapshot_id)

    def current_snapshot(self):
        return self._snapshots[202]


def _record_write(store, run_id: str, before: str | None, after: str, finished: datetime) -> None:
    store.append_pipeline_run(
        PipelineRun(project_id="demo", run_id=run_id, status="success", finished_at=finished)
    )
    store.append_resource(
        RunResource(
            project_id="demo",
            run_id=run_id,
            resource_id=f"{run_id}-orders-{after}",
            resource_kind="iceberg_table",
            role="output",
            table_name="gold.orders",
            snapshot_before=before,
            snapshot_after=after,
        )
    )


def test_schema_diff_resolves_runs_to_snapshots_and_matches_fields_by_id(dagster, monkeypatch):
    dagster["nodes"] = [
        _node(
            "prod_loc",
            ["orders"],
            [],
            metadataEntries=[
                {"__typename": "TextMetadataEntry", "label": "phlo/relation", "text": "gold.orders"}
            ],
        )
    ]
    refs: list[str] = []

    def load_table(table_name, ref):
        refs.append(ref)
        assert table_name == "gold.orders"
        return _Table()

    monkeypatch.setattr(v1_asset_insights, "_load_table", load_table)
    monkeypatch.setattr(
        v1_asset_insights, "get_settings", lambda: SimpleNamespace(phlo_project="demo")
    )
    store = default_run_evidence_store()
    store.initialize()
    # Run "first" wrote two commits; its final snapshot is the one no other write replaced.
    _record_write(store, "first", None, "99", datetime(2026, 10, 1, tzinfo=UTC))
    _record_write(store, "first", "99", "101", datetime(2026, 10, 1, tzinfo=UTC))
    _record_write(store, "second", "101", "202", datetime(2026, 10, 2, tzinfo=UTC))

    client = authenticated_client("viewer")
    response = client.get("/api/v1/assets/orders/schema-diff?env=prod&from_run=first&to_run=second")
    assert response.status_code == 200, response.text
    body = response.json()
    assert refs == ["main"]
    assert body["from_schema"]["snapshot_id"] == "101"
    assert body["to_schema"]["snapshot_id"] == "202"
    assert body["to_schema"]["observed_at"].startswith("2026-10-02")
    assert body["changes"] == [
        {
            "change": "renamed",
            "field_id": 2,
            "name": "amount",
            "previous_name": "amt",
            "type": None,
            "previous_type": None,
            "required": None,
            "previous_required": None,
        },
        {
            "change": "type_changed",
            "field_id": 2,
            "name": "amount",
            "previous_name": None,
            "type": "long",
            "previous_type": "int",
            "required": None,
            "previous_required": None,
        },
        {
            "change": "added",
            "field_id": 3,
            "name": "currency",
            "previous_name": None,
            "type": "string",
            "previous_type": None,
            "required": True,
            "previous_required": None,
        },
        {
            "change": "removed",
            "field_id": 4,
            "name": "legacy",
            "previous_name": None,
            "type": None,
            "previous_type": "string",
            "required": None,
            "previous_required": False,
        },
    ]
    current = client.get("/api/v1/assets/orders/schema-diff?env=prod&from_run=second").json()
    assert current["to_schema"]["run_id"] is None and current["changes"] == []

    unknown = client.get("/api/v1/assets/orders/schema-diff?env=prod&from_run=absent")
    assert unknown.status_code == 404
    assert client.get("/api/v1/assets/orders/schema-diff?env=prod").status_code == 422


# --- Search -----------------------------------------------------------------


def _dagster_run(run_id: str, location: str, ref: str, job: str = "orders_job") -> dict:
    return {
        "runId": run_id,
        "pipelineName": job,
        "status": "SUCCESS",
        "creationTime": 1,
        "startTime": 1,
        "endTime": 2,
        "assetSelection": [{"path": ["orders"]}],
        "tags": [{"key": "phlo/ref", "value": ref}],
        "repositoryOrigin": {"repositoryLocationName": location},
    }


def test_search_is_environment_scoped_and_reports_run_scan_completeness(dagster):
    dagster["nodes"] = [
        _node("prod_loc", ["orders"], []),
        _node("prod_loc", ["order_items"], []),
        _node("stage_loc", ["orders_draft"], []),
    ]
    runs = [
        _dagster_run("prod-orders", "prod_loc", "main"),
        _dagster_run("stage-orders", "stage_loc", "candidate"),
        _dagster_run("prod-billing", "prod_loc", "main", job="billing_job"),
    ]
    dagster["handlers"]["runsOrError"] = lambda variables: {
        "data": {"runsOrError": {"__typename": "Runs", "results": runs[: variables["limit"]]}}
    }
    client = authenticated_client("admin")
    response = client.get("/api/v1/search?env=prod&q=ORDER")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(item["kind"], item["id"]) for item in body["items"]] == [
        ("asset", "order_items"),
        ("asset", "orders"),
        ("run", "prod-orders"),
    ]
    assert body["run_scan_complete"] is True and body["run_scan_limit"] == 100

    staging = client.get("/api/v1/search?env=staging&q=order&kind=asset").json()
    assert [item["id"] for item in staging["items"]] == ["orders_draft"]
    assert staging["run_scan_complete"] is None

    page = client.get("/api/v1/search?env=prod&q=order&limit=2").json()
    rest = client.get(f"/api/v1/search?env=prod&q=order&limit=2&cursor={page['next_cursor']}")
    assert [item["id"] for item in rest.json()["items"]] == ["prod-orders"]
    assert (
        client.get(f"/api/v1/search?env=prod&q=billing&cursor={page['next_cursor']}").status_code
        == 400
    )
    assert client.get("/api/v1/search?env=prod&q=x&kind=table").status_code == 400
    assert client.get("/api/v1/search?q=order").status_code == 422
    assert authenticated_client("viewer").get("/api/v1/search?env=prod&q=x").status_code == 403
