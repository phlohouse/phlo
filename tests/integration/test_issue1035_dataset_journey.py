"""Real ingestion, quality, Dataset publication and durable report acceptance."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import duckdb
import pytest
from pandera.pandas import DataFrameModel
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
from phlo.run_evidence.report import build_run_report
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
