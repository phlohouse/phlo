"""End-to-end telemetry test against the real phlo-observe SDK.

The SDK requires Python >=3.12 and is not installed in every dev
environment, so the module skips cleanly when it is absent. When present it
exercises the *real* emission chain — phlo hook events → ObserveHookPlugin
translation → phlo.telemetry shim → observe-core builder → capture backend —
and asserts the captured canonical payloads form the coherent, correlated
history a WAP-launched run should leave for the observer:

    dagster run
      ├─ wap.branch.create        (staging ref, owned by the real catalog)
      ├─ ingestion.extract/load   (asset + table + branch entities)
      ├─ wap.validate             (quality gate verdict)
      ├─ wap.promote              (critical delivery)
      └─ pipeline.run             (terminal, physical run id)
"""

from __future__ import annotations

import time
from typing import Any

import pytest

observe_core = pytest.importorskip("observe_core", reason="phlo-observe SDK not installed")
pytest.importorskip("phlo_observe", reason="phlo-observe SDK not installed")

import phlo.telemetry as phlo_observe  # noqa: E402
from phlo.hooks.events import (  # noqa: E402
    HookCorrelation,
    IngestionEvent,
    QualityResultEvent,
    RunEvidenceObservationEvent,
)


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


def test_wap_run_produces_coherent_correlated_history(captured: Any) -> None:
    from phlo_observe_plugin.hooks_plugin import ObserveHookPlugin

    plugin = ObserveHookPlugin()
    physical_run = "dagster-phys-1"
    logical_run = "logical-1"
    staging_ref = f"pipeline-run-{logical_run}"

    # The dagster asset scope binds the physical run id; every hook event
    # translated inside the run inherits it as correlation.run_id.
    with phlo_observe.bind_context(run_id=physical_run):
        # WAP launch — snapshot strategy, so the staging ref is owned by the
        # resolved catalog (polaris), not Nessie.
        phlo_observe.emit(
            "wap.branch.create",
            category="wap",
            outcome="success",
            attributes={
                "branch": staging_ref,
                "strategy": "snapshot",
                "phlo_run_id": logical_run,
                "project_id": "proj",
            },
            correlation={"branch": staging_ref},
            entities={"branch": phlo_observe.branch_entity_id(staging_ref, system="polaris")},
        )

        # DLT ingestion lifecycle on the staging ref.
        plugin._handle(
            IngestionEvent(
                event_type="ingestion.start",
                asset_key="raw.users",
                table_name="raw.users",
                group_name="raw",
                partition_key="2024-01-01",
                run_id=logical_run,
                branch_name=staging_ref,
                catalog_system="polaris",
                correlation=HookCorrelation(
                    run_id=logical_run, asset_key="raw.users", job_name="ingest_job"
                ),
            )
        )
        plugin._handle(
            IngestionEvent(
                event_type="ingestion.end",
                asset_key="raw.users",
                table_name="raw.users",
                group_name="raw",
                partition_key="2024-01-01",
                run_id=logical_run,
                branch_name=staging_ref,
                catalog_system="polaris",
                status="success",
                metrics={"rows_processed": 1000},
                correlation=HookCorrelation(run_id=logical_run, asset_key="raw.users"),
            )
        )

        # The WAP aggregate quality verdict.
        plugin._handle(
            QualityResultEvent(
                event_type="quality.result",
                asset_key="raw.users",
                check_name="wap.aggregate",
                passed=True,
                correlation=HookCorrelation(run_id=logical_run, check_name="wap.aggregate"),
            )
        )

        # Promotion evidence observation — as _emit_wap_observation builds it.
        plugin._handle(
            RunEvidenceObservationEvent(
                event_type="run_evidence.observation",
                observation_type="publish",
                status="success",
                catalog_change={
                    "operation": "promotion",
                    "catalog_ref": "main",
                    "dagster_run_id": physical_run,
                    "wap_branch": staging_ref,
                    "catalog_system": "polaris",
                    "source_hash": "abc123",
                    "target_hash": "def456",
                    "merge_outcome": "promoted",
                },
                correlation=HookCorrelation(run_id=logical_run),
            )
        )

        # Terminal run event — the observe_run_success sensor's emission.
        phlo_observe.emit(
            "pipeline.run",
            category="pipeline",
            outcome="success",
            attributes={
                "job_name": "ingest_job",
                "dagster_status": "SUCCESS",
                "phlo_run_id": logical_run,
            },
            correlation={"run_id": physical_run, "job_id": "ingest_job", "branch": staging_ref},
            entities={
                "run": phlo_observe.run_entity_for("dagster", physical_run),
                "branch": phlo_observe.branch_entity_id(staging_ref, system="polaris"),
            },
            producer="dagster",
        )

    payloads = captured.payloads()
    names = [p.get("event") for p in payloads]
    assert names == [
        "wap.branch.create",
        "ingestion.extract",
        "ingestion.load",
        "wap.validate",
        "wap.promote",
        "pipeline.run",
    ]

    # Physical run correlation is uniform across the history.
    for payload in payloads:
        assert payload["correlation"].get("run_id") == physical_run

    # The logical phlo run id survives as an attribute for retry grouping.
    for payload in payloads:
        assert payload["attributes"].get("phlo_run_id") == logical_run

    # Every staging-ref entity names the catalog that owns it — never Nessie.
    create, _extract, load, validate, promote, terminal = payloads
    assert create["entities"]["branch"] == f"branch://polaris/{staging_ref}"
    assert load["entities"]["branch"] == f"branch://polaris/{staging_ref}"
    assert load["entities"]["table"] == "table://raw.users"
    assert load["entities"]["asset"] == "asset://raw.users"
    assert promote["entities"]["branch"] == f"branch://polaris/{staging_ref}"
    assert promote["entities"]["run"] == f"run://dagster/{physical_run}"
    assert terminal["entities"]["run"] == f"run://dagster/{physical_run}"
    assert terminal["entities"]["branch"] == f"branch://polaris/{staging_ref}"

    # WAP promotion lands as a critical-delivery event; quality verdict is
    # translated to wap.validate with a passing outcome.
    assert promote["delivery"] == "critical"
    assert validate["outcome"] == "success"
    assert terminal["outcome"] == "success"
    assert terminal["source"]["producer"] == "dagster"


def test_failed_run_history_records_failure_outcomes(captured: Any) -> None:
    """A failed attempt must emit failure outcomes without poisoning the
    retry's later run row — physical ids differ per attempt."""
    from phlo_observe_plugin.hooks_plugin import ObserveHookPlugin

    plugin = ObserveHookPlugin()
    with phlo_observe.bind_context(run_id="dagster-phys-failed"):
        plugin._handle(
            IngestionEvent(
                event_type="ingestion.end",
                asset_key="raw.users",
                table_name="raw.users",
                group_name="raw",
                run_id="logical-2",
                status="failed",
                error="source read blew up",
                correlation=HookCorrelation(run_id="logical-2"),
            )
        )
        phlo_observe.emit(
            "pipeline.run",
            category="pipeline",
            outcome="failure",
            severity="error",
            attributes={"phlo_run_id": "logical-2", "dagster_status": "FAILURE"},
            correlation={"run_id": "dagster-phys-failed"},
            entities={"run": phlo_observe.run_entity_for("dagster", "dagster-phys-failed")},
            producer="dagster",
        )

    payloads = captured.payloads()
    assert [p["event"] for p in payloads] == ["ingestion.load", "pipeline.run"]
    assert payloads[0]["outcome"] == "failure"
    assert payloads[0]["severity"] == "error"
    assert payloads[0]["error"]["message"] == "source read blew up"
    assert payloads[1]["outcome"] == "failure"
    assert payloads[1]["entities"]["run"] == "run://dagster/dagster-phys-failed"


def test_emission_overhead_is_bounded(captured: Any) -> None:
    """Integration sanity check on emit cost: operation-level events must be
    cheap enough that instrumentation cannot materially slow a pipeline.

    Measures real shim→builder→capture latency; a healthy integration emits a
    handful of events per operation, so the per-event bound below is far
    looser than anything a real workload could notice.
    """
    n = 500
    start = time.perf_counter()
    for i in range(n):
        phlo_observe.emit(
            "ingestion.load",
            category="data",
            outcome="success",
            attributes={"iteration": i},
            correlation={"run_id": "phys-perf", "table": "raw.users"},
            entities={"table": "table://raw.users"},
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(captured.payloads()) == n
    # 5ms/event is already absurdly conservative — real emission is µs-scale
    # dict writes plus a queue push. This catches blocking I/O or per-event
    # reconfiguration mistakes, not micro-performance regressions.
    assert elapsed_ms / n < 5.0, f"emit averaged {elapsed_ms / n:.3f}ms/event"


def test_observability_overhead_enabled_vs_disabled(captured: Any) -> None:
    """With-vs-without comparison over a representative operation mix.

    Each iteration mimics one pipeline operation: a run-scoped block, one
    hook-event translation, one commit scope, and one terminal emit — the
    same emission shape a Dagster+DLT+Iceberg step produces. The enabled leg
    runs the full shim→builder→capture path; the disabled leg reconfigures
    the shim with ``enabled=False`` (the production "not configured" state).
    The delta is the instrumentation cost per operation.
    """
    from phlo_observe_plugin.hooks_plugin import ObserveHookPlugin

    plugin = ObserveHookPlugin()
    n = 200

    def _one_operation(run: str) -> None:
        with phlo_observe.bind_context(run_id=run):
            plugin._handle(
                IngestionEvent(
                    event_type="ingestion.end",
                    asset_key="raw.users",
                    table_name="raw.users",
                    group_name="raw",
                    run_id=run,
                    status="success",
                    metrics={"rows_processed": 1000},
                    correlation=HookCorrelation(run_id=run, asset_key="raw.users"),
                )
            )
            with phlo_observe.iceberg_commit(
                table="raw.users", branch="pipeline-run-x", operation="append"
            ):
                pass
            phlo_observe.emit(
                "pipeline.run",
                category="pipeline",
                outcome="success",
                correlation={"run_id": run},
            )

    start = time.perf_counter()
    for i in range(n):
        _one_operation(f"phys-enabled-{i}")
    enabled_ms = (time.perf_counter() - start) * 1000

    phlo_observe.reset_for_tests()
    assert phlo_observe.configure(enabled=False, drains=[], service_name="phlo-test")
    start = time.perf_counter()
    for i in range(n):
        _one_operation(f"phys-disabled-{i}")
    disabled_ms = (time.perf_counter() - start) * 1000

    overhead_ms = (enabled_ms - disabled_ms) / n
    # The budget is per *operation* (several events each) and is still ~1000x
    # looser than measured reality; it exists to catch blocking drains,
    # per-event reconfiguration, or accidental synchronous I/O — not to
    # micro-regress on dict-write timing.
    assert overhead_ms < 2.0, (
        f"observability added {overhead_ms:.3f}ms/operation "
        f"(enabled={enabled_ms:.1f}ms disabled={disabled_ms:.1f}ms over {n} ops)"
    )


def test_emission_does_not_block_when_observer_is_unreachable() -> None:
    """An observer outage must never stall pipeline work: the worker backend
    pushes to a bounded queue and retries on background threads, so emit is a
    dict-write plus queue push even when every delivery fails."""
    phlo_observe.reset_for_tests()
    try:
        assert phlo_observe.configure(
            enabled=True,
            runtime_backend="worker",
            drains=[
                {
                    "type": "http",
                    # Unroutable: nothing listens on port 1.
                    "endpoint": "http://127.0.0.1:1/v1/events",
                    "connect_timeout_s": 0.2,
                    "read_timeout_s": 0.2,
                    "max_attempts": 1,
                    "spool_on_failure": False,
                }
            ],
            spool_enabled=False,
            service_name="phlo-test",
        )
        n = 100
        start = time.perf_counter()
        for i in range(n):
            phlo_observe.emit(
                "ingestion.load",
                category="data",
                outcome="success",
                attributes={"i": i},
                correlation={"run_id": "phys-observer-down"},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        # If emit blocked on delivery, each event would cost >= the 200ms
        # connect timeout (>=20s total). 2s for 100 events still passes only
        # when delivery is genuinely asynchronous.
        assert elapsed_ms < 2000, f"emit blocked on dead observer: {elapsed_ms:.1f}ms/{n}"
    finally:
        phlo_observe.reset_for_tests()
