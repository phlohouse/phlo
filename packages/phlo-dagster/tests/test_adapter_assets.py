"""Asset policies, scoped execution, and deferred failures without external services."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import dagster as dg
import pytest
from phlo_dagster import adapter

from phlo.capabilities.specs import (
    AssetCheckSpec,
    AssetSpec,
    CheckResult,
    MaterializeResult,
    PartitionSpec,
    ResourceSpec,
    RunSpec,
)


@pytest.fixture
def execution(monkeypatch):
    active = []
    events = []
    policies = {}

    @contextmanager
    def scope(name, **kwargs):
        active.append(name)
        events.append((name, "enter", kwargs))
        try:
            yield
        except dg.Failure:
            events.append((name, "failed", kwargs))
            raise
        finally:
            active.pop()

    def asset(**kwargs):
        policies.update(kwargs)
        return lambda fn: fn

    monkeypatch.setattr(adapter.dg, "asset", asset)
    monkeypatch.setattr(
        adapter, "setup_logging", lambda **kwargs: events.append(("logging", kwargs))
    )
    monkeypatch.setattr(
        adapter.phlo_observe, "dagster_run_scope", lambda context, **kw: scope("run", **kw)
    )
    monkeypatch.setattr(adapter.phlo_observe, "dagster_step", lambda context: scope("step"))
    monkeypatch.setattr(
        adapter.phlo_observe,
        "emit_materialization",
        lambda context, **kw: scope("materialize", **kw),
    )
    monkeypatch.setattr(
        adapter.phlo_observe,
        "emit_asset_check",
        lambda context, **kw: events.append(("check", tuple(active), kw)),
    )
    return active, events, policies


def _execute(fn, **kwargs):
    spec = AssetSpec(key="raw.orders", group=None, description=None, run=RunSpec(fn=fn), **kwargs)
    execute = adapter.DagsterOrchestratorAdapter()._build_asset(spec)
    context = SimpleNamespace(run=SimpleNamespace(run_id="physical-run"), has_partition_key=False)
    return execute(context)


def test_asset_policies():
    spec = AssetSpec(
        key="raw.orders",
        group="raw",
        description="Orders",
        partitions=PartitionSpec(kind="daily"),
        deps=["raw.customers"],
        resources={"store"},
        metadata={"rows": 2},
        checks=[
            AssetCheckSpec(name="volume", asset_key="raw.orders"),
            AssetCheckSpec(name="external", asset_key="raw.orders", fn=lambda runtime: None),
        ],
        run=RunSpec(
            fn=lambda runtime: [],
            max_runtime_seconds=60,
            max_retries=2,
            retry_delay_seconds=0,
            cron="0 0 * * *",
            freshness_hours=(1, 2),
        ),
    )
    definitions = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[spec], checks=[], resources=[ResourceSpec(name="store", resource=object())]
    )
    key = dg.AssetKey(["raw", "orders"])
    asset = definitions.resolve_assets_def(key)
    assert asset.asset_deps[key] == {dg.AssetKey(["raw", "customers"])}
    assert asset.group_names_by_key[key] == "raw"
    assert "store" in asset.required_resource_keys
    assert asset.op.tags == {"dagster/max_runtime": "60"}
    assert asset.op.retry_policy == dg.RetryPolicy(max_retries=2, delay=30)
    assert asset.automation_conditions_by_key[key] == dg.AutomationCondition.on_cron("0 0 * * *")
    assert asset.get_asset_spec(key).freshness_policy == dg.FreshnessPolicy.time_window(
        warn_window=timedelta(hours=1), fail_window=timedelta(hours=2)
    )
    assert "2025-01-01" in asset.partitions_def.get_partition_keys()
    assert [check.name for check in asset.check_specs] == ["volume"]


@pytest.mark.parametrize(
    "results", [None, [], [object()], [MaterializeResult(metadata={"rows": 3}, status="success")]]
)
def test_materializations_never_suspend_with_correlation(execution, results):
    active, events, _ = execution

    def run(runtime):
        assert active == ["run", "step"]
        assert runtime.run_id == "physical-run"
        return results

    iterator = _execute(run)
    result = next(iterator)
    assert isinstance(result, dg.MaterializeResult)
    assert active == []
    with pytest.raises(StopIteration):
        next(iterator)
    assert len([event for event in events if event[0] == "materialize"]) == 1


@pytest.mark.parametrize("status", ["failure", "FAILED", "error"])
def test_checks_are_emitted_in_step_and_yielded_before_failure(execution, status):
    active, events, _ = execution
    results = [
        MaterializeResult(status="success"),
        MaterializeResult(status=status, metadata={"reason": "boom"}),
        CheckResult(check_name="volume", asset_key="raw.orders", passed=False),
        CheckResult(check_name="freshness", asset_key="raw.orders", passed=True, severity="warn"),
        CheckResult(check_name="other", asset_key="raw.orders", passed=True),
    ]
    iterator = _execute(lambda runtime: results)
    for name in ("volume", "freshness", "other"):
        assert next(iterator).check_name == name
        assert active == []
    with pytest.raises(dg.Failure, match="Asset run reported status") as exc:
        next(iterator)
    assert exc.value.metadata["reason"].value == "boom"
    assert not any(event[0] == "materialize" for event in events)
    checks = [event for event in events if event[0] == "check"]
    assert all(event[1] == ("run", "step") for event in checks)
    assert [event[2]["severity"] for event in checks] == ["error", "warn", "info"]
    assert any(event[:2] == ("step", "failed") for event in events)


def test_user_failure_and_non_dagster_exception_leave_scopes(execution):
    active, _, _ = execution

    def failed(runtime):
        raise dg.Failure("user failure")

    with pytest.raises(dg.Failure, match="user failure"):
        next(_execute(failed))
    assert active == []

    def broken(runtime):
        raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        next(_execute(broken))
    assert active == []


@pytest.fixture
def candidate_environments(monkeypatch):
    monkeypatch.setattr(
        "phlo.config.project.get_environment_targets",
        lambda: {"research": SimpleNamespace(nessie_ref="release_test")},
    )


def test_source_owned_candidate_executes_with_wap_enabled(
    execution, monkeypatch, candidate_environments
):
    from phlo.capabilities.specs import IsolatedWriteSpec

    writes = []
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.verify_wap_execution",
        lambda *args, **kwargs: pytest.fail("Candidate must not enter WAP promotion"),
    )
    spec = AssetSpec(
        key="reviewed_csv",
        group=None,
        description=None,
        run=RunSpec(
            fn=lambda runtime: writes.append(runtime.tags["phlo/ref"]),
            isolated_write=IsolatedWriteSpec(ref="sandbox-upload-abc", actor="alice"),
        ),
    )
    context = SimpleNamespace(
        run=SimpleNamespace(
            run_id="candidate-run",
            tags={"phlo/ref": "sandbox-upload-abc", "phlo/actor": "alice"},
        )
    )
    assert len(list(adapter._execute_asset(context, spec))) == 1
    assert writes == ["sandbox-upload-abc"]


@pytest.mark.parametrize(
    "changes",
    [
        {"phlo/ref": "main"},
        {"phlo/ref": "other-candidate"},
        {"phlo/actor": "bob"},
        {"phlo/actor": ""},
        {"phlo/wap_branch": "sandbox-upload-abc"},
        {"phlo/wap_binding": "forged-binding"},
    ],
)
def test_isolated_candidate_rejects_identity_or_promotion_tag_changes(
    execution, monkeypatch, candidate_environments, changes
):
    from phlo.capabilities.specs import IsolatedWriteSpec
    from phlo.exceptions import PhloConfigError

    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(enabled=True)
    )
    writes = []
    spec = AssetSpec(
        key="reviewed_csv",
        group=None,
        description=None,
        run=RunSpec(
            fn=lambda runtime: writes.append("write"),
            isolated_write=IsolatedWriteSpec(ref="sandbox-upload-abc", actor="alice"),
        ),
    )
    tags = {"phlo/ref": "sandbox-upload-abc", "phlo/actor": "alice", **changes}
    context = SimpleNamespace(run=SimpleNamespace(run_id="candidate-run", tags=tags))
    with pytest.raises(PhloConfigError, match="restricted"):
        list(adapter._execute_asset(context, spec))
    assert writes == []


@pytest.mark.parametrize(
    "day,hours,tags",
    [
        ("2026-03-29", 23, {"phlo/interval_mode": "replay"}),
        ("2026-10-25", 25, {"dagster/backfill": "historical-orders"}),
    ],
)
def test_calendar_intervals_cross_london_dst(day, hours, tags):
    """The actual Dagster runtime supplies instants, not a guessed 24-hour day."""
    from datetime import date

    spec = AssetSpec(
        key="interval_orders",
        group=None,
        description=None,
        partitions=PartitionSpec(
            kind="daily", start_date=date(2026, 1, 1), timezone="Europe/London"
        ),
    )
    partitions = adapter._asset_policies(spec)["partitions_def"]
    observed = []

    @dg.asset(partitions_def=partitions)
    def interval_orders(context):
        routing = adapter.DagsterRuntime(context).routing
        observed.append(routing)

    result = dg.materialize([interval_orders], partition_key=day, tags=tags)
    assert result.success
    assert observed[0].data_interval.end - observed[0].data_interval.start == timedelta(hours=hours)
    assert observed[0].interval_mode == "replay"


def test_caller_tags_cannot_exempt_ordinary_asset_from_wap(execution, monkeypatch):
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(enabled=True)
    )
    verified = []

    def require_manifest(run, **kwargs):
        verified.append(run.run_id)
        raise ValueError("Missing WAP manifest")

    monkeypatch.setattr("phlo_dagster.wap_launch.verify_wap_execution", require_manifest)
    writes = []
    spec = AssetSpec(
        key="ordinary",
        group=None,
        description=None,
        run=RunSpec(fn=lambda runtime: writes.append("write")),
    )
    tags = {
        "phlo/ref": "sandbox-upload-abc",
        "phlo/actor": "alice",
        "phlo/isolated_write": "true",
        "phlo/skip_wap": "true",
    }
    context = SimpleNamespace(run=SimpleNamespace(run_id="ordinary-run", tags=tags))
    with pytest.raises(ValueError, match="Missing WAP manifest"):
        list(adapter._execute_asset(context, spec))
    assert verified == ["ordinary-run"]
    assert writes == []


def test_isolated_contract_cannot_write_configured_custom_primary(
    execution, monkeypatch, candidate_environments
):
    from phlo.capabilities.specs import IsolatedWriteSpec
    from phlo.exceptions import PhloConfigError

    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(enabled=True)
    )
    writes = []
    spec = AssetSpec(
        key="unsafe_direct_write",
        group=None,
        description=None,
        run=RunSpec(
            fn=lambda runtime: writes.append("write"),
            isolated_write=IsolatedWriteSpec(ref="release_test", actor="alice"),
        ),
    )
    context = SimpleNamespace(
        run=SimpleNamespace(
            run_id="candidate-run", tags={"phlo/ref": "release_test", "phlo/actor": "alice"}
        )
    )
    with pytest.raises(PhloConfigError, match="restricted"):
        list(adapter._execute_asset(context, spec))
    assert writes == []


def test_cron_assets_get_a_running_automation_sensor() -> None:
    """A declared cron must launch runs without starting a sensor by hand."""

    def spec(key: str, cron: str | None) -> AssetSpec:
        return AssetSpec(
            key=key, group="raw", description=key, run=RunSpec(fn=lambda runtime: [], cron=cron)
        )

    definitions = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[spec("raw.scheduled", "*/10 * * * *"), spec("raw.manual", None)],
        checks=[],
        resources=[],
    )

    (sensor,) = definitions.sensors
    assert sensor.name == adapter.CRON_SENSOR_NAME
    assert sensor.default_status == dg.DefaultSensorStatus.RUNNING
    selected = sensor.asset_selection.resolve(definitions.resolve_asset_graph())
    assert selected == {dg.AssetKey(["raw", "scheduled"])}

    unscheduled = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[spec("raw.manual", None)], checks=[], resources=[]
    )
    assert not unscheduled.sensors


def _cron_spec(key: str, cron: str | None = "*/10 * * * *") -> AssetSpec:
    return AssetSpec(
        key=key, group="raw", description=key, run=RunSpec(fn=lambda runtime: [], cron=cron)
    )


def test_wap_projects_keep_cron_assets_off_the_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sensor-launched runs lack the WAP launch tags, so WAP projects keep CLI launches."""
    import phlo.infrastructure

    monkeypatch.setattr(
        phlo.infrastructure, "load_wap_config", lambda: SimpleNamespace(enabled=True)
    )

    definitions = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[_cron_spec("raw.scheduled")], checks=[], resources=[]
    )

    assert not definitions.sensors


@pytest.mark.parametrize(
    ("project_target", "expected_cron_keys"),
    [
        (dg.AssetSelection.all(), None),
        (dg.AssetSelection.assets(["raw", "first"]), {dg.AssetKey(["raw", "second"])}),
    ],
)
def test_project_automation_sensor_keeps_the_cron_assets_it_targets(
    project_target: dg.AssetSelection, expected_cron_keys: set[dg.AssetKey] | None
) -> None:
    """Dagster refuses overlapping automation sensors, so the project sensor wins."""
    from phlo_dagster.framework.definitions import _cede_cron_assets_to_project_sensors

    phlo_defs = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[_cron_spec("raw.first"), _cron_spec("raw.second")], checks=[], resources=[]
    )
    project_sensor = dg.AutomationConditionSensorDefinition("project_sensor", target=project_target)
    merged = dg.Definitions.merge(phlo_defs, dg.Definitions(sensors=[project_sensor]))

    sensors = _cede_cron_assets_to_project_sensors(merged)
    final = dg.Definitions(assets=merged.assets, sensors=sensors)
    final.get_repository_def()  # raises DagsterInvalidDefinitionError on overlap

    cron_sensors = [s for s in sensors if s.name == adapter.CRON_SENSOR_NAME]
    if expected_cron_keys is None:
        assert cron_sensors == []
    else:
        (cron_sensor,) = cron_sensors
        assert cron_sensor.default_status == dg.DefaultSensorStatus.RUNNING
        graph = final.resolve_asset_graph()
        assert cron_sensor.asset_selection.resolve(graph) == expected_cron_keys
