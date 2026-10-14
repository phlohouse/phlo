"""Disposable Dagster job process for the Dataset acceptance journey."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dagster as dg
import duckdb
from pandera.pandas import DataFrameModel
from phlo_dlt.decorator import get_ingestion_assets, phlo_ingestion
from phlo_iceberg.resource import IcebergResource
from phlo_pandera import RangeCheck
from phlo_pandera.decorator import get_quality_checks, phlo_pandera
from phlo_postgres.dataset_state_store import SettingsDatasetStateStore
from phlo_postgres.settings_store import PostgresSettingsStore
from pyiceberg.catalog import load_catalog

from phlo.dataset.models import TransitionRequest, candidate_dataset_id
from phlo.dataset.policy import DatasetPolicy, EvidenceCondition, PolicyRule
from phlo.dataset.service import DatasetService, StaticPolicySource
from phlo.dataset_projection import DatasetAuthority, RunReportQualityEvidenceSource
from phlo.governance.surface import GovernanceSurface
from phlo.hooks import HookBus
from phlo.logging import bind_context, clear_context, get_logger
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo.run_evidence.store import SQLiteRunEvidenceStore


class JourneySchema(DataFrameModel):
    id: int
    name: str


@dg.op(config_schema={"configuration": str, "phase": str})
def dataset_producers(context):
    """Run real producers and publish only after their real quality result passes."""
    with Path(context.op_config["configuration"]).open() as stream:
        config = json.load(stream)
    phase = context.op_config["phase"]
    table_name, table_id = config["table_name"], config["table_id"]
    project = context.run.tags["phlo/project_id"]
    catalog = load_catalog("default", **config["catalog"])
    store = SQLiteRunEvidenceStore(config["evidence"])
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="core")
    connection = duckdb.connect()

    @phlo_ingestion(
        table_name=table_name,
        unique_key="id",
        group="integration_test",
        validation_schema=JourneySchema,
    )
    def source(partition_date: str):
        if phase == "source-unavailable":
            raise RuntimeError("Disposable source is unavailable")
        yield {"id": 1 if phase == "recovered" else 2, "name": phase}

    @phlo_pandera(
        table="observed_rows",
        checks=[RangeCheck(column="id", min_value=1, max_value=1)],
        backend="duckdb",
        full_table=True,
        asset_key=f"dlt_{table_name}",
    )
    def domain_quality():
        pass

    try:
        with (
            patch("phlo.hooks.emitters.get_hook_bus", return_value=bus),
            patch("phlo.run_evidence.emit.get_hook_bus", return_value=bus),
            patch("phlo_iceberg.resource.get_catalog", return_value=catalog),
            patch("phlo_iceberg.tables.get_catalog", return_value=catalog),
            patch("phlo_iceberg.catalog.get_catalog", return_value=catalog),
            patch(
                "phlo_dlt.decorator._resolve_table_store_capability",
                return_value=(IcebergResource(), "iceberg"),
            ),
        ):
            bind_context(project_id=project, run_id=context.run_id, attempt=1)
            runtime = SimpleNamespace(
                run_id=context.run_id,
                partition_key="2026-08-29",
                tags=context.run.tags,
                resources={"duckdb": connection},
                logger=get_logger("issue1035"),
            )
            list(get_ingestion_assets()[0].run.fn(runtime))
            connection.register("observed_rows", catalog.load_table(table_id).scan().to_arrow())
            result = get_quality_checks()[0].fn(runtime)
            if not result.passed:
                raise dg.Failure("Actual blocking Pandera domain quality failed")
            service = DatasetService(
                store=SettingsDatasetStateStore(PostgresSettingsStore(), config["namespace"]),
                evidence_source=RunReportQualityEvidenceSource(
                    store,
                    project_id=project,
                    run_id=context.run_id,
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
            authority = DatasetAuthority(service=service, surface=GovernanceSurface(tables={}))
            for resource_id, action in (
                (candidate_dataset_id(table_id), "promote"),
                (table_id, "publish"),
            ):
                transition = authority.transition(
                    TransitionRequest(resource_id, action, action, actor="acceptance-operator")
                )
                assert transition.status.value == "committed", transition
    finally:
        clear_context()
        connection.close()
        store.close()


@dg.job
def dataset_journey():
    dataset_producers()


defs = dg.Definitions(jobs=[dataset_journey])
