"""Fast deterministic contract tests for the federated-domains example.

These tests pin CURRENT framework behavior around multi-project dbt
discovery: all three domain projects are discovered, but exactly one is ever
activated, and the default choice is the first project in lexicographic path
order (not an intentional federation policy). See FEDERATION_FINDINGS.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.errors
import pytest
import yaml
from phlo_dbt.discovery import find_dbt_projects, get_dbt_project_dir
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import AGING_HORIZON, generate
from workflows.finance.ingest import read_invoices  # noqa: F401 - registers assets
from workflows.finance.quality import assert_amounts_positive, assert_known_deals_only
from workflows.finance.schemas import InvoiceSchema
from workflows.operations.ingest import read_incidents  # noqa: F401 - registers assets
from workflows.operations.quality import (
    assert_resolution_consistency,
    assert_severity_vocabulary,
)
from workflows.operations.schemas import IncidentSchema
from workflows.sales.ingest import read_deals  # noqa: F401 - registers assets
from workflows.sales.quality import assert_deal_ids_unique, assert_stage_in_pipeline
from workflows.sales.schemas import DealSchema

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
FINDINGS_PATH = EXAMPLE_ROOT / "FEDERATION_FINDINGS.md"
DOMAIN_DBT_DIRS = {
    "sales": EXAMPLE_ROOT / "workflows/sales/transforms/dbt",
    "finance": EXAMPLE_ROOT / "workflows/finance/transforms/dbt",
    "operations": EXAMPLE_ROOT / "workflows/operations/transforms/dbt",
}


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


def _read_failure_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _read_failure_json(path: Path) -> pd.DataFrame:
    return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))


def _aging_bucket(record: dict[str, object]) -> str:
    """Mirror the invoice_aging SQL bucketing against the fixed horizon."""
    if record["paid_on"]:
        return "paid"
    overdue = (AGING_HORIZON - date.fromisoformat(str(record["due_on"]))).days
    if overdue <= 0:
        return "current"
    if overdue <= 30:
        return "1-30"
    if overdue <= 60:
        return "31-60"
    return "60+"


# ---------------------------------------------------------------------------
# Fixture determinism and expected numbers


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_fixtures_are_byte_stable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)
    assert _tree_digest(first) == _tree_digest(second)


def test_expected_fixture_numbers(data_dir: Path) -> None:
    deals = pd.read_csv(data_dir / "sales" / "deals.csv")
    assert len(deals) == 12
    assert deals["stage"].value_counts().to_dict() == {
        "won": 3,
        "lost": 2,
        "qualification": 2,
        "proposal": 2,
        "negotiation": 2,
        "prospecting": 1,
    }
    assert float(deals["amount_usd"].sum()) == 142500.00

    invoices = json.loads((data_dir / "finance" / "invoices.json").read_text("utf-8"))
    assert len(invoices) == 8
    buckets: dict[str, int] = {}
    for record in invoices:
        buckets[_aging_bucket(record)] = buckets.get(_aging_bucket(record), 0) + 1
    assert buckets == {"paid": 3, "current": 1, "1-30": 1, "31-60": 2, "60+": 1}
    assert sum(float(r["amount_usd"]) for r in invoices) == 19400.0

    incidents = pd.read_csv(data_dir / "operations" / "incidents.csv")
    assert len(incidents) == 10
    assert incidents["resolved_at"].notna().sum() == 5
    assert incidents["resolution_minutes"].dropna().tolist() == [15.0, 41.0, 67.0, 93.0, 119.0]


# ---------------------------------------------------------------------------
# Domain contracts and labeled failures


def test_main_fixtures_pass_every_domain_gate(data_dir: Path) -> None:
    deals = pd.read_csv(data_dir / "sales" / "deals.csv")
    DealSchema.validate(deals)
    assert_stage_in_pipeline(deals)
    assert_deal_ids_unique(deals)

    invoices = read_invoices(data_dir / "finance")
    InvoiceSchema.validate(invoices)
    assert_amounts_positive(invoices)
    assert_known_deals_only(invoices, deals)

    incidents = pd.read_csv(data_dir / "operations" / "incidents.csv")
    IncidentSchema.validate(incidents)
    assert_severity_vocabulary(incidents)
    assert_resolution_consistency(incidents)


def test_invalid_stage_breaks_only_stage_vocabulary(data_dir: Path) -> None:
    invalid = _read_failure_csv(data_dir / "failures" / "deals_invalid_stage.csv")
    # Schema contract still holds: stage shape is unconstrained there.
    DealSchema.validate(invalid)
    assert_deal_ids_unique(invalid)  # the other gate stays green
    with pytest.raises(ValueError, match="archived"):
        assert_stage_in_pipeline(invalid)


def test_schema_contract_rejects_malformed_deal_id(data_dir: Path) -> None:
    malformed = _read_failure_csv(data_dir / "failures" / "deals_invalid_stage.csv")
    malformed.loc[0, "deal_id"] = "DEAL-1"
    with pytest.raises(pandera.errors.SchemaError):
        DealSchema.validate(malformed)


def test_unknown_deal_breaks_only_attribution(data_dir: Path) -> None:
    invalid = _read_failure_json(data_dir / "failures" / "invoices_unknown_deal.json")
    deals = pd.read_csv(data_dir / "sales" / "deals.csv")
    InvoiceSchema.validate(invalid)  # DL-9999 satisfies the id pattern
    assert_amounts_positive(invalid)  # the other gate stays green
    with pytest.raises(ValueError, match="DL-9999"):
        assert_known_deals_only(invalid, deals)


def test_negative_duration_breaks_only_resolution_consistency(data_dir: Path) -> None:
    invalid = _read_failure_csv(data_dir / "failures" / "incidents_negative_duration.csv")
    IncidentSchema.validate(invalid)  # contract allows nullable numerics
    assert_severity_vocabulary(invalid)  # the other gate stays green
    with pytest.raises(ValueError, match="negative"):
        assert_resolution_consistency(invalid)


def test_finance_partition_filter_reads_single_issue_day(data_dir: Path) -> None:
    day = read_invoices(data_dir / "finance", partition_date="2026-08-25")
    assert day["invoice_id"].tolist() == ["INV-2001"]
    with pytest.raises(FileNotFoundError, match="2031-01-01"):
        read_invoices(data_dir / "finance", partition_date="2031-01-01")


# ---------------------------------------------------------------------------
# Ingestion assets and schedules


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {
        "dlt_sales_deals",
        "dlt_finance_invoices",
        "dlt_operations_incidents",
    }

    sales = assets["dlt_sales_deals"]
    assert sales.metadata["write_mode"] == "merge"
    assert sales.metadata["owner"] == "revenue-ops"
    assert sales.run.max_retries == 2
    assert sales.run.freshness_hours == (168, 336)

    finance = assets["dlt_finance_invoices"]
    assert finance.metadata["write_mode"] == "append"
    assert finance.metadata["owner"] == "billing-ops"
    assert finance.run.max_retries == 3
    assert finance.run.freshness_hours == (48, 96)

    operations = assets["dlt_operations_incidents"]
    assert operations.metadata["write_mode"] == "merge"
    assert operations.metadata["owner"] == "sre"
    assert operations.run.max_retries == 3
    assert operations.run.freshness_hours == (24, 48)

    assert all(asset.checks[0].blocking for asset in assets.values())


def test_schedules_distinct_and_default_to_stopped() -> None:
    from workflows.finance import schedules as finance_schedules
    from workflows.operations import schedules as operations_schedules
    from workflows.sales import schedules as sales_schedules
    from workflows.schedules import federation

    registered = (
        sales_schedules.sales_domain_daily_schedule,
        finance_schedules.finance_domain_daily_schedule,
        operations_schedules.operations_domain_daily_schedule,
        federation.federated_domains_weekly_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "10 2 * * *",
        "25 2 * * *",
        "40 2 * * *",
        "0 3 * * 1",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )
    phlo_config = yaml.safe_load((EXAMPLE_ROOT / "phlo.yaml").read_text("utf-8"))
    assert phlo_config["wap"]["job_name"] == "federated_domains_wap_job"
    assert federation.federated_domains_wap_job.name == "federated_domains_wap_job"


# ---------------------------------------------------------------------------
# THE PROBE: multi-project dbt discovery boundary (current behavior)


def test_discovery_enumerates_all_three_domain_projects() -> None:
    projects = find_dbt_projects(EXAMPLE_ROOT)
    discovered = {p.relative_to(EXAMPLE_ROOT).as_posix() for p in projects}
    assert discovered == {p.relative_to(EXAMPLE_ROOT).as_posix() for p in DOMAIN_DBT_DIRS.values()}


def test_default_activation_picks_first_lexicographic_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CURRENT BEHAVIOR: default single-project activation lands on finance.

    All three candidates share the same depth, so any 'shallowest wins' rule
    degenerates to plain alphabetical order and picks finance. This pins the
    exact behavior the runtime exhibits today; it is recorded verbatim in
    FEDERATION_FINDINGS.md.
    """
    monkeypatch.delenv("DBT_PROJECT_DIR", raising=False)
    monkeypatch.chdir(EXAMPLE_ROOT)
    assert get_dbt_project_dir() == EXAMPLE_ROOT / "workflows/finance/transforms/dbt"


def test_explicit_activation_selects_exactly_one_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented escape hatch: one active project at a time via env var."""
    monkeypatch.chdir(EXAMPLE_ROOT)
    sales = DOMAIN_DBT_DIRS["sales"]
    monkeypatch.setenv("DBT_PROJECT_DIR", str(sales))
    assert get_dbt_project_dir() == sales


def test_probe_script_reports_the_boundary() -> None:
    env = dict(os.environ)
    env.pop("DBT_PROJECT_DIR", None)
    completed = subprocess.run(
        [sys.executable, "scripts/probe_federation.py"],
        cwd=EXAMPLE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    stdout = completed.stdout
    for relative in DOMAIN_DBT_DIRS.values():
        assert relative.relative_to(EXAMPLE_ROOT).as_posix() in stdout
    assert "ACTIVE" in stdout
    assert "single-active-project" in stdout
    assert "workflows/finance/transforms/dbt ACTIVE" in stdout


# ---------------------------------------------------------------------------
# Per-project structural validity (all three manifests stay valid artifacts)


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_each_project_is_valid_uniquely_named_and_selectable() -> None:
    project_names: set[str] = set()
    selector_names: set[str] = set()
    for domain, dbt_dir in DOMAIN_DBT_DIRS.items():
        config = _load_yaml(dbt_dir / "dbt_project.yml")
        assert isinstance(config, dict)
        name = config["name"]
        assert name not in project_names, f"duplicate dbt project name: {name}"
        project_names.add(name)
        assert config["profile"] == name
        assert config["model-paths"] == ["models"]

        profiles = _load_yaml(dbt_dir / "profiles" / "profiles.yml")
        assert isinstance(profiles, dict)
        profile = profiles[name]
        assert profile["target"] == "dev"
        output = profile["outputs"]["dev"]
        assert output["type"] == "trino"
        assert output["catalog"] == "iceberg"

        selectors = _load_yaml(dbt_dir / "selectors.yml")
        assert isinstance(selectors, dict)
        these = {selector["name"] for selector in selectors["selectors"]}
        assert these and these.isdisjoint(selector_names)
        selector_names |= these

        models_dir = dbt_dir / "models"
        sql_files = list(models_dir.glob("*.sql"))
        assert len(sql_files) == 1, f"{domain} expects exactly one model"


def test_sources_map_to_shared_asset_graph_via_phlo_asset_keys() -> None:
    expected_keys = {
        "sales": {"dlt_sales_deals"},
        "finance": {"dlt_finance_invoices", "dlt_sales_deals"},
        "operations": {"dlt_operations_incidents"},
    }
    for domain, dbt_dir in DOMAIN_DBT_DIRS.items():
        schema_docs = [
            doc
            for doc in _iter_model_yamls(dbt_dir)
            if isinstance(doc, dict) and doc.get("sources")
        ]
        keys = {
            table["meta"]["phlo_asset_key"]
            for doc in schema_docs
            for source in doc["sources"]
            for table in source["tables"]
        }
        assert keys == expected_keys[domain]


def _iter_model_yamls(dbt_dir: Path):
    for yaml_path in sorted((dbt_dir / "models").glob("*.yml")):
        doc = _load_yaml(yaml_path)
        if isinstance(doc, dict):
            yield doc


# ---------------------------------------------------------------------------
# Model evidence


def test_active_project_sales_carries_pipeline_evidence() -> None:
    sql = (DOMAIN_DBT_DIRS["sales"] / "models" / "deal_pipeline.sql").read_text("utf-8")
    assert "{{ config(materialized='table'" in sql
    assert "source('sales_raw', 'sales_deals')" in sql
    assert "count(*) as deal_count" in sql
    assert "sum(amount_usd) as pipeline_value_usd" in sql
    assert "group by stage" in sql


def test_finance_manifest_is_valid_and_attempts_cross_domain_join() -> None:
    sql = (DOMAIN_DBT_DIRS["finance"] / "models" / "invoice_aging.sql").read_text("utf-8")
    assert "source('finance_raw', 'finance_invoices')" in sql
    # The cross-domain attempt: finance joins the SALES raw table through a
    # locally-declared source because ref() cannot cross project manifests.
    assert "source('sales_raw', 'sales_deals')" in sql
    assert "inner join deals as d" in sql
    assert "date_diff('day', cast(i.due_on as date), date '2026-08-31')" in sql
    assert "FEDERATION_FINDINGS.md" in sql


def test_operations_manifest_is_valid_and_references_its_source() -> None:
    sql = (DOMAIN_DBT_DIRS["operations"] / "models" / "incident_summary.sql").read_text("utf-8")
    assert "source('operations_raw', 'operations_incidents')" in sql
    assert "sum(case when resolved_at is null then 1 else 0 end)" in sql
    assert "group by service, severity" in sql


def test_cross_project_refs_resolve_through_no_single_manifest() -> None:
    """Structural proof that the cross-domain join is unresolved today.

    Every source identifier across all manifests is an ingestion-owned raw
    table; no manifest can reference another project's MODEL node, and the
    model name spaces are disjoint, so activating one project can never
    compile or materialize another domain's models.
    """
    ingestion_tables = {"sales_deals", "finance_invoices", "operations_incidents"}
    model_names: dict[str, set[str]] = {}
    source_identifiers: set[str] = set()
    for domain, dbt_dir in DOMAIN_DBT_DIRS.items():
        model_names[domain] = {path.stem for path in (dbt_dir / "models").glob("*.sql")}
        for doc in _iter_model_yamls(dbt_dir):
            for source in doc.get("sources") or []:
                for table in source["tables"]:
                    source_identifiers.add(table["identifier"])
    assert source_identifiers == ingestion_tables

    all_models = [names for names in model_names.values()]
    for index, names in enumerate(all_models):
        for other in all_models[index + 1 :]:
            assert names.isdisjoint(other)
    assert set(model_names) == {"sales", "finance", "operations"}
    assert model_names["finance"] == {"invoice_aging"}


# ---------------------------------------------------------------------------
# Findings record


def test_findings_file_records_verified_boundary_and_product_work() -> None:
    assert FINDINGS_PATH.exists(), "FEDERATION_FINDINGS.md must stay committed"
    text = FINDINGS_PATH.read_text("utf-8")
    assert "workflows/finance/transforms/dbt" in text
    assert "workflows/operations/transforms/dbt" in text
    lowered = text.lower()
    for topic in ("multi-manifest", "namespaced", "lineage", "unresolved"):
        assert topic in lowered, f"missing product-work topic: {topic}"
    assert "WAP" in text
