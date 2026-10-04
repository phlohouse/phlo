"""Operational consumers and honest read evidence, with no external delivery."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from phlo.plugins import observatory_settings as storage
from phlo_alerting import alert_sink, manager
from phlo_alerting.manager import Alert, AlertDestination, AlertManager
from phlo_api.api import v1, v1_assets
from phlo_api.errors import BackendUnavailableError, BadGatewayError
from phlo_api.observatory_api import settings, observatory_services
from phlo_api.v1_contract import EnvironmentTarget


@pytest.fixture
def saved_settings(monkeypatch):
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    storage._reset_memory_service()

    def save(values, revision=1):
        return storage.get_settings_service().put(
            storage.SettingsScope.GLOBAL,
            storage.ADMIN_SETTINGS_NAMESPACE,
            {
                "version": revision,
                "values": {f"observatory.settings.{key}": value for key, value in values.items()},
            },
        )

    yield save
    storage._reset_memory_service()


def test_current_revision_and_strict_validation(saved_settings):
    saved_settings({"freshness.bronze_minutes": "17", "audit.protect_main": True}, 5)
    value = storage.get_operational_settings()
    assert value.settings_revision == 5
    assert value.freshness_sla_seconds("bronze") == 1020
    assert value.freshness_sla_seconds("registry") is None
    saved_settings({"freshness.bronze_minutes": ""}, 6)
    assert storage.get_operational_settings().bronze_minutes is None
    assert storage.get_operational_settings().settings_revision == 6
    for raw in (True, "0", "1.5", "-1", "nan"):
        with pytest.raises(ValidationError):
            storage.parse_operational_settings(
                {"observatory.settings.freshness.bronze_minutes": raw}
            )
    saved_settings({"maintenance.orphan_cleanup": "whenever"})
    with pytest.raises(storage.StorageCorruptionError):
        storage.get_operational_settings()


class ControlledDestination(AlertDestination):
    def __init__(self, confirmed=True):
        self.confirmed = confirmed
        self.calls = []

    def send(self, alert):
        self.calls.append(alert)
        return self.confirmed


def test_notification_routes_confirm_all_and_retry_only_unconfirmed(saved_settings, monkeypatch):
    saved_settings({"alerts.notify_owners": True, "alerts.notify_consumers": True})
    registry = AlertManager()
    qa = ControlledDestination()
    owner = ControlledDestination(False)
    consumer = ControlledDestination()
    for name, destination in (
        ("qa_email", qa),
        ("owner:qa-owner", owner),
        ("consumer:raw/readings", consumer),
    ):
        registry.register_destination(name, destination)
    monkeypatch.setattr(manager, "_alert_manager", registry)
    routes = {
        name: alert_sink.AlertManagerSink([destination])
        for name, destination in (
            ("qa", "qa_email"),
            ("owner:qa-owner", "owner:qa-owner"),
            ("consumer:raw/readings", "consumer:raw/readings"),
        )
    }
    monkeypatch.setattr(
        settings,
        "resolve_capability",
        lambda kind, name: SimpleNamespace(provider=routes[name]) if name in routes else None,
    )
    settings.preflight_incident_notification(notify_qa=True)
    payload = dict(
        env="staging",
        incident_id="inc-9",
        effect_id="eff-17",
        severity="high",
        owner="qa-owner",
        asset_ids=["raw/readings"],
        notify_qa=True,
    )
    with pytest.raises(settings.NotificationDeliveryError):
        settings.send_incident_notification(None, **payload)
    assert len(qa.calls) == 1 and len(owner.calls) == 1 and not consumer.calls
    owner.confirmed = True
    assert settings.send_incident_notification(None, **payload) is True
    assert len(qa.calls) == 1 and len(owner.calls) == 2 and len(consumer.calls) == 1
    assert settings.send_incident_notification(None, **payload) is True
    assert len(owner.calls) == 2
    actual = qa.calls[0]
    assert actual.run_id == "staging:eff-17"
    assert actual.error_message == "incident-effect:staging:eff-17"
    assert json.loads(actual.message) == payload
    assert actual.severity.value == "error"
    routes.clear()
    with pytest.raises(settings.NotificationUnavailableError):
        settings.preflight_incident_notification(notify_qa=True)
    # Explicit routes must never receive general hook notifications.
    assert registry.send(Alert(title="other", message="other")) is False


def test_alert_adapter_consumes_chat_without_mutating_provider(saved_settings, monkeypatch):
    from phlo_alerting.destinations.slack import SlackAlertDestination

    saved_settings({"alerts.chat_channel": "#controlled"})
    registry = AlertManager()
    slack = SlackAlertDestination("https://example.invalid/controlled", "#old")
    registry.register_destination("slack", slack)
    monkeypatch.setattr(manager, "_alert_manager", registry)
    calls = []
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: calls.append(kwargs["json"]) or SimpleNamespace(status_code=200),
    )
    assert alert_sink.AlertManagerSink().send_alert(
        title="controlled", message="identifiers", run_id="run-1"
    )
    assert calls[0]["channel"] == "#controlled"
    assert slack.channel == "#old"


def asset(
    identity,
    observed=None,
    sla=None,
    layer=None,
    freshness_source="dagster_materialization",
    freshness_reason=None,
    relation=None,
):
    materialized_at = observed if freshness_source == "dagster_materialization" else None
    return v1_assets.AssetView(
        id=identity,
        key=[identity],
        description=None,
        compute_kind=None,
        group_name="transform",
        is_source=False,
        dependencies=[],
        last_materialization_at=materialized_at,
        last_run_id=None,
        freshness_observed_at=observed,
        freshness_source=freshness_source if observed is not None else None,
        freshness_reason=freshness_reason,
        freshness_sla_seconds=sla,
        relation=relation,
        layer=layer,
        repository_name="iot_repo",
        check_definition_scope="unique",
    )


def test_freshness_precedence_boundaries_future_and_missing():
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    defaults = storage.OperationalSettings(bronze_minutes=10)
    assets = [
        asset("default", now - timedelta(minutes=10), layer="bronze"),
        asset("declared", now - timedelta(seconds=61), sla=60),
        asset("override", now - timedelta(seconds=61), sla=600),
        asset("missing", sla=60),
        asset("unclassified", now),
        asset("future", now + timedelta(seconds=1), sla=60),
    ]
    counts = v1_assets._count_freshness(assets, {"override": 60}, defaults, now)
    assert counts.model_dump() == {"fresh": 1, "stale": 2, "unknown": 3}
    node = {
        "assetKey": {"path": ["reading"]},
        "groupName": "registry",
        "metadataEntries": [
            {"label": "phlo/layer", "text": "bronze"},
            {"label": "sla", "jsonString": '{"freshness_hours":4,"quality_threshold":0.995}'},
        ],
        "internalFreshnessPolicy": {
            "__typename": "TimeWindowFreshnessPolicy",
            "failWindowSeconds": 7200,
        },
    }
    assert v1_assets._declared_asset_policy(node) == ("bronze", 14400)
    node["metadataEntries"] = []
    assert v1_assets._declared_asset_policy(node) == (None, 7200)


def test_snapshot_freshness_is_separate_from_failed_or_missing_materialization():
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    snapshot = asset(
        "snapshot-only",
        now - timedelta(minutes=1),
        sla=60,
        freshness_source="iceberg_snapshot",
    )
    failed_run = asset(
        "failed-run",
        now - timedelta(days=2),
        sla=60,
        freshness_source="iceberg_snapshot",
    ).model_copy(update={"last_materialization_at": None, "last_run_id": None})

    assert snapshot.last_materialization_at is None
    assert snapshot.last_run_id is None
    assert failed_run.last_materialization_at is None
    assert failed_run.last_run_id is None
    assert v1_assets._count_freshness([snapshot], {}, None, now).model_dump() == {
        "fresh": 1,
        "stale": 0,
        "unknown": 0,
    }
    assert v1_assets._count_freshness([failed_run], {}, None, now).stale == 1


def test_snapshot_freshness_rejects_future_invalid_and_missing_evidence():
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    assets = [
        asset(
            "future-snapshot",
            now + timedelta(seconds=1),
            sla=60,
            freshness_source="iceberg_snapshot",
        ),
        asset(
            "invalid-snapshot",
            sla=60,
            freshness_source=None,
            freshness_reason="invalid_observation",
        ),
        asset("no-snapshot", sla=60, freshness_source=None, freshness_reason="no_observation"),
    ]
    assert [
        v1_assets._apply_asset_freshness(item, {}, None, now).freshness_reason for item in assets
    ] == ["future_observation", "invalid_observation", "no_observation"]
    assert v1_assets._count_freshness(assets, {}, None, now).unknown == 3


def test_aggregate_snapshot_reads_are_bounded_and_leave_the_remainder_unknown(monkeypatch):
    now = datetime.now(UTC).replace(microsecond=0)
    assets = [
        asset("first", sla=600, relation="raw.first"),
        asset("second", sla=600, relation="raw.second"),
    ]
    calls = []
    monkeypatch.setattr(v1_assets, "_AGGREGATE_FRESHNESS_METADATA_LIMIT", 1)

    def metadata(table_name, ref):
        calls.append((table_name, ref))
        return {
            "row_count": 1,
            "size_bytes": 10,
            "snapshot_id": "9007199254740993",
            "freshness_observed_at": now - timedelta(minutes=1),
            "freshness_reason": None,
        }

    monkeypatch.setattr(v1_assets, "_iceberg_asset_metadata", metadata)
    enriched = asyncio.run(v1_assets._assets_with_freshness_evidence(assets, "prod", "main"))

    assert calls == [("raw.first", "main")]
    assert enriched[0].freshness_source == "iceberg_snapshot"
    assert enriched[1].freshness_source is None
    assert enriched[1].freshness_reason == "catalog_unavailable"
    assert v1_assets._count_freshness(enriched, {}, None, now).model_dump() == {
        "fresh": 1,
        "stale": 0,
        "unknown": 1,
    }


def test_quality_failed_latest_and_no_checks_are_not_passing(monkeypatch):
    definitions = [v1_assets.CheckDefinition(name=name) for name in ("bad", "pending")]

    async def inventory(*args, **kwargs):
        return [
            {
                "assetKey": {"path": ["readings"]},
                "assetChecksOrError": {
                    "__typename": "AssetChecks",
                    "checks": [definition.model_dump() for definition in definitions],
                },
            }
        ]

    async def groups(*args):
        def event(name, status, passed, hour):
            return v1_assets.CheckExecution(
                status=status,
                run_id="scoped",
                timestamp=datetime(2026, 10, 2, hour, tzinfo=UTC),
                check_name=name,
                passed=passed,
                severity="ERROR",
                metadata=[],
            )

        return (
            {
                "bad": [event("bad", "SUCCEEDED", True, 10), event("bad", "FAILED", False, 11)],
                "pending": [
                    event("pending", "SUCCEEDED", True, 10),
                    event("pending", "IN_PROGRESS", None, 11),
                ],
            },
            {"bad": 2, "pending": 2},
            set(),
        )

    monkeypatch.setattr(v1_assets, "_asset_check_inventory", inventory)
    monkeypatch.setattr(v1_assets, "_asset_check_execution_groups", groups)
    evidence = asyncio.run(
        v1_assets._overview_quality_checks([asset("readings")], "production_jobs", "main")
    )
    assert evidence.counts.model_dump() == {"passing": 0, "total": 2, "unevaluated": 1}
    assert evidence.failing_assets == ["readings"]
    definitions.clear()
    evidence = asyncio.run(
        v1_assets._overview_quality_checks([asset("readings")], "production_jobs", "main")
    )
    assert evidence.status == "unknown" and evidence.counts is None


def test_same_key_quality_is_scoped_to_location_and_ref(monkeypatch):
    def execution(identity, location, ref, passed, hour):
        return {
            "runId": identity,
            "timestamp": datetime(2026, 10, 2, hour, tzinfo=UTC).timestamp(),
            "status": "SUCCEEDED" if passed else "FAILED",
            "evaluation": {"success": passed, "severity": "ERROR", "metadataEntries": []},
            "run": {
                "runId": identity,
                "repositoryOrigin": {
                    "repositoryName": "iot_repo",
                    "repositoryLocationName": location,
                },
                "tags": [{"key": "phlo/ref", "value": ref}],
            },
        }

    rows = [
        execution("stage", "staging", "dev", True, 12),
        execution("wrong-ref", "production", "dev", True, 11),
        execution("prod", "production", "main", False, 10),
    ]

    async def inventory(*args, **kwargs):
        return [
            {
                "assetKey": {"path": ["same-key"]},
                "assetChecksOrError": {
                    "__typename": "AssetChecks",
                    "checks": [{"name": "controlled"}],
                },
            }
        ]

    async def graphql(*args):
        return {"data": {"assetCheckExecutions": rows}}

    monkeypatch.setattr(v1_assets, "_asset_check_inventory", inventory)
    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    prod = asyncio.run(
        v1_assets._overview_quality_checks([asset("same-key")], "production", "main")
    )
    stage = asyncio.run(v1_assets._overview_quality_checks([asset("same-key")], "staging", "dev"))
    assert prod.counts.model_dump() == {"passing": 0, "total": 1, "unevaluated": 0}
    assert prod.failing_assets == ["same-key"]
    assert stage.counts.model_dump() == {"passing": 1, "total": 1, "unevaluated": 0}
    assert stage.failing_assets == []


@pytest.fixture
def repository_check_source(monkeypatch):
    repositories = []
    for location in ("iot_prod", "iot_staging"):
        node = {
            "assetKey": {"path": ["same-key"]},
            "repository": {"name": "iot_repo", "location": {"name": location}},
            "assetChecksOrError": {
                "__typename": "AssetChecks",
                "checks": [
                    {"name": "volume", "description": location},
                    {"name": "never_run", "description": None},
                ],
            },
        }
        repositories.append(
            {"name": "iot_repo", "location": {"name": location}, "assetNodes": [node]}
        )
    rows = [
        {
            "runId": identity,
            "timestamp": datetime(2026, 10, 2, hour, tzinfo=UTC).timestamp(),
            "status": "SUCCEEDED" if passed else "FAILED",
            "evaluation": {"success": passed, "severity": "ERROR", "metadataEntries": []},
            "run": {
                "runId": identity,
                "repositoryOrigin": {
                    "repositoryName": "iot_repo",
                    "repositoryLocationName": location,
                },
                "tags": [{"key": "phlo/ref", "value": ref}],
            },
        }
        for identity, location, ref, passed, hour in (
            ("prod-pass", "iot_prod", "main", True, 12),
            ("stage-fail", "iot_staging", "dev", False, 10),
            ("wrong-ref", "iot_staging", "main", True, 11),
        )
    ]

    async def graphql(query, variables=None):
        if "V1AssetChecks" in query:
            assert variables["limit"] == v1_assets._CHECK_DEFINITION_LIMIT + 1
            if "repositoriesOrError" in query:
                selector = variables["repositorySelector"]
                return {
                    "data": {
                        "repositoriesOrError": {
                            "__typename": "RepositoryConnection",
                            "nodes": [
                                repository
                                for repository in repositories
                                if repository["location"]["name"]
                                == selector["repositoryLocationName"]
                                and repository["name"] == selector["repositoryName"]
                            ],
                        }
                    }
                }
            # Dagster's global lookup resolves one preferred duplicate key.
            return {"data": {"assetNodes": repositories[0]["assetNodes"]}}
        assert "V1AssetCheckExecutions" in query
        assert variables["limit"] == v1_assets._CHECK_EXECUTION_SCAN_LIMIT
        return {
            "data": {"assetCheckExecutions": rows if variables["checkName"] == "volume" else []}
        }

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    return repositories


def test_duplicate_key_check_definitions_and_quality_are_repository_bound(repository_check_source):
    for location, ref, passing in (("iot_prod", "main", 1), ("iot_staging", "dev", 0)):
        definitions = asyncio.run(
            v1_assets._asset_check_definitions(["same-key"], location, "iot_repo")
        )
        assert [(item.name, item.description) for item in definitions] == [
            ("volume", location),
            ("never_run", None),
        ]
        quality = asyncio.run(
            v1_assets._overview_quality_checks([asset("same-key")], location, ref)
        )
        assert quality.status == "available"
        assert quality.counts.model_dump() == {"passing": passing, "total": 2, "unevaluated": 1}
        assert quality.failing_assets == ([] if passing else ["same-key"])
    repository_check_source[1]["assetNodes"][0]["assetChecksOrError"]["checks"] = []
    empty = asyncio.run(
        v1_assets._overview_quality_checks([asset("same-key")], "iot_staging", "dev")
    )
    assert (
        empty.status == "unknown" and empty.counts is None and empty.reason == "no_checks_defined"
    )


def test_overview_fetches_definitions_once_per_selected_repository(
    repository_check_source, monkeypatch
):
    keys = ["same-key", *(f"other-{index}" for index in range(10))]
    for repository in repository_check_source:
        repository["assetNodes"].extend(
            {
                "assetKey": {"path": [key]},
                "repository": {"name": "iot_repo", "location": repository["location"]},
                "assetChecksOrError": {"__typename": "AssetChecks", "checks": []},
            }
            for key in keys[1:]
        )
    graphql = v1_assets._graphql
    calls = []

    async def counted(query, variables=None):
        if "V1AssetChecks" in query:
            assert "repositoriesOrError(repositorySelector: $repositorySelector)" in query
            calls.append(variables)
        return await graphql(query, variables)

    monkeypatch.setattr(v1_assets, "_graphql", counted)
    inventory = [asset(key) for key in keys]
    for location, ref, passing in (("iot_prod", "main", 1), ("iot_staging", "dev", 0)):
        for _ in range(2):
            calls.clear()
            evidence = asyncio.run(v1_assets._overview_quality_checks(inventory, location, ref))
            assert evidence.counts.model_dump() == {
                "passing": passing,
                "total": 2,
                "unevaluated": 1,
            }
            assert len(calls) == 1
            assert calls[0]["repositorySelector"] == {
                "repositoryName": "iot_repo",
                "repositoryLocationName": location,
            }


def test_check_inventory_missing_identity_and_asset_limit_do_not_query(monkeypatch):
    async def unexpected(*args):
        pytest.fail("Missing repository identity or an exceeded asset bound must not query Dagster")

    monkeypatch.setattr(v1_assets, "_graphql", unexpected)
    missing = asset("same-key").model_copy(update={"repository_name": None})
    with pytest.raises(BackendUnavailableError, match="repository identity"):
        asyncio.run(v1_assets._overview_quality_checks([missing], "iot_staging", "dev"))
    bounded = asyncio.run(
        v1_assets._overview_quality_checks(
            [asset(str(index)) for index in range(v1_assets._OVERVIEW_CHECK_ASSET_LIMIT + 1)],
            "iot_staging",
            "dev",
        )
    )
    assert bounded.status == "unknown" and bounded.counts is None
    assert bounded.reason == "asset_limit_exceeded"
    empty = asyncio.run(v1_assets._overview_quality_checks([], "iot_staging", "dev"))
    assert empty.status == "unknown" and empty.reason == "no_checks_defined"


def test_selected_repository_identity_is_internal():
    selected = v1_assets._asset_view(
        {
            "assetKey": {"path": ["same-key"]},
            "repository": {"name": "iot_repo", "location": {"name": "iot_staging"}},
            "isMaterializable": True,
            "dependencyKeys": [],
            "assetMaterializations": [],
        },
        "dev",
    )
    assert selected.repository_name == "iot_repo"
    assert "repository_name" not in selected.model_dump()
    assert selected.check_definition_scope == "unknown"
    assert "check_definition_scope" not in selected.model_dump()


@pytest.mark.parametrize("failure", ["node_location", "node_name", "duplicate", "definition_bound"])
def test_repository_check_definitions_reject_unsafe_evidence(repository_check_source, failure):
    selected = repository_check_source[1]["assetNodes"]
    if failure == "node_location":
        selected[0]["repository"]["location"]["name"] = "iot_prod"
        expected = BadGatewayError
    elif failure == "node_name":
        selected[0]["repository"]["name"] = "wrong-repository"
        expected = BadGatewayError
    elif failure == "duplicate":
        selected.append(selected[0].copy())
        expected = BackendUnavailableError
    else:
        selected[0]["assetChecksOrError"]["checks"] = [
            {"name": str(i)} for i in range(v1_assets._CHECK_DEFINITION_LIMIT + 1)
        ]
        expected = BackendUnavailableError
    with pytest.raises(expected):
        asyncio.run(v1_assets._asset_check_definitions(["same-key"], "iot_staging", "iot_repo"))
    with pytest.raises(expected):
        asyncio.run(v1_assets._overview_quality_checks([asset("same-key")], "iot_staging", "dev"))


def test_runtime_observations_are_bound_and_failed_replicas_stay_visible(monkeypatch):
    def container(name, state, status, project="prod-stack"):
        return {
            "State": state,
            "Status": status,
            "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": name},
        }

    containers = [
        container("worker", "running", "Up (healthy)"),
        container("worker", "running", "Up (unhealthy)"),
        container("other", "running", "Up (unhealthy)", "staging-stack"),
        container("completed", "exited", "Exited (0)"),
        container("failed", "exited", "Exited (137)"),
        container("no-probe", "running", "Up"),
    ]
    monkeypatch.setattr(observatory_services, "docker_runtime_metadata", lambda container: {})
    monkeypatch.setattr(v1, "load_docker_containers", lambda: containers)
    monkeypatch.setattr(
        v1.ServiceDiscovery,
        "discover",
        lambda self: {"unused": object(), "disabled": SimpleNamespace(disabled=True)},
    )
    monkeypatch.setattr(v1, "project_env_value", lambda name: None)

    async def locations():
        return {"prod-location"}

    monkeypatch.setattr(v1, "_locations", locations)
    bound = EnvironmentTarget(
        dagster_location="prod-location", nessie_ref="main", compose_project="prod-stack"
    )
    result = {item.id: item for item in asyncio.run(v1._service_snapshots(bound))}
    assert "other" not in result
    assert result["worker"].status == result["failed"].status == "unhealthy"
    assert result["completed"].status == result["disabled"].status == "inactive"
    assert result["unused"].observed_at is None and result["unused"].status == "unknown"
    assert result["no-probe"].status == "unknown" and result["no-probe"].runtime_state == "running"
    assert result["dagster"].status == "healthy" and result["dagster"].observed_at is not None
    unbound = EnvironmentTarget(dagster_location="prod-location", nessie_ref="main")
    assert "worker" not in {item.id for item in asyncio.run(v1._service_snapshots(unbound))}


def test_environment_health_urls_probe_only_bound_enabled_services(monkeypatch):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/failed":
            return httpx.Response(503)
        if request.url.path == "/offline":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    @asynccontextmanager
    async def client_context():
        yield client

    monkeypatch.setattr(v1, "backend_client", lambda: client_context())
    monkeypatch.setattr(
        v1.ServiceDiscovery,
        "discover",
        lambda self: {
            "external": object(),
            "failed": object(),
            "offline": object(),
            "disabled": SimpleNamespace(disabled=True),
        },
    )
    monkeypatch.setattr(v1, "project_env_value", lambda name: None)

    async def locations():
        return {"prod-location"}

    monkeypatch.setattr(v1, "_locations", locations)
    target = EnvironmentTarget(
        dagster_location="prod-location",
        nessie_ref="main",
        service_health_urls={
            "external": "https://prod-health.example:8443/private-health-path",
            "failed": "https://health.example/failed",
            "offline": "https://health.example/offline",
            "disabled": "https://health.example/ok",
            "unknown-definition": "https://health.example/ok",
        },
    )
    try:
        result = {item.id: item for item in asyncio.run(v1._service_snapshots(target))}
    finally:
        asyncio.run(client.aclose())

    assert result["external"].status == "healthy"
    assert result["external"].runtime_state == "unknown"
    assert result["external"].health_origin == "https://prod-health.example:8443"
    assert result["failed"].health_origin == "https://health.example"
    assert result["dagster"].health_origin is None
    assert "private-health-path" not in result["external"].model_dump_json()
    assert result["failed"].status == "unhealthy"
    assert result["offline"].status == "unavailable"
    assert result["disabled"].status == "inactive"
    assert result["disabled"].reason == "disabled"
    assert "unknown-definition" not in result
    assert len(calls) == 3


def test_environment_target_rejects_health_urls_with_secrets_or_query_parameters():
    for url in (
        "https://user:password@health.example/ready",
        "https://health.example/ready?token=secret",
        "https://health.example/ready#fragment",
    ):
        with pytest.raises(ValidationError):
            EnvironmentTarget(
                dagster_location="prod-location",
                nessie_ref="main",
                service_health_urls={"external": url},
            )


def test_environment_mapping_allows_operator_owned_shared_health_urls(monkeypatch):
    shared = "https://shared.example/ready"
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {
                    "dagster_location": "prod-location",
                    "nessie_ref": "main",
                    "service_health_urls": {"catalog": shared},
                },
                "staging": {
                    "dagster_location": "staging-location",
                    "nessie_ref": "candidate",
                    "service_health_urls": {"catalog": shared},
                },
            }
        ),
    )

    targets = v1._targets()
    assert str(targets["prod"].service_health_urls["catalog"]) == shared
    assert str(targets["staging"].service_health_urls["catalog"]) == shared


def test_probe_deadline_marks_unobserved_services_unavailable(monkeypatch):
    monkeypatch.setattr(v1, "_SERVICE_PROBE_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(
        v1.ServiceDiscovery,
        "discover",
        lambda self: {"fast": object(), "slow": object()},
    )
    monkeypatch.setattr(v1, "project_env_value", lambda name: None)

    async def locations():
        return {"prod-location"}

    async def probe(name, url):
        if name == "slow":
            await asyncio.sleep(1)
        return v1.ServiceSnapshot(
            id=name,
            status="healthy",
            observed_at=datetime.now(UTC),
            response_time_seconds=0.001,
            definition_state="configured",
            reason="health_probe_ok",
        )

    monkeypatch.setattr(v1, "_locations", locations)
    monkeypatch.setattr(v1, "_service_health_probe", probe)
    result = {
        item.id: item
        for item in asyncio.run(
            v1._service_snapshots(
                EnvironmentTarget(
                    dagster_location="prod-location",
                    nessie_ref="main",
                    service_health_urls={
                        "fast": "https://health.example/fast",
                        "slow": "https://health.example/slow",
                    },
                )
            )
        )
    }

    assert result["fast"].status == "healthy"
    assert result["slow"].status == "unavailable"
    assert result["slow"].reason == "health_probe_deadline_exceeded"
    assert result["slow"].observed_at is not None
    assert result["slow"].response_time_seconds is None


def test_bound_health_probe_tasks_are_cleaned_up_when_caller_is_cancelled(monkeypatch):
    async def run():
        started = asyncio.Event()
        finalized = asyncio.Event()

        async def probe(name, url):
            started.set()
            try:
                await asyncio.Future()
            finally:
                finalized.set()

        monkeypatch.setattr(v1, "_service_health_probe", probe)
        work = asyncio.create_task(
            v1._bound_service_health_snapshots(
                EnvironmentTarget(
                    dagster_location="prod-location",
                    nessie_ref="main",
                    service_health_urls={"blocked": "https://health.example/blocked"},
                ),
                {"blocked": object()},
            )
        )
        await started.wait()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work

        assert finalized.is_set()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


def test_bound_health_probe_failure_cleans_up_unfinished_sibling(monkeypatch):
    async def run():
        sibling_started = asyncio.Event()
        sibling_finalized = asyncio.Event()

        async def fail(name, url):
            await sibling_started.wait()
            raise RuntimeError("probe failed unexpectedly")

        async def sibling(name, url):
            sibling_started.set()
            try:
                await asyncio.Future()
            finally:
                sibling_finalized.set()

        async def probe(name, url):
            if name == "failure":
                return await fail(name, url)
            return await sibling(name, url)

        monkeypatch.setattr(v1, "_service_health_probe", probe)
        with pytest.raises(RuntimeError, match="probe failed unexpectedly"):
            await v1._bound_service_health_snapshots(
                EnvironmentTarget(
                    dagster_location="prod-location",
                    nessie_ref="main",
                    service_health_urls={
                        "failure": "https://health.example/failure",
                        "sibling": "https://health.example/sibling",
                    },
                ),
                {"failure": object(), "sibling": object()},
            )

        assert sibling_finalized.is_set()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())
