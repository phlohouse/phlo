"""Real Dagster execution end-to-end telemetry tests.

Unlike ``test_observe_e2e_sdk.py`` (which hand-feeds constructed hook events to
the plugin's private ``_handle``), these tests drive an actual Dagster
``materialize()`` through ``DagsterOrchestratorAdapter._build_asset`` — the
same code path a deployed asset executes. The capability run function emits
hook events through a real ``HookBus`` wired to ``ObserveHookPlugin``, so the
full chain executes for real:

    dagster execute step
      -> dagster_run_scope / dagster_step (SDK ambient correlation)
      -> capability fn -> HookBus.emit -> ObserveHookPlugin._handle
      -> phlo.telemetry shim -> observe-core builder -> capture backend
      -> emit_materialization / emit_asset_check (adapter yields)

The captured envelopes must form one correlated history on the physical
Dagster run id — the contract the observer reconstructs runs from.

The SDK requires Python >=3.12; the module skips cleanly on 3.11.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

observe_core = pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")
pytest.importorskip("phlo_observe", reason="phlo-observe SDK not installed")
dagster = pytest.importorskip("dagster", reason="dagster not installed")

import phlo.telemetry as phlo_observe  # noqa: E402


def _backend() -> Any:
    runtime = observe_core.runtime.get_runtime()
    backend = getattr(runtime, "_backend", None)
    assert backend is not None and hasattr(backend, "payloads")
    return backend


@pytest.fixture
def captured() -> Any:
    """Configure the real SDK with the in-memory capture backend."""
    phlo_observe.reset_for_tests()
    assert phlo_observe.configure(
        enabled=True, runtime_backend="capture", drains=[], service_name="phlo-test"
    )
    backend = _backend()
    yield backend
    backend.clear()
    phlo_observe.reset_for_tests()


@pytest.fixture
def bus() -> Any:
    """A real hook bus.

    No explicit registration: the first emit auto-discovers installed plugins
    via the ``phlo.plugins.hooks`` entry point — exactly how production emits
    reach ``ObserveHookPlugin``. Registering the plugin by hand would double
    it alongside the discovered one.
    """
    from phlo.hooks.bus import HookBus

    return HookBus()


def _build_ingesting_asset(bus: Any):
    """Build a real Dagster asset def whose run fn emits production hook events."""
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    from phlo.capabilities import (
        AssetCheckSpec,
        AssetSpec,
        CheckResult,
        MaterializeResult,
        RunSpec,
    )
    from phlo.hooks.emitters import (
        IngestionEventContext,
        IngestionEventEmitter,
        QualityResultEventContext,
        QualityResultEventEmitter,
    )

    def _run(runtime: Any) -> list[Any]:
        # Mirror production capability code: emit ingestion + quality hook
        # events through the bus, then report materialization + check results.
        ingestion = IngestionEventEmitter(
            IngestionEventContext(
                asset_key="bronze.users",
                table_name="bronze.users",
                group_name="bronze",
                run_id=runtime.routing.run_id,
                branch_name="pipeline-run-e2e",
                catalog_system="nessie",
            ),
            hook_bus=bus,
        )
        ingestion.emit_start()
        ingestion.emit_end(status="success", metrics={"rows_processed": 5})
        QualityResultEventEmitter(
            QualityResultEventContext(asset_key="bronze.users", run_id=runtime.routing.run_id),
            hook_bus=bus,
        ).emit_result(check_name="not_null", passed=True)
        return [
            MaterializeResult(metadata={"rows": 5}),
            CheckResult(check_name="schema_ok", passed=True, asset_key="bronze.users"),
        ]

    adapter = DagsterOrchestratorAdapter()
    return adapter._build_asset(
        AssetSpec(
            key="bronze.users",
            group=None,
            description=None,
            run=RunSpec(fn=_run),
            # Declarative check spec (fn=None): Dagster requires the asset to
            # declare check_specs before it accepts an in-band AssetCheckResult.
            checks=[
                AssetCheckSpec(
                    name="schema_ok",
                    asset_key="bronze.users",
                    fn=None,
                    blocking=False,
                )
            ],
        )
    )


def test_dagster_materialization_produces_correlated_canonical_history(
    captured: Any, bus: Any
) -> None:
    """A real materialize() leaves one correlated canonical history."""
    result = dagster.materialize([_build_ingesting_asset(bus)])
    assert result.success

    payloads = captured.payloads()
    names = [p.get("event") for p in payloads]

    # The real execution path produced every expected canonical event:
    # hook-translated ingestion lifecycle and quality result, the adapter's
    # in-band check emission, the materialization fact, and the step scope's
    # boundary event.
    assert "ingestion.extract" in names
    assert "ingestion.load" in names
    assert "asset.materialize" in names
    assert "pipeline.step" in names
    assert names.count("quality.check") == 2  # hook translation + CheckResult path

    physical_run = result.run_id
    for payload in payloads:
        # Every event correlates to the physical Dagster run id — the key the
        # observer groups the run timeline by.
        assert payload["correlation"].get("run_id") == physical_run, (
            f"{payload['event']} missing physical run correlation"
        )
        assert payload["schema_version"] == "2.0"

    by_name = {p["event"]: p for p in payloads}
    checks = [p for p in payloads if p["event"] == "quality.check"]

    # The in-band CheckResult produced a quality.check with its name/outcome.
    schema_check = next(p for p in checks if p["attributes"].get("check_name") == "schema_ok")
    assert schema_check["outcome"] == "success"
    assert schema_check["correlation"].get("asset_key") == "bronze.users"

    # The hook-emitted quality result translated with the logical run id
    # preserved as an attribute for retry grouping.
    hook_check = next(p for p in checks if p["attributes"].get("check_name") == "not_null")
    assert hook_check["attributes"].get("phlo_run_id") == physical_run

    # Entities land on canonical ids: the asset materialization names the
    # asset and the run; the ingestion load names asset + table + branch.
    materialize = by_name["asset.materialize"]
    assert materialize["entities"].get("asset") == "asset://bronze.users"
    assert materialize["entities"].get("run") == f"run://dagster/{physical_run}"
    load = by_name["ingestion.load"]
    assert load["entities"].get("table") == "table://bronze.users"
    assert load["entities"].get("branch") == "branch://nessie/pipeline-run-e2e"

    # The step boundary event carries measured timing and success outcome.
    step = by_name["pipeline.step"]
    assert step["outcome"] == "success"
    assert step.get("duration_ms") is not None


def test_dagster_check_failure_records_failure_outcome(captured: Any, bus: Any) -> None:
    """A failing dedicated asset check emits quality.check with failure."""
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    from phlo.capabilities import AssetCheckSpec, CheckResult

    def _check(_runtime: Any) -> Any:
        return CheckResult(
            check_name="freshness", passed=False, asset_key="bronze.users", severity="warn"
        )

    adapter = DagsterOrchestratorAdapter()
    check_def = adapter._build_check(
        AssetCheckSpec(
            name="freshness",
            asset_key="bronze.users",
            blocking=False,
            description="data is fresh",
            fn=_check,
        )
    )

    # Execute through a real Dagster job run so the check gets a real run id
    # and step context — the same path a deployed check job executes.
    defs = dagster.Definitions(
        asset_checks=[check_def],
        jobs=[
            dagster.define_asset_job(
                "check_job", selection=dagster.AssetSelection.checks(check_def)
            )
        ],
    )
    result = defs.resolve_job_def("check_job").execute_in_process()
    assert result.success

    payloads = captured.payloads()
    checks = [p for p in payloads if p["event"] == "quality.check"]
    assert len(checks) == 1
    check = checks[0]
    assert check["attributes"].get("check_name") == "freshness"
    assert check["outcome"] == "failure"
    assert check["severity"] == "warn"
    # The dedicated check ran inside dagster_run_scope, so run correlation
    # comes from the real execution context's run id — the same id the
    # job's pipeline.step boundary event carries.
    run_id = check["correlation"].get("run_id")
    assert run_id
    step = next(p for p in payloads if p["event"] == "pipeline.step")
    assert step["correlation"].get("run_id") == run_id
    assert check["correlation"].get("asset_key") == "bronze.users"


def test_pipeline_materialization_overhead_enabled_vs_disabled(captured: Any, bus: Any) -> None:
    """Whole-pipeline overhead: real materialize() runs with and without
    observability. Unlike the per-emit microbenchmarks, this exercises the
    actual integration cost — run/step scopes, hook translation, and check
    and materialization emissions on every Dagster execution.

    The bound is deliberately generous: it exists to catch structural mistakes
    (synchronous delivery, per-event reconfiguration, emission inside row
    loops), not to micro-regress on scheduler noise. Measured reality on this
    path is single-digit milliseconds per materialization.
    """
    asset_def = _build_ingesting_asset(bus)

    def _materialize_once() -> None:
        result = dagster.materialize([asset_def])
        assert result.success

    _materialize_once()  # warm-up: module imports, job graph construction
    captured.clear()

    n = 5
    start = time.perf_counter()
    for _ in range(n):
        _materialize_once()
    enabled_s = time.perf_counter() - start
    enabled_events = len(captured.payloads())
    assert enabled_events > 0

    # Disabled leg: the production "PHLO_OBSERVE_ENABLED=false" state.
    phlo_observe.reset_for_tests()
    assert phlo_observe.configure(enabled=False, drains=[], service_name="phlo-test")
    captured.clear()
    start = time.perf_counter()
    for _ in range(n):
        _materialize_once()
    disabled_s = time.perf_counter() - start
    assert len(captured.payloads()) == 0

    overhead_s = (enabled_s - disabled_s) / n
    # 500ms/materialization is ~50x the measured delta; it fails only if
    # observability introduces blocking or runaway emission into the step.
    assert overhead_s < 0.5, (
        f"observability added {overhead_s * 1000:.1f}ms/materialization "
        f"(enabled={enabled_s:.3f}s disabled={disabled_s:.3f}s over {n} runs, "
        f"{enabled_events} events)"
    )
