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

The module skips cleanly when its external services are unavailable.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

observe_core = pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")
pytest.importorskip("phlo_observe", reason="phlo-observe SDK not installed")
dagster = pytest.importorskip("dagster", reason="dagster not installed")

import phlo.telemetry as phlo_observe  # noqa: E402
from phlo.logging import get_logger  # noqa: E402


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
        get_logger("test.dagster.e2e").warning("slow_source_read", rows=5)
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

    physical_run = result.run_id
    payloads = [
        payload
        for payload in captured.payloads()
        if payload["correlation"].get("run_id") == physical_run
    ]
    names = [p.get("event") for p in payloads]

    # The real execution path produced every expected canonical event:
    # hook-translated ingestion lifecycle and quality result, the adapter's
    # in-band check emission, the materialization fact, and the step scope's
    # boundary event.
    assert "ingestion.extract" in names
    assert "ingestion.load" in names
    assert "application.log" in names
    assert "asset.materialize" in names
    assert "pipeline.step" in names
    assert names.count("quality.check") == 2  # hook translation + CheckResult path

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

    # The CheckResult passed — its severity is informational, never the
    # spec's configured *failure* severity (AssetCheckSeverity.ERROR).
    assert schema_check["severity"] == "info"

    # Entities land on canonical ids: the asset materialization names the
    # asset and the run; the ingestion load names asset + table + branch.
    materialize = by_name["asset.materialize"]
    assert materialize["entities"].get("asset") == "asset://bronze.users"
    assert materialize["entities"].get("run") == f"run://dagster/{physical_run}"
    load = by_name["ingestion.load"]
    assert load["entities"].get("table") == "table://bronze.users"
    assert load["entities"].get("branch") == "branch://nessie/pipeline-run-e2e"
    application_log = next(
        payload
        for payload in payloads
        if payload["event"] == "application.log"
        and payload["attributes"]["logger"] == "test.dagster.e2e"
    )
    assert application_log["attributes"]["message"] == "slow_source_read"

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
    # An explicit result severity wins: "warn", not the spec default "error".
    assert check["severity"] == "warn"
    # The dedicated check ran inside dagster_run_scope, so run correlation
    # comes from the real execution context's run id — the same id the
    # job's pipeline.step boundary event carries.
    run_id = check["correlation"].get("run_id")
    assert run_id
    step = next(p for p in payloads if p["event"] == "pipeline.step")
    assert step["correlation"].get("run_id") == run_id
    assert check["correlation"].get("asset_key") == "bronze.users"


def test_failed_materialize_result_records_failed_step(captured: Any, bus: Any) -> None:
    """A run fn reporting ``status="failed"`` must emit a failed
    ``pipeline.step`` — the Dagster failure is raised inside the step scope,
    so the step event records the failure instead of closing as a success
    before the error surfaces."""
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    from phlo.capabilities import AssetSpec, MaterializeResult, RunSpec

    def _run(_runtime: Any) -> list[Any]:
        return [MaterializeResult(status="failed", metadata={"reason": "boom"})]

    adapter = DagsterOrchestratorAdapter()
    asset_def = adapter._build_asset(
        AssetSpec(key="bronze.failing", group=None, description=None, run=RunSpec(fn=_run))
    )
    result = dagster.materialize([asset_def], raise_on_error=False)
    assert not result.success

    payloads = captured.payloads()
    steps = [p for p in payloads if p["event"] == "pipeline.step"]
    assert len(steps) == 1
    assert steps[0]["outcome"] == "failure"
    # A failed step records no materialization event — the canonical stream
    # must not claim the asset materialized.
    assert not [
        p for p in payloads if p["event"] == "asset.materialize" and p["outcome"] == "success"
    ]


# -- golden UX fixture -----------------------------------------------------------
#
# A complete WAP-launched run through the real production path: real
# dagster.materialize(), the production adapter, a real HookBus auto-discovering
# ObserveHookPlugin, and the same lifecycle emissions the WAP orchestration
# makes around the materialization. The captured canonical stream is the
# machine record; render_events() shows what a human sees. These goldens pin
# the UX contract: canonical JSONL stays exhaustive, the default view stays
# selective, verbose stays diagnostic, and failures stay readable.

WAP_STAGING_REF = "pipeline-run-e2e-demo"
WAP_ASSET = "bronze.users"
WAP_TABLE = "bronze.users"

_TS = __import__("re").compile(r"\d{2}:\d{2}:\d{2}\.\d{3}")
_DUR = __import__("re").compile(r"\d+(?:\.\d+)?(?:ms|s|m \d+s|h \d+m)\b")


def _normalize_volatile(text: str) -> str:
    """Wall-clock timestamps and measured durations vary per run — pin the
    shape, not the value."""
    return _DUR.sub("<dur>", _TS.sub("TT:TT:TT.ttt", text))


def _wap_lifecycle(bus: Any, physical: str | None, logical: str, *, fail: bool) -> None:
    """The WAP lifecycle emissions the orchestration makes around a run.

    Branch create -> ingest -> table commit -> quality gate -> promote or
    reject -> cleanup -> publish, plus the bookkeeping events the canonical
    stream keeps (evidence receipt, lineage, catalog-level ref operations).
    """
    from phlo.hooks.emitters import (
        IngestionEventContext,
        IngestionEventEmitter,
        QualityResultEventContext,
        QualityResultEventEmitter,
    )
    from phlo.hooks.events import (
        HookCorrelation,
        LineageEvent,
        PublishEvent,
        RunEvidenceObservationEvent,
    )

    # WAP launch: the staging ref is created on the run's catalog.
    phlo_observe.emit(
        "wap.branch.create",
        category="wap",
        outcome="success",
        attributes={
            "branch": WAP_STAGING_REF,
            "base_branch": "main",
            "strategy": "branch",
            "catalog_system": "nessie",
            "phlo_run_id": logical,
            "project_id": "demo",
        },
        correlation={"branch": WAP_STAGING_REF},
        entities={"branch": phlo_observe.branch_entity_id(WAP_STAGING_REF, system="nessie")},
    )
    # Catalog-level view of the same creation (what the nessie integration
    # emits around the real ref create).
    phlo_observe.emit(
        "nessie.branch.create",
        category="storage",
        outcome="success",
        producer="nessie",
        attributes={"branch": WAP_STAGING_REF, "base_branch": "main"},
        correlation={"branch": WAP_STAGING_REF},
    )

    # DLT extraction + load onto the staging ref.
    ingestion = IngestionEventEmitter(
        IngestionEventContext(
            asset_key=WAP_ASSET,
            table_name=WAP_TABLE,
            group_name="bronze",
            run_id=logical,
            branch_name=WAP_STAGING_REF,
            catalog_system="nessie",
        ),
        hook_bus=bus,
    )
    ingestion.emit_start()
    ingestion.emit_end(
        status="failed" if fail else "success",
        error="source read blew up" if fail else None,
        metrics={"rows_processed": 12481},
    )

    # The load lands as a commit on the staging ref's table (what the
    # iceberg integration emits around the real table operation).
    phlo_observe.emit(
        "iceberg.commit",
        category="storage",
        outcome="success",
        producer="iceberg",
        attributes={
            "catalog": "nessie",
            "namespace": "bronze",
            "operation": "append",
            "rows_added": 12481,
            "files_added": 3,
            "snapshot_id_after": 8675309123456789,
            "commit_hash": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c",
        },
        correlation={
            "table": WAP_TABLE,
            "branch": WAP_STAGING_REF,
            "snapshot_id": 8675309123456789,
        },
    )
    # The source pipeline's own completion record (dlt integration).
    phlo_observe.emit(
        "dlt.pipeline.run",
        category="data",
        outcome="success",
        producer="dlt",
        attributes={
            "pipeline_name": "users_ingest",
            "destination": "iceberg",
            "dataset_name": "bronze",
            "load_id": "1726657408.123456",
            "rows_loaded": 12481,
            "tables": [WAP_TABLE],
        },
        correlation={"pipeline": "users_ingest", "run_id": "1726657408.123456"},
    )

    # Column-level quality results on the staged data — the one that fails
    # a rejected run. QualityResultEvent carries no error field: the
    # failure shows through the glyph, check name, and asset, matching
    # what a real check translation emits.
    QualityResultEventEmitter(
        QualityResultEventContext(asset_key=WAP_ASSET, run_id=logical),
        hook_bus=bus,
    ).emit_result(check_name="not_null", passed=not fail)

    # The WAP audit verdict — the quality gate that decides promotion.
    QualityResultEventEmitter(
        QualityResultEventContext(asset_key=WAP_ASSET, run_id=logical),
        hook_bus=bus,
    ).emit_result(
        check_name="wap.aggregate",
        passed=not fail,
        metadata={
            "decision": "rejected" if fail else "passed",
            "dagster_run_id": physical,
        },
    )

    # Promotion evidence: staging merges into main — or is rejected.
    bus.emit(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            observation_type="publish",
            status="rejected" if fail else "success",
            error=("quality gate rejected: check 'not_null' failed" if fail else None),
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "dagster_run_id": physical,
                "wap_branch": WAP_STAGING_REF,
                "catalog_system": "nessie",
                "source_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
                "target_hash": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3",
                "merge_outcome": "rejected_quality" if fail else "promoted",
            },
            correlation=HookCorrelation(run_id=logical),
        )
    )
    # The merge commit landing on main (catalog level).
    phlo_observe.emit(
        "nessie.commit",
        category="storage",
        outcome="success",
        producer="nessie",
        attributes={"branch": "main", "merged_branch": WAP_STAGING_REF},
        correlation={"branch": "main"},
    )
    # Post-decision cleanup: the staging ref is dropped.
    bus.emit(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            observation_type="cleanup",
            status="success",
            catalog_change={
                "operation": "cleanup",
                "catalog_ref": WAP_STAGING_REF,
                "dagster_run_id": physical,
                "wap_branch": WAP_STAGING_REF,
                "catalog_system": "nessie",
            },
            correlation=HookCorrelation(run_id=logical),
        )
    )
    # Evidence receipt with no catalog mutation (a stage receipt).
    bus.emit(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            observation_type="receipt",
            status="success",
            stage_id="promote",
            resources=[{"kind": "catalog", "ref": "main"}],
            correlation=HookCorrelation(run_id=logical),
        )
    )
    # Lineage edges recorded for the materialized asset.
    bus.emit(
        LineageEvent(
            event_type="lineage",
            edges=[("raw.users", WAP_ASSET)],
            asset_keys=[WAP_ASSET],
            correlation=HookCorrelation(run_id=logical),
        )
    )
    # Downstream publish of the promoted table.
    bus.emit(
        PublishEvent(
            event_type="publish.end",
            asset_key=WAP_ASSET,
            target_system="observatory",
            tables={WAP_ASSET: f"main.{WAP_TABLE}"},
            status="success",
            correlation=HookCorrelation(run_id=logical),
        )
    )


def _build_wap_asset(bus: Any, *, fail: bool = False):
    """A real Dagster asset whose run fn emits the full WAP lifecycle."""
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    from phlo.capabilities import (
        AssetCheckSpec,
        AssetSpec,
        CheckResult,
        MaterializeResult,
        RunSpec,
    )

    def _run(runtime: Any) -> list[Any]:
        physical = phlo_observe.ambient_run_id()
        _wap_lifecycle(bus, physical, runtime.routing.run_id, fail=fail)
        return [
            MaterializeResult(metadata={"rows": 12481}),
            CheckResult(check_name="schema_ok", passed=True, asset_key=WAP_ASSET),
        ]

    adapter = DagsterOrchestratorAdapter()
    return adapter._build_asset(
        AssetSpec(
            key=WAP_ASSET,
            group=None,
            description=None,
            run=RunSpec(fn=_run),
            checks=[
                AssetCheckSpec(
                    name="schema_ok",
                    asset_key=WAP_ASSET,
                    fn=None,
                    blocking=False,
                )
            ],
        )
    )


def _terminal_run_event(result: Any, *, fail: bool) -> None:
    """What the observe_run_success/failure sensor emits in a deployed stack."""
    phlo_observe.emit(
        "pipeline.run",
        category="pipeline",
        outcome="failure" if fail else "success",
        severity="error" if fail else "info",
        attributes={
            "job_name": "__anonymous_asset_job__",
            "dagster_status": "FAILURE" if fail else "SUCCESS",
            "phlo_run_id": result.run_id,
        },
        correlation={
            "run_id": result.run_id,
            "root_run_id": result.run_id,
            "job_id": "__anonymous_asset_job__",
            "branch": WAP_STAGING_REF,
        },
        entities={
            "run": phlo_observe.run_entity_for("dagster", result.run_id),
            "branch": phlo_observe.branch_entity_id(WAP_STAGING_REF, system="nessie"),
        },
        producer="dagster",
    )


def test_wap_run_golden_ux(captured: Any, bus: Any) -> None:
    """The production-path run keeps its operational stream while the default
    human view stays selective."""
    from phlo_observe_plugin.presentation import render_events

    result = dagster.materialize([_build_wap_asset(bus)])
    assert result.success
    _terminal_run_event(result, fail=False)

    payloads = captured.payloads()
    operational_names = [
        payload.get("event") for payload in payloads if payload.get("event") != "application.log"
    ]
    assert operational_names == [
        "wap.branch.create",
        "nessie.branch.create",
        "ingestion.extract",
        "ingestion.load",
        "iceberg.commit",
        "dlt.pipeline.run",
        "quality.check",
        "wap.validate",
        "wap.promote",
        "nessie.commit",
        "wap.cleanup",
        "phlo.observation",
        "phlo.lineage",
        "phlo.publish",
        "quality.check",
        "pipeline.step",
        "asset.materialize",
        "pipeline.run",
    ]

    dlt_event = next(payload for payload in payloads if payload.get("event") == "dlt.pipeline.run")
    assert dlt_event["correlation"]["run_id"] != result.run_id
    assert dlt_event["correlation"]["root_run_id"] == result.run_id
    assert {
        payload["event"]: payload.get("correlation", {}).get("root_run_id")
        for payload in payloads
        if payload.get("event") != "application.log"
    } == dict.fromkeys(operational_names, result.run_id)

    # Default: thirteen operational events tell the story — the full WAP
    # lifecycle, the extract→load pair, the DLT run, both commits, checks,
    # the step, the materialization, the run's outcome. Field-dense events
    # stack their fields rather than running to a very long line.
    default_out = render_events(payloads, color="never", symbols="unicode")
    assert _normalize_volatile(default_out) == (
        f"── Run: {result.run_id[:16]}…\n"
        f"TT:TT:TT.ttt ✓ WAP branch  Branch: {WAP_STAGING_REF}  Strategy: branch  Catalog: nessie\n"
        f"TT:TT:TT.ttt ✓ Extract  Table: {WAP_TABLE}  Group: bronze\n"
        f"TT:TT:TT.ttt ✓ Load  {WAP_TABLE}\n"
        "    Rows   12,481\n"
        "    Group  bronze\n"
        f"TT:TT:TT.ttt ✓ Commit  {WAP_TABLE}\n"
        "    Op       append\n"
        "    Rows +   12,481\n"
        "    Files +  3\n"
        "TT:TT:TT.ttt ✓ Stage  users_ingest\n"
        "    Destination  iceberg\n"
        "    Dataset      bronze\n"
        "    Rows         12,481\n"
        f"TT:TT:TT.ttt ✓ Check  Check: not_null  Asset: {WAP_ASSET}\n"
        "TT:TT:TT.ttt ✓ WAP validate  Decision: passed  Check: wap.aggregate\n"
        f"TT:TT:TT.ttt ✓ WAP promote  {WAP_STAGING_REF}\n"
        "    Target   main\n"
        "    Merge    promoted\n"
        "    From     a1b2c3d4e5f6a1b2…\n"
        "    To       f6e5d4c3b2a1f6e5…\n"
        "    Catalog  nessie\n"
        f"TT:TT:TT.ttt ✓ WAP cleanup  Branch: {WAP_STAGING_REF}\n"
        f"TT:TT:TT.ttt ✓ Check  Check: schema_ok  Asset: {WAP_ASSET}\n"
        f"TT:TT:TT.ttt ✓ Step  Asset: {WAP_ASSET}  Op: bronze__users  <dur>\n"
        f"TT:TT:TT.ttt ✓ Materialize  Asset: {WAP_ASSET}  Rows: 12,481\n"
        "TT:TT:TT.ttt ✓ Run  Status: SUCCESS"
    )

    # Verbose: the plumbing-level events indent alongside, and the stacked
    # blocks pick up their secondary diagnostic fields.
    verbose_out = render_events(payloads, mode="verbose", color="never", symbols="unicode")
    for fragment in (
        "✓ Nessie branch",
        "✓ Nessie commit",
        "✓ Publish  Target: observatory",
        "✓ Lineage",
    ):
        assert fragment in verbose_out, fragment
    # Verbose-only fields join the stacked blocks.
    assert "    Snapshot" in verbose_out
    assert "    Trace" in verbose_out
    # Telemetry plumbing stays hidden even in verbose.
    assert "Observation" not in verbose_out


def test_wap_rejection_golden_ux(captured: Any, bus: Any) -> None:
    """A quality rejection reads plainly: which check failed, on which
    asset, the error, and the WAP/run outcome — no JSONL required."""
    from phlo_observe_plugin.presentation import render_events

    result = dagster.materialize([_build_wap_asset(bus, fail=True)])
    assert result.success
    _terminal_run_event(result, fail=True)

    payloads = captured.payloads()
    out = _normalize_volatile(render_events(payloads, color="never", symbols="unicode"))

    # The failed check names itself and its asset.
    assert f"TT:TT:TT.ttt ✕ Check  Check: not_null  Asset: {WAP_ASSET}" in out
    # The audit verdict and the rejection are primary, in order, and the
    # reject carries the reason through the error block under its
    # stacked fields.
    assert "✕ WAP validate  Decision: rejected  Check: wap.aggregate" in out
    assert f"✕ WAP reject  {WAP_STAGING_REF}" in out
    assert "    Merge  rejected_quality" in out
    assert "    quality gate rejected: check 'not_null' failed" in out
    # The run's terminal line carries the failed outcome.
    assert "✕ Run  Status: FAILURE" in out
    # The failed load's error surfaces under the stacked block.
    assert f"✕ Load  {WAP_TABLE}" in out
    assert "    source read blew up" in out


def test_pretty_mode_quiets_framework_console(
    captured: Any, bus: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pretty-primary runs drop redundant framework chatter from the terminal.

    Launch sites merge ``dagster_run_config`` into the run: with pretty on,
    the run's console logger falls to WARNING while the Dagster event log
    keeps every record — canonical telemetry is untouched.
    """
    # conftest sets PHLO_LOG_LEVEL=DEBUG for the suite — a debug config that
    # rightly preserves framework logs — so the quiet path clears it here.
    monkeypatch.delenv("PHLO_LOG_LEVEL", raising=False)
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    result = dagster.materialize(
        [_build_wap_asset(bus)],
        run_config=phlo_observe.dagster_run_config(),
    )
    assert result.success

    err = capsys.readouterr().err
    assert "- dagster - DEBUG -" not in err
    assert "- dagster - INFO -" not in err


def test_pretty_verbose_preserves_framework_console(
    captured: Any, bus: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verbose configuration keeps the full framework stream alongside
    the verbose pretty drain."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY_VERBOSE", "1")
    run_config = phlo_observe.dagster_run_config()
    assert "loggers" not in run_config  # nothing injected under verbose config

    result = dagster.materialize([_build_wap_asset(bus)], run_config=run_config)
    assert result.success

    err = capsys.readouterr().err
    assert "- dagster - DEBUG -" in err
    assert "RUN_START" in err
    assert "STEP_SUCCESS" in err


def test_framework_console_default_without_pretty(
    captured: Any, bus: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without pretty, merged run config is a no-op and Dagster logs as usual."""
    run_config = phlo_observe.dagster_run_config()
    assert "loggers" not in run_config

    result = dagster.materialize([_build_wap_asset(bus)], run_config=run_config)
    assert result.success
    assert "- dagster - DEBUG -" in capsys.readouterr().err


def test_debug_log_level_preserves_framework_console(
    captured: Any, bus: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PHLO_LOG_LEVEL=DEBUG is a debug configuration: full logs survive."""
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    monkeypatch.setenv("PHLO_LOG_LEVEL", "DEBUG")
    result = dagster.materialize(
        [_build_wap_asset(bus)],
        run_config=phlo_observe.dagster_run_config(),
    )
    assert result.success
    assert "- dagster - DEBUG -" in capsys.readouterr().err


def test_caller_explicit_console_level_survives_scope(
    captured: Any, bus: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller's explicit ``loggers.console.config.log_level`` is their own
    configuration — worker-side alignment must not stomp it, even with the
    pretty drain live: debug messages must keep appearing after scope entry."""
    monkeypatch.delenv("PHLO_LOG_LEVEL", raising=False)
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")
    result = dagster.materialize(
        [_build_wap_asset(bus)],
        run_config={"loggers": {"console": {"config": {"log_level": "DEBUG"}}}},
    )
    assert result.success
    err = capsys.readouterr().err
    # Post-scope events prove the explicit level survived scope entry —
    # STEP_SUCCESS/RUN_SUCCESS are emitted inside and after the step.
    assert "- dagster - DEBUG -" in err
    assert "STEP_SUCCESS" in err or "RUN_SUCCESS" in err


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
