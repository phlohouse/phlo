"""Synthetic CGT-04 scenario matching the supplied Mission Control designs.

All materializations write real Iceberg snapshots through Phlo's catalog provider.
No historical runs, timestamps, row counts or incidents are fabricated.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import dagster as dg
import pandas as pd
import pyarrow as pa
from phlo_iceberg.catalog import create_namespace, get_catalog

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return get_catalog(ref="main").load_table(name).scan().to_pandas()


def source(name):
    frame = pd.read_csv(ROOT / "data" / f"{name}.csv")
    flags = json.loads((ROOT / "data" / "scenario.json").read_text())
    if name == "bioreactor_telemetry" and flags["bioreactor_schema_drift"]:
        frame = frame.rename(columns={"do_pct": "do_sat_pct"})
        raise dg.Failure(
            "Schema contract freeze: unexpected column do_sat_pct; required column do_pct missing. "
            "No Iceberg write committed."
        )
    if name == "elisa_plate_reads" and flags["plate_schema_drift"]:
        frame["dilution_factor"] = 2.0
        raise dg.Failure(
            "Schema contract freeze: new dilution_factor column requires review. "
            "No Iceberg write committed."
        )
    return frame


def persist(relation, frame):
    catalog = get_catalog(ref="main")
    namespace = relation.split(".")[0]
    create_namespace(namespace, ref="main")
    arrow = pa.Table.from_pandas(frame, preserve_index=False)
    if catalog.table_exists(relation):
        table = catalog.load_table(relation)
        table.overwrite(arrow)
    else:
        table = catalog.create_table(relation, schema=arrow.schema)
        table.append(arrow)
    return dg.MaterializeResult(
        metadata={
            "dagster/row_count": len(frame),
            "rows_inserted": len(frame),
            "rows_deleted": 0,
            "snapshot_id": str(table.current_snapshot().snapshot_id),
            "phlo/relation": relation,
            "fixture": "synthetic CGT-04, actual Iceberg snapshot",
        }
    )


SPECS = [
    (
        "bronze.bioreactor_telemetry",
        [],
        "Platform fixture",
        "Process historian",
        lambda: source("bioreactor_telemetry"),
    ),
    ("bronze.lims_samples", [], "Platform fixture", "LIMS", lambda: source("lims_samples")),
    (
        "bronze.elisa_plate_reads",
        [],
        "Assay Dev",
        "Plate readers",
        lambda: source("elisa_plate_reads"),
    ),
    ("bronze.erp_batches", [], "Platform fixture", "ERP batches", lambda: source("erp_batches")),
    (
        "silver.bioreactor_runs_1min",
        ["bronze.bioreactor_telemetry"],
        "Platform fixture",
        "Process historian",
        lambda: (
            read("bronze.bioreactor_telemetry")
            .assign(minute=lambda x: pd.to_datetime(x.ts).dt.floor("min").astype(str))
            .groupby(["vessel_id", "run_id", "minute"], as_index=False)[
                ["ph", "do_pct", "temp_c", "agitation_rpm"]
            ]
            .mean()
        ),
    ),
    (
        "silver.feed_events",
        ["bronze.bioreactor_telemetry"],
        "Process Dev",
        "Process historian",
        lambda: read("bronze.bioreactor_telemetry").iloc[::30].assign(feed_ml=25.0),
    ),
    (
        "silver.run_summaries",
        ["silver.bioreactor_runs_1min"],
        "Process Dev",
        "Process historian",
        lambda: (
            read("silver.bioreactor_runs_1min")
            .groupby("run_id", as_index=False)
            .agg(
                ph_mean=("ph", "mean"),
                do_mean=("do_pct", "mean"),
                temperature_mean=("temp_c", "mean"),
            )
        ),
    ),
    (
        "silver.qc_results",
        ["bronze.lims_samples"],
        "QC Analytics",
        "LIMS",
        lambda: read("bronze.lims_samples").assign(
            in_spec=lambda x: x.result_value.between(x.spec_lower, x.spec_upper)
        ),
    ),
    (
        "silver.elisa_results",
        ["bronze.elisa_plate_reads"],
        "Assay Dev",
        "Plate readers",
        lambda: (
            read("bronze.elisa_plate_reads")
            .groupby("sample_id", as_index=False)
            .agg(od_mean=("od", "mean"), replicates=("od", "count"))
        ),
    ),
    (
        "silver.sample_lineage",
        ["bronze.lims_samples", "bronze.erp_batches"],
        "QC Analytics",
        "LIMS",
        lambda: read("bronze.lims_samples")[["sample_id", "batch_id"]].merge(
            read("bronze.erp_batches"), on="batch_id"
        ),
    ),
    (
        "gold.batch_release_metrics",
        ["silver.qc_results", "bronze.erp_batches"],
        "QC Analytics",
        "ERP batches",
        lambda: (
            read("silver.qc_results")
            .groupby("batch_id", as_index=False)
            .agg(
                potency_mean_pct=("result_value", "mean"),
                results_held=("in_spec", lambda s: int((~s).sum())),
            )
            .merge(read("bronze.erp_batches"), on="batch_id")
        ),
    ),
    (
        "gold.cpp_trends",
        ["silver.bioreactor_runs_1min"],
        "QC Analytics",
        "Process historian",
        lambda: (
            read("silver.bioreactor_runs_1min")
            .groupby("run_id", as_index=False)
            .agg(
                ph_min=("ph", "min"),
                ph_max=("ph", "max"),
                do_min=("do_pct", "min"),
                do_max=("do_pct", "max"),
            )
        ),
    ),
    (
        "gold.process_capability",
        ["silver.run_summaries"],
        "QC Analytics",
        "Process historian",
        lambda: read("silver.run_summaries").assign(cp_ph=lambda x: (8.0 - 6.0) / (6 * 0.2)),
    ),
    (
        "gold.qc_release_dashboard",
        ["gold.batch_release_metrics", "silver.sample_lineage"],
        "QC Analytics",
        "LIMS",
        lambda: (
            read("gold.batch_release_metrics")
            .groupby(["product", "release_status"], as_index=False)
            .agg(batches=("batch_id", "count"), results_held=("results_held", "sum"))
        ),
    ),
]


def make_asset(relation, deps, owner, origin, compute):
    layer, name = relation.split(".")

    @dg.asset(
        name=name,
        key_prefix=[layer],
        deps=[dg.AssetKey(d.split(".")) for d in deps],
        group_name=layer,
        tags={"asset_type": "ingestion" if not deps else "transformation"},
        compute_kind="python" if deps else "csv",
        description=f"Synthetic CGT-04 {name.replace('_', ' ')}. Real Iceberg table from {origin}.",
        metadata={
            "phlo/relation": relation,
            "phlo/layer": layer,
            "owner": owner,
            "source_name": origin,
            "schema_contract": "freeze",
            "sla": dg.MetadataValue.json({"freshness_hours": 1 if layer != "gold" else 1.5}),
        },
    )
    def materialize(context):
        return persist(relation, compute())

    return materialize


_assets = [make_asset(*s) for s in SPECS]


def make_check(relation, check_name, predicate):
    @dg.asset_check(
        asset=dg.AssetKey(relation.split(".")),
        name=check_name,
        description="Evaluates the actual synthetic Iceberg rows.",
    )
    def check():
        frame = read(relation)
        passed, details = predicate(frame)
        return dg.AssetCheckResult(passed=passed, metadata=details)

    return check


_checks = [
    make_check(
        "bronze.bioreactor_telemetry",
        "ph_in_range",
        lambda f: (
            bool(f.ph.between(6, 8).all()),
            {"lower": 6.0, "upper": 8.0, "rows_checked": len(f)},
        ),
    ),
    make_check(
        "bronze.bioreactor_telemetry",
        "unique_vessel_timestamp",
        lambda f: (not f.duplicated(["vessel_id", "ts"]).any(), {"rows_checked": len(f)}),
    ),
    make_check(
        "bronze.bioreactor_telemetry",
        "source_freshness",
        lambda f: (
            0 <= (datetime.now(timezone.utc) - pd.to_datetime(f.ts).max()).total_seconds() < 3600,
            {"source_max_ts": str(pd.to_datetime(f.ts).max()), "threshold_seconds": 3600},
        ),
    ),
    make_check(
        "silver.qc_results",
        "within_spec",
        lambda f: (
            bool(f.in_spec.all()),
            {"failing_rows": int((~f.in_spec).sum()), "rows_checked": len(f)},
        ),
    ),
    make_check(
        "silver.qc_results",
        "unique_sample_id",
        lambda f: (not f.sample_id.duplicated().any(), {"rows_checked": len(f)}),
    ),
    make_check(
        "silver.elisa_results",
        "four_replicates",
        lambda f: (bool((f.replicates == 4).all()), {"rows_checked": len(f)}),
    ),
]
_jobs = [
    dg.define_asset_job(
        "ingest_" + label, selection=dg.AssetSelection.assets(dg.AssetKey(relation.split(".")))
    )
    for label, relation in [
        ("bioreactor", "bronze.bioreactor_telemetry"),
        ("lims", "bronze.lims_samples"),
        ("plate_readers", "bronze.elisa_plate_reads"),
        ("erp", "bronze.erp_batches"),
    ]
]
_jobs += [
    dg.define_asset_job("transform_silver", selection=dg.AssetSelection.groups("silver")),
    dg.define_asset_job("transform_gold", selection=dg.AssetSelection.groups("gold")),
    dg.define_asset_job("audits_qc", selection=dg.AssetSelection.checks(*_checks)),
    dg.define_asset_job("mission_control_full", selection=dg.AssetSelection.assets(*_assets)),
]
defs = dg.Definitions(assets=_assets, asset_checks=_checks, jobs=_jobs)
