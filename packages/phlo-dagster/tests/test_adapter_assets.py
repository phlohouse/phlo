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
