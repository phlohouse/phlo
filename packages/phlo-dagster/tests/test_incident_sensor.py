"""Tests for evidence-bound Dagster incident signals."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import dagster as dg
import pytest
import requests

from phlo.plugins import observatory_settings as storage
from phlo_dagster import incident_sensor


@pytest.fixture(autouse=True)
def operational_settings(monkeypatch):
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod-location", "nessie_ref": "main"},
                "staging": {"dagster_location": "staging-location", "nessie_ref": "dev"},
            }
        ),
    )
    storage._reset_memory_service()

    def save(**values):
        storage.get_settings_service().put(
            storage.SettingsScope.GLOBAL,
            storage.ADMIN_SETTINGS_NAMESPACE,
            {
                "version": 9,
                "values": {f"observatory.settings.{key}": value for key, value in values.items()},
            },
        )

    save(**{"freshness.open_incident_on_breach": True})
    yield save
    storage._reset_memory_service()


def _entry(
    *,
    passed: bool = False,
    asset: tuple[str, ...] = ("orders",),
    source: str | None = None,
    severity: str = "ERROR",
    run_id: str = "run-1",
) -> SimpleNamespace:
    metadata = {"source": source} if source is not None else {}
    evaluation = SimpleNamespace(
        passed=passed,
        check_name="contract",
        partition=None,
        severity=SimpleNamespace(value=severity),
        metadata=metadata,
    )
    return SimpleNamespace(
        run_id=run_id,
        dagster_event=SimpleNamespace(
            asset_key=SimpleNamespace(path=asset),
            asset_check_evaluation=evaluation,
        ),
    )


def test_failed_dlt_contract_and_generic_check_use_distinct_signal_kinds() -> None:
    dlt = incident_sensor._failure(_entry(asset=("dlt_orders",), source="pandera"))
    generic = incident_sensor._failure(_entry())

    assert dlt is not None and dlt["kind"] == "dlt_contract_violation"
    assert generic is not None and generic["kind"] == "failed_check"
    assert incident_sensor._failure(_entry(passed=True)) is None
    assert incident_sensor._failure(_entry(severity="WARN")) is None


def test_location_mapping_is_explicit_and_validated() -> None:
    assert incident_sensor._location_environments('{"prod-location":"prod"}') == {
        "prod-location": "prod"
    }
    with pytest.raises(ValueError, match="maps location names"):
        incident_sensor._location_environments('{"prod-location":"production"}')
    with pytest.raises(ValueError, match="maps location names"):
        incident_sensor._location_environments('{"prod-location":[]}')


def test_production_forwarding_uses_existing_orchestration_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(incident_sensor, "requires_http_authorization", lambda: True)
    monkeypatch.setattr(incident_sensor, "load_service_identity_credentials", lambda: object())

    def scoped_headers(caller: str, **kwargs: object) -> dict[str, str]:
        calls.append({"caller": caller, **kwargs})
        return {"Authorization": "Bearer scoped"}

    monkeypatch.setattr(incident_sensor, "build_scoped_service_headers", scoped_headers)

    assert incident_sensor._service_headers() == {"Authorization": "Bearer scoped"}
    assert calls[0]["caller"] == "phlo-orchestration"
    assert calls[0]["audience"] == "phlo-api"
    assert calls[0]["scp"] == ("api:orchestrate",)


@pytest.mark.parametrize(
    ("environment", "location"),
    [("prod", "prod-location"), ("staging", "staging-location")],
)
def test_sensor_maps_run_origin_and_posts_replay_safe_signal(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    location: str,
) -> None:
    monkeypatch.setenv(incident_sensor._LOCATION_ENV_MAP, json.dumps({location: environment}))
    monkeypatch.setenv(incident_sensor._API_URL, "http://phlo-api:4000")
    monkeypatch.setattr(
        incident_sensor, "_service_headers", lambda: {"Authorization": "Bearer test"}
    )

    event_entry = _entry(asset=("dlt_orders",), source="domain")
    record = SimpleNamespace(storage_id=73, event_log_entry=event_entry)
    run = SimpleNamespace(
        remote_job_origin=SimpleNamespace(
            repository_origin=SimpleNamespace(
                code_location_origin=SimpleNamespace(location_name=location)
            )
        )
    )
    instance = dg.DagsterInstance.ephemeral()
    instance.get_event_records = lambda *_args, **_kwargs: [record]
    instance.get_run_by_id = lambda run_id: run if run_id == "run-1" else None
    context = dg.build_sensor_context(instance=instance, cursor="72")
    requests: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

    def post(url: str, **kwargs: object) -> Response:
        requests.append({"url": url, **kwargs})
        return Response()

    class PolicyResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"env": environment, "items": [], "next_cursor": None}

    monkeypatch.setattr(incident_sensor.requests, "post", post)
    monkeypatch.setattr(incident_sensor.requests, "get", lambda *_args, **_kwargs: PolicyResponse())
    try:
        result = incident_sensor.phlo_incident_signal_sensor.evaluate_tick(context)
        replay = incident_sensor.phlo_incident_signal_sensor.evaluate_tick(context)
    finally:
        instance.dispose()
    assert result.cursor == "73"
    assert replay.cursor == result.cursor
    assert len(requests) == 2
    request = requests[0]
    replay_request = requests[1]
    assert request["url"] == f"http://phlo-api:4000/api/v1/incidents?env={environment}"
    assert request["headers"]["Idempotency-Key"] == f"dagster-check:{location}:73"
    assert replay_request["headers"]["Idempotency-Key"] == request["headers"]["Idempotency-Key"]
    assert request["json"]["kind"] == "dlt_contract_violation"


def test_sensor_bootstraps_at_event_head_without_replaying_historical_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(incident_sensor._LOCATION_ENV_MAP, '{"prod-location":"prod"}')
    monkeypatch.setenv(incident_sensor._API_URL, "http://phlo-api:4000")
    event = SimpleNamespace(storage_id=75, event_log_entry=_entry())
    instance = dg.DagsterInstance.ephemeral()
    instance.get_event_records = lambda *_args, **kwargs: (
        [event] if kwargs.get("ascending") is False else []
    )
    monkeypatch.setattr(incident_sensor, "_asset_policies", lambda *_args: [])
    try:
        result = incident_sensor.phlo_incident_signal_sensor.evaluate_tick(
            dg.build_sensor_context(instance=instance)
        )
    finally:
        instance.dispose()
    assert result.cursor == "75"
    assert result.skip_message == "Sensor function returned an empty result"


def test_freshness_uses_explicit_sla_and_latest_success_in_same_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        incident_sensor._LOCATION_ENV_MAP,
        '{"prod-location":"prod","staging-location":"staging"}',
    )
    monkeypatch.setenv(incident_sensor._API_URL, "http://phlo-api:4000")
    monkeypatch.setattr(incident_sensor, "_service_headers", lambda: {})

    @dg.asset
    def orders() -> int:
        return 1

    repository = dg.Definitions(assets=[orders]).get_repository_def()
    old_timestamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    records = [
        SimpleNamespace(
            storage_id=102,
            event_log_entry=SimpleNamespace(
                run_id="stage-success",
                timestamp=old_timestamp,
                dagster_event=SimpleNamespace(
                    asset_materialization=SimpleNamespace(partition=None)
                ),
            ),
        ),
        SimpleNamespace(
            storage_id=101,
            event_log_entry=SimpleNamespace(
                run_id="prod-failed",
                timestamp=old_timestamp,
                dagster_event=SimpleNamespace(
                    asset_materialization=SimpleNamespace(partition=None)
                ),
            ),
        ),
        SimpleNamespace(
            storage_id=100,
            event_log_entry=SimpleNamespace(
                run_id="prod-success",
                timestamp=old_timestamp,
                dagster_event=SimpleNamespace(
                    asset_materialization=SimpleNamespace(partition=None)
                ),
            ),
        ),
    ]
    runs = {
        "stage-success": ("SUCCESS", "staging-location"),
        "prod-failed": ("FAILURE", "prod-location"),
        "prod-success": ("SUCCESS", "prod-location"),
    }
    instance = dg.DagsterInstance.ephemeral()
    instance.get_event_records = lambda event_filter, **_kwargs: (
        [] if event_filter.event_type == dg.DagsterEventType.ASSET_CHECK_EVALUATION else records
    )
    instance.get_run_by_id = lambda run_id: SimpleNamespace(
        status=SimpleNamespace(value=runs[run_id][0]),
        tags={"phlo/ref": "main" if runs[run_id][1] == "prod-location" else "dev"},
        remote_job_origin=SimpleNamespace(
            repository_origin=SimpleNamespace(
                code_location_origin=SimpleNamespace(location_name=runs[run_id][1])
            )
        ),
    )

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def json(self) -> dict[str, object]:
            return self.payload

        def raise_for_status(self) -> None:
            pass

    def get(_url: str, *, params: dict[str, object], **_kwargs: object) -> Response:
        env = str(params["env"])
        items = [{"asset_id": "orders", "freshness_sla_seconds": 3600}] if env == "prod" else []
        return Response({"env": env, "items": items, "next_cursor": None})

    incidents: list[dict[str, object]] = []

    class Accepted:
        def raise_for_status(self) -> None:
            pass

    def post(_url: str, *, json: dict[str, object], **_kwargs: object) -> Accepted:
        incidents.append(json)
        return Accepted()

    monkeypatch.setattr(incident_sensor.requests, "get", get)
    monkeypatch.setattr(incident_sensor.requests, "post", post)
    try:
        result = incident_sensor.phlo_incident_signal_sensor.evaluate_tick(
            dg.build_sensor_context(instance=instance, repository_def=repository, cursor="0")
        )
    finally:
        instance.dispose()

    assert result.cursor == "0"
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "freshness_breach"
    assert incidents[0]["evidence"]["successful_run_id"] == "prod-success"
    assert incidents[0]["evidence"]["materialization_event_id"] == 100


def test_partitioned_asset_policy_is_not_used_for_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dg.asset(partitions_def=dg.DailyPartitionsDefinition(start_date="2025-01-01"))
    def partitioned_orders() -> int:
        return 1

    repository = dg.Definitions(assets=[partitioned_orders]).get_repository_def()
    context = dg.build_sensor_context(repository_def=repository)
    assert incident_sensor._asset_key_for_policy(context, "partitioned_orders") is None


def test_durable_freshness_defaults_precedence_and_hold_request(operational_settings, monkeypatch):
    monkeypatch.setenv("PHLO_OBSERVATORY_ENVIRONMENT", "prod")
    operational_settings(
        **{
            "freshness.open_incident_on_breach": True,
            "freshness.bronze_minutes": "60",
            "freshness.hold_downstream": True,
        }
    )

    @dg.asset(metadata={"sla": {"freshness_hours": 1}, "phlo/layer": "bronze"})
    def overridden():
        return 1

    @dg.asset(
        metadata={"sla": {"freshness_hours": 1}},
        freshness_policy=dg.FreshnessPolicy.time_window(fail_window=timedelta(hours=8)),
    )
    def declared():
        return 1

    @dg.asset(
        metadata={"phlo/layer": "bronze"},
        freshness_policy=dg.FreshnessPolicy.time_window(fail_window=timedelta(hours=3)),
    )
    def native_policy():
        return 1

    @dg.asset(metadata={"phlo/layer": "bronze"})
    def defaulted():
        return 1

    @dg.asset(metadata={"phlo/layer": "bronze"})
    def missing():
        return 1

    @dg.asset(group_name="ingest")
    def unclassified():
        return 1

    repository = dg.Definitions(
        assets=[overridden, declared, native_policy, defaulted, missing, unclassified]
    ).get_repository_def()
    context = dg.build_sensor_context(repository_def=repository)
    monkeypatch.setattr(
        incident_sensor,
        "_asset_policies",
        lambda url, env: (
            [{"asset_id": "overridden", "freshness_sla_seconds": 28800}] if env == "prod" else []
        ),
    )

    def materialized(context, key, env, environments):
        if key.path == ["missing"]:
            return None
        return {
            "location": "prod-location",
            "storage_id": 91,
            "run_id": "success",
            "materialized_at": datetime.now(UTC) - timedelta(hours=2),
        }

    monkeypatch.setattr(incident_sensor, "_latest_successful_materialization", materialized)
    calls = []

    def send(*args):
        calls.append(args)

    monkeypatch.setattr(incident_sensor, "_send_incident", send)
    mapping = {"prod-location": "prod", "staging-location": "staging"}
    incident_sensor._detect_freshness_breaches(context, "http://controlled", mapping)
    assert {call[3]["asset_id"] for call in calls} == {"declared", "defaulted"}
    assert all(call[1] == "prod" and call[3]["pause_downstream"] is True for call in calls)
    assert all(call[3]["evidence"]["settings_revision"] == 9 for call in calls)
    operational_settings(
        **{"freshness.open_incident_on_breach": False, "freshness.hold_downstream": True}
    )
    calls.clear()
    incident_sensor._detect_freshness_breaches(context, "http://controlled", mapping)
    assert calls == []


def test_requested_hold_surfaces_api_authorization_failure(monkeypatch):
    monkeypatch.setattr(
        incident_sensor,
        "_service_headers",
        lambda: {"Authorization": "Bearer api-orchestrate-only"},
    )
    calls = []

    class Denied:
        def raise_for_status(self):
            raise requests.HTTPError("403 independent pause permission required")

    def post(url, **kwargs):
        calls.append(kwargs)
        return Denied()

    monkeypatch.setattr(incident_sensor.requests, "post", post)
    with pytest.raises(requests.HTTPError, match="403"):
        incident_sensor._send_incident(
            "http://controlled",
            "prod",
            "evidence",
            {"asset_id": "declared", "pause_downstream": True},
        )
    assert calls[0]["headers"]["Authorization"] == "Bearer api-orchestrate-only"
    assert calls[0]["json"]["pause_downstream"] is True


def test_policy_source_outage_fails_sensor_without_fabricating_a_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(incident_sensor._LOCATION_ENV_MAP, '{"prod-location":"prod"}')
    monkeypatch.setenv(incident_sensor._API_URL, "http://phlo-api:4000")

    def unavailable(*_args: object) -> list[dict[str, object]]:
        raise OSError("incident policy API unavailable")

    monkeypatch.setattr(incident_sensor, "_asset_policies", unavailable)
    posts: list[object] = []
    monkeypatch.setattr(incident_sensor.requests, "post", lambda *_args, **_kwargs: posts.append(1))
    instance = dg.DagsterInstance.ephemeral()
    instance.get_event_records = lambda *_args, **_kwargs: []
    try:
        with pytest.raises(OSError, match="policy API unavailable"):
            incident_sensor.phlo_incident_signal_sensor.evaluate_tick(
                dg.build_sensor_context(instance=instance, cursor="70")
            )
    finally:
        instance.dispose()
    assert posts == []


def test_sensor_does_not_advance_cursor_when_delivery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(incident_sensor._LOCATION_ENV_MAP, '{"prod-location":"staging"}')
    monkeypatch.setenv(incident_sensor._API_URL, "http://phlo-api:4000")
    monkeypatch.setattr(incident_sensor, "_service_headers", lambda: {})
    event_entry = _entry()
    record = SimpleNamespace(storage_id=74, event_log_entry=event_entry)
    run = SimpleNamespace(
        remote_job_origin=SimpleNamespace(
            repository_origin=SimpleNamespace(
                code_location_origin=SimpleNamespace(location_name="prod-location")
            )
        )
    )
    instance = dg.DagsterInstance.ephemeral()
    instance.get_event_records = lambda *_args, **_kwargs: [record]
    instance.get_run_by_id = lambda _run_id: run

    class Response:
        def raise_for_status(self) -> None:
            raise RuntimeError("API unavailable")

    monkeypatch.setattr(incident_sensor.requests, "post", lambda *_args, **_kwargs: Response())
    try:
        with pytest.raises(RuntimeError, match="API unavailable"):
            incident_sensor.phlo_incident_signal_sensor.evaluate_tick(
                dg.build_sensor_context(instance=instance, cursor="70")
            )
    finally:
        instance.dispose()
