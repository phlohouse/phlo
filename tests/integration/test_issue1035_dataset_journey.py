"""Real ingestion, quality, Dataset publication and durable report acceptance."""

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import dagster as dg
import duckdb
import pytest
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import PythonFileTarget
from dagster_graphql.test.utils import execute_dagster_graphql
from pandera.pandas import DataFrameModel
from phlo_dagster.run_evidence import DagsterRunEvidenceSource
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets, phlo_ingestion
from phlo_dlt.registry import TableConfig
from phlo_iceberg.resource import IcebergResource
from phlo_pandera import RangeCheck
from phlo_pandera.decorator import clear_quality_checks, get_quality_checks, phlo_pandera
from phlo_postgres.dataset_state_store import SettingsDatasetStateStore
from phlo_postgres.settings_store import PostgresSettingsStore
from testcontainers.postgres import PostgresContainer

from phlo.dataset.models import TransitionRequest, candidate_dataset_id
from phlo.dataset.policy import DatasetPolicy, EvidenceCondition, PolicyRule
from phlo.dataset.service import DatasetService, StaticPolicySource
from phlo.dataset.store import state_store_namespace
from phlo.dataset_projection import DatasetAuthority, RunReportQualityEvidenceSource
from phlo.governance.surface import GovernanceSurface
from phlo.hooks import HookBus
from phlo.logging import bind_context, clear_context, get_logger
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo.run_evidence.reconciliation import RequiredEvidenceProfile, RunReconciler
from phlo.run_evidence.report import RunReportNotFound, build_run_report
from phlo.run_evidence.store import SQLiteRunEvidenceStore

pytestmark = pytest.mark.integration


class JourneySchema(DataFrameModel):
    """Ingestible rows; the domain quality constraint is separate."""

    id: int
    name: str


def test_real_ingestion_quality_core_publication_restart_and_report(
    tmp_path, iceberg_catalog, configured_minio_object_store, monkeypatch
):
    """Reject, recover and retain the distinct Dataset publication observation."""
    assert configured_minio_object_store is not None, "This acceptance test requires real MinIO"
    project = f"issue1035-{uuid4().hex}"
    table_name = f"journey_{uuid4().hex[:12]}"
    config = TableConfig(
        table_name=table_name,
        table_schema=None,
        validation_schema=JourneySchema,
        unique_key="id",
        group_name="integration_test",
    )
    table_id = config.full_table_name
    candidate = candidate_dataset_id(table_id)
    database = tmp_path / "run-evidence.sqlite"
    evidence_store = SQLiteRunEvidenceStore(database)
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(evidence_store), plugin_name="core")
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    monkeypatch.setattr("phlo.run_evidence.emit.get_hook_bus", lambda: bus)
    resource = IcebergResource()
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability", lambda _runtime: (resource, "iceberg")
    )
    policy = StaticPolicySource(
        DatasetPolicy(
            policy_version="issue1035-v1",
            rules=(
                PolicyRule(
                    control="actual_quality_passed",
                    evidence_kind="quality_checks",
                    condition=EvidenceCondition("passed", "true"),
                    applies_to=frozenset({"promote", "publish"}),
                ),
            ),
        )
    )
    values = [{"id": 2, "name": "bad-domain"}]
    source_available = False
    clear_ingestion_assets()
    clear_quality_checks()

    @phlo_ingestion(
        table_name=table_name,
        unique_key="id",
        group="integration_test",
        validation_schema=JourneySchema,
    )
    def source(partition_date: str):
        if not source_available:
            raise RuntimeError("Disposable source is unavailable")
        yield from values

    @phlo_pandera(
        table="observed_rows",
        checks=[RangeCheck(column="id", min_value=1, max_value=1)],
        backend="duckdb",
        full_table=True,
        asset_key=f"dlt_{table_name}",
    )
    def domain_quality():
        pass

    connection = duckdb.connect()
    try:
        with (
            PostgresContainer("postgres:18-alpine", password="test") as postgres,
            patch("phlo_iceberg.resource.get_catalog", return_value=iceberg_catalog),
            patch("phlo_iceberg.tables.get_catalog", return_value=iceberg_catalog),
            patch("phlo_iceberg.catalog.get_catalog", return_value=iceberg_catalog),
        ):
            monkeypatch.setenv(
                "PHLO_OBSERVATORY_SETTINGS_DB_URL", postgres.get_connection_url(driver=None)
            )
            namespace = state_store_namespace(str(tmp_path))
            quality_run_id = "failed-quality"

            def service():
                return DatasetService(
                    store=SettingsDatasetStateStore(PostgresSettingsStore(), namespace),
                    evidence_source=RunReportQualityEvidenceSource(
                        evidence_store,
                        project_id=project,
                        run_id=quality_run_id,
                        attempt=1,
                        checks_by_table={table_id: ((f"dlt_{table_name}", "range_check"),)},
                    ),
                    policy_source=policy,
                )

            def transition(resource_id, action, key):
                return DatasetAuthority(
                    service=service(), surface=GovernanceSurface(tables={})
                ).transition(
                    TransitionRequest(resource_id, action, key, actor="acceptance-operator")
                )

            def ingest_and_check(run_id):
                bind_context(project_id=project, run_id=run_id, attempt=1)
                runtime = SimpleNamespace(
                    run_id=run_id,
                    partition_key="2026-08-29",
                    tags={"phlo/project_id": project, "phlo/ref": "main"},
                    resources={"duckdb": connection},
                    logger=get_logger("issue1035"),
                )
                list(get_ingestion_assets()[0].run.fn(runtime))
                # The quality executor queries the actual Iceberg scan through
                # real DuckDB. This is not the bundled Trino publication path.
                connection.register(
                    "observed_rows", iceberg_catalog.load_table(table_id).scan().to_arrow()
                )
                return get_quality_checks()[0].fn(runtime)

            assert transition(candidate, "claim", "claim").status.value == "committed"
            assert transition(candidate, "review", "review").status.value == "committed"
            with pytest.raises(Exception, match="Disposable source is unavailable"):
                ingest_and_check("source-unavailable")
            unavailable = build_run_report(evidence_store, project, "source-unavailable", 1)
            assert any(stage.status == "failed" for stage in unavailable.stages)
            assert unavailable.outputs == ()
            assert not iceberg_catalog.table_exists(table_id)
            source_available = True
            assert not ingest_and_check("failed-quality").passed
            blocked = transition(candidate, "promote", "blocked")
            assert blocked.status.value == "blocked"
            assert service().record(candidate).current_state == "review"
            failed_report = build_run_report(evidence_store, project, "failed-quality", 1)
            assert failed_report.quality and not failed_report.quality[0].passed
            assert failed_report.outputs

            # Recover by replacing the real table, then run the same producers.
            iceberg_catalog.drop_table(table_id)
            values[:] = [{"id": 1, "name": "recovered"}]
            assert ingest_and_check("recovered").passed
            quality_run_id = "recovered"
            promoted = transition(candidate, "promote", "promote")
            assert promoted.status.value == "committed", (
                promoted,
                build_run_report(evidence_store, project, "recovered", 1).quality,
            )
            assert transition(table_id, "publish", "publish").status.value == "committed"
            assert service().record(table_id).publication_state == "published"
            assert transition(table_id, "publish", "publish").status.value == "replayed"
            assert [event.outcome for event in service().store.audit_events()] == [
                "committed",
                "committed",
                "blocked",
                "committed",
                "committed",
                "replayed",
            ]
            evidence_store.close()
            restarted = SQLiteRunEvidenceStore(database)
            try:
                report = build_run_report(restarted, project, "recovered", 1)
                assert report.quality[0].passed
                assert report.outputs[0].snapshot_after is not None
                publication = [
                    stage for stage in report.stages if stage.stage_type == "dataset_publication"
                ]
                assert len(publication) == 1
                assert publication[0].status == "success"
                assert publication[0].asset == table_id
                assert publication[0].resource_identity.resource_id == table_id
                # Dataset state is not an orchestrator's whole-run outcome.
                assert report.terminal_outcome is None
                # A new interpreter cannot see the parent's objects or patches.
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from phlo_postgres.dataset_state_store import SettingsDatasetStateStore; "
                        "from phlo_postgres.settings_store import PostgresSettingsStore; "
                        "from phlo.run_evidence.store import SQLiteRunEvidenceStore; "
                        "from phlo.run_evidence.report import build_run_report; "
                        "import sys; "
                        "state=SettingsDatasetStateStore(PostgresSettingsStore(),sys.argv[1]); "
                        "assert state.load(sys.argv[2]).publication_state=='published'; "
                        "store=SQLiteRunEvidenceStore(sys.argv[3]); "
                        "report=build_run_report(store,sys.argv[4],'recovered',1); "
                        "assert report.quality[0].passed; "
                        "assert report.outputs[0].snapshot_after is not None; "
                        "assert sum(s.stage_type=='dataset_publication' "
                        "for s in report.stages)==1; "
                        "store.close()",
                        namespace,
                        table_id,
                        str(database),
                        project,
                    ],
                    env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
                    check=True,
                    timeout=30,
                    capture_output=True,
                )
                print(
                    "ISSUE1035: real MinIO/Iceberg + dlt + Pandera/DuckDB; "
                    "PostgreSQL core state and SQLite report verified in new interpreter; "
                    "Dataset publication retained once; no invented terminal outcome; "
                    f"dataset={table_id}; snapshot={report.outputs[0].snapshot_after}; "
                    f"output_rows={report.outputs[0].record_count}; quality={report.quality[0].passed}"
                )
            finally:
                restarted.close()
    finally:
        clear_context()
        connection.close()
        evidence_store.close()
        clear_ingestion_assets()
        clear_quality_checks()
        if iceberg_catalog.table_exists(table_id):
            iceberg_catalog.drop_table(table_id)


def test_real_dagster_producers_publication_terminal_and_restart(
    tmp_path, iceberg_catalog, configured_minio_object_store, monkeypatch
):
    """Launch isolated job processes; reconcile only their actual durable terminal events."""
    assert configured_minio_object_store is not None, "This acceptance test requires real MinIO"
    project = f"issue1035-job-{uuid4().hex}"
    table_name = f"orchestrated_{uuid4().hex[:12]}"
    table_id = TableConfig(
        table_name=table_name,
        table_schema=None,
        validation_schema=JourneySchema,
        unique_key="id",
        group_name="integration_test",
    ).full_table_name
    candidate = candidate_dataset_id(table_id)
    namespace = state_store_namespace(str(tmp_path))
    database = tmp_path / "job-evidence.sqlite"
    store = SQLiteRunEvidenceStore(database)
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    configuration = tmp_path / "job.json"
    configuration.write_text(
        json.dumps(
            {
                "table_name": table_name,
                "table_id": table_id,
                "namespace": namespace,
                "evidence": str(database),
                "catalog": iceberg_catalog.properties,
            }
        )
    )
    profile = RequiredEvidenceProfile("issue1035-terminal", "1", provider="dagster")
    reports = {}
    try:
        with PostgresContainer("postgres:18-alpine", password="test") as postgres:
            monkeypatch.setenv(
                "PHLO_OBSERVATORY_SETTINGS_DB_URL", postgres.get_connection_url(driver=None)
            )
            quality_run_id = "not-yet-run"

            def service():
                return DatasetService(
                    store=SettingsDatasetStateStore(PostgresSettingsStore(), namespace),
                    evidence_source=RunReportQualityEvidenceSource(
                        store,
                        project_id=project,
                        run_id=quality_run_id,
                        attempt=1,
                        checks_by_table={table_id: ((f"dlt_{table_name}", "range_check"),)},
                    ),
                    policy_source=StaticPolicySource(
                        DatasetPolicy(
                            policy_version="issue1035-v1",
                            rules=(
                                PolicyRule(
                                    control="actual_quality_passed",
                                    evidence_kind="quality_checks",
                                    condition=EvidenceCondition("passed", "true"),
                                    applies_to=frozenset({"promote", "publish"}),
                                ),
                            ),
                        )
                    ),
                )

            def transition(resource_id, action, key):
                return DatasetAuthority(
                    service=service(), surface=GovernanceSurface(tables={})
                ).transition(
                    TransitionRequest(resource_id, action, key, actor="acceptance-operator")
                )

            for action in ("claim", "review"):
                assert transition(candidate, action, action).status.value == "committed"
            target = PythonFileTarget(
                python_file=str(Path(__file__).with_name("issue1035_dataset_job.py")),
                attribute="defs",
                working_directory=str(Path.cwd()),
                location_name="journey",
            )
            overrides = {
                "run_coordinator": {
                    "module": "dagster._core.run_coordinator.default_run_coordinator",
                    "class": "DefaultRunCoordinator",
                }
            }
            with (
                dg.instance_for_test(temp_dir=str(instance_dir), overrides=overrides) as instance,
                WorkspaceProcessContext(instance, target) as workspace,
            ):
                context = workspace.create_request_context()

                def launch(phase, expected_status):
                    result = execute_dagster_graphql(
                        context,
                        """mutation($executionParams: ExecutionParams!) {
                              launchPipelineExecution(executionParams: $executionParams) {
                                __typename
                                ... on LaunchRunSuccess { run { runId } }
                                ... on PythonError { message }
                                ... on RunConfigValidationInvalid { errors { message } }
                              }
                            }""",
                        {
                            "executionParams": {
                                "selector": {
                                    "pipelineName": "dataset_journey",
                                    "repositoryLocationName": "journey",
                                    "repositoryName": "__repository__",
                                },
                                "runConfigData": {
                                    "ops": {
                                        "dataset_producers": {
                                            "config": {
                                                "configuration": str(configuration),
                                                "phase": phase,
                                            }
                                        }
                                    }
                                },
                                "executionMetadata": {
                                    "tags": [
                                        {"key": "phlo/project_id", "value": project},
                                        {"key": "phlo/ref", "value": "main"},
                                        {"key": "phlo/attempt", "value": "1"},
                                    ]
                                },
                            }
                        },
                    )
                    assert not result.errors, result.errors
                    launched = result.data["launchPipelineExecution"]
                    assert launched["__typename"] == "LaunchRunSuccess", launched
                    run_id = launched["run"]["runId"]
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        run = instance.get_run_by_id(run_id)
                        if run.is_finished:
                            break
                        time.sleep(0.5)
                    assert run.status == expected_status, instance.all_logs(run_id)
                    terminal_event = (
                        dg.DagsterEventType.RUN_SUCCESS
                        if expected_status == dg.DagsterRunStatus.SUCCESS
                        else dg.DagsterEventType.RUN_FAILURE
                    )
                    assert (
                        sum(
                            entry.dagster_event is not None
                            and entry.dagster_event.event_type == terminal_event
                            for entry in instance.all_logs(run_id)
                        )
                        == 1
                    )
                    # Producer evidence alone cannot assert whole-run success.
                    before = build_run_report(store, project, run_id, 1)
                    assert before.terminal_outcome is None
                    source = DagsterRunEvidenceSource(instance, project_id=project)
                    reconciler = RunReconciler(store, source)
                    reconciler.reconcile(project, run_id, profile)
                    report = build_run_report(store, project, run_id, 1)
                    assert report.terminal_outcome.status == (
                        "success" if expected_status == dg.DagsterRunStatus.SUCCESS else "failed"
                    )
                    assert report.lifecycle.run.provider_run_id == run_id
                    assert (report.project_id, report.run_id, report.attempt) == (
                        project,
                        run_id,
                        1,
                    )
                    reconciler.reconcile(project, run_id, profile)
                    assert build_run_report(store, project, run_id, 1) == report
                    reports[phase] = report
                    return run_id

                launch("source-unavailable", dg.DagsterRunStatus.FAILURE)
                unavailable = reports["source-unavailable"]
                assert any(s.status == "failed" for s in unavailable.stages)
                assert unavailable.outputs == () and unavailable.quality == ()
                assert not iceberg_catalog.table_exists(table_id)
                quality_run_id = launch("failed-quality", dg.DagsterRunStatus.FAILURE)
                failed = reports["failed-quality"]
                assert failed.outputs and failed.quality and not failed.quality[0].passed
                assert failed.quality[0].blocking and failed.quality[0].failed_count == 1
                assert failed.outputs[0].record_count == 1
                assert transition(candidate, "promote", "blocked").status.value == "blocked"
                assert service().record(candidate).current_state == "review"
                assert not any(s.stage_type == "dataset_publication" for s in failed.stages)
                iceberg_catalog.drop_table(table_id)
                quality_run_id = launch("recovered", dg.DagsterRunStatus.SUCCESS)
                recovered = reports["recovered"]
                assert recovered.quality[0].passed
                assert recovered.quality[0].failed_count == 0
                assert recovered.outputs[0].record_count == 1
                assert recovered.outputs[0].snapshot_after is not None
                assert iceberg_catalog.load_table(table_id).scan().to_arrow().column(
                    "id"
                ).to_pylist() == [1]
                publication = [s for s in recovered.stages if s.stage_type == "dataset_publication"]
                assert len(publication) == 1 and publication[0].status == "success"
                assert publication[0].resource_identity.resource_id == table_id
                assert service().record(table_id).publication_state == "published"
                assert transition(table_id, "publish", "publish").status.value == "replayed"
                assert build_run_report(store, project, quality_run_id, 1) == recovered
                assert [e.outcome for e in service().store.audit_events()] == [
                    "committed",
                    "committed",
                    "blocked",
                    "committed",
                    "committed",
                    "replayed",
                ]
                for identity in (
                    (project + "-other", quality_run_id, 1),
                    (project, quality_run_id + "-other", 1),
                    (project, quality_run_id, 2),
                ):
                    with pytest.raises(RunReportNotFound):
                        build_run_report(store, *identity)
                store.close()
                # A fresh interpreter reopens both stores and compares the
                # complete report, including every durable evidence ID.
                restart = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import json,sys; from dataclasses import asdict; "
                        "from phlo.run_evidence.store import SQLiteRunEvidenceStore; "
                        "from phlo.run_evidence.report import build_run_report; "
                        "from phlo_postgres.dataset_state_store import SettingsDatasetStateStore; "
                        "from phlo_postgres.settings_store import PostgresSettingsStore; "
                        "state=SettingsDatasetStateStore(PostgresSettingsStore(),sys.argv[3]); "
                        "assert state.load(sys.argv[4]).publication_state=='published'; "
                        "store=SQLiteRunEvidenceStore(sys.argv[1]); "
                        "reports=json.load(sys.stdin); "
                        "assert all(json.loads(json.dumps(asdict(build_run_report("
                        "store,sys.argv[2],r['run_id'],1)))) "
                        "==r for r in reports); store.close()",
                        str(database),
                        project,
                        namespace,
                        table_id,
                    ],
                    input=json.dumps([asdict(r) for r in reports.values()]),
                    text=True,
                    env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
                    capture_output=True,
                    timeout=30,
                )
                assert restart.returncode == 0, restart.stderr
                print(
                    "ISSUE1035 DAGSTER",
                    json.dumps(
                        {
                            phase: {
                                "run_id": r.run_id,
                                "terminal": asdict(r.terminal_outcome),
                                "quality": [q.passed for q in r.quality],
                                "snapshots": [o.snapshot_after for o in r.outputs],
                            }
                            for phase, r in reports.items()
                        },
                        sort_keys=True,
                    ),
                )
    finally:
        store.close()
        if iceberg_catalog.table_exists(table_id):
            iceberg_catalog.drop_table(table_id)
