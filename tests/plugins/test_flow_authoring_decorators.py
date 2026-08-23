"""Tests for terse flow authoring decorators.

Each decorator (transform/publish/observe/backfill/contract/access/schedule)
registers its asset, surface, or job metadata at decoration time without
executing the wrapped callable; dependencies compose strings with logical
relations and unsupported signatures fail clearly at run time.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from phlo.contracts import SLA, Consumer
from phlo.helpers.testing import FakeRuntimeContext

pytestmark = pytest.mark.core_regression


def test_transform_sql_registers_provider_neutral_asset() -> None:
    """SQL transforms should register asset specs with the returned SQL text."""
    import phlo

    transform = importlib.import_module("phlo.transform")
    transform.clear_transform_assets()

    @phlo.transform.sql(
        table="silver.orders",
        depends_on=["bronze.orders"],
        materialized="incremental",
        owner="data-platform",
        consumers=[Consumer(name="analytics")],
        sla=SLA(freshness_hours=4),
    )
    def orders_sql() -> str:
        return "select * from bronze.orders"

    assets = transform.get_transform_assets()

    assert orders_sql() == "select * from bronze.orders"
    assert len(assets) == 1
    assert assets[0].key == "transform_silver_orders"
    assert assets[0].deps == ["bronze.orders"]
    assert assets[0].kinds == {"sql", "transform"}
    assert assets[0].tags == {
        "asset_type": "transform",
        "provider": "core",
        "transform_type": "sql",
        "materialized": "incremental",
    }
    assert assets[0].metadata["table"] == "silver.orders"
    assert assets[0].metadata["sql"] == "select * from bronze.orders"
    assert assets[0].metadata["owner"] == "data-platform"
    assert assets[0].metadata["consumers"] == [
        {"name": "analytics", "contact": None, "usage": None}
    ]
    assert assets[0].metadata["sla"] == {
        "freshness_hours": 4,
        "quality_threshold": 1.0,
        "max_failures": None,
        "notify": None,
    }


def test_transform_sql_accepts_ref_dependencies() -> None:
    """SQL transform deps should use logical relation asset keys."""
    import phlo

    transform = importlib.import_module("phlo.transform")
    transform.clear_transform_assets()

    @phlo.transform.sql(
        table="gold.orders",
        depends_on=[phlo.ref("fct_orders", discover=False)],
    )
    def orders_sql() -> str:
        return "select * from {{ ref('fct_orders') }}"

    assets = transform.get_transform_assets()

    assert orders_sql() == "select * from {{ ref('fct_orders') }}"
    assert assets[0].deps == ["fct_orders"]


def test_publish_accepts_source_dependencies() -> None:
    """Publish deps should use dbt-style source relation asset keys."""
    import phlo

    phlo.clear_publish_assets()

    @phlo.publish(
        table="gold.orders",
        depends_on=[phlo.source("raw", "orders", discover=False)],
    )
    def orders_publish() -> str:
        return "gold.orders"

    assets = phlo.get_publish_assets()

    assert orders_publish() == "gold.orders"
    assert assets[0].deps == ["raw.orders"]


def test_observe_preserves_mixed_string_and_relation_dependencies() -> None:
    """Existing string dependencies should compose with logical references."""
    import phlo

    phlo.clear_observe_assets()

    @phlo.observe(
        table="gold.orders",
        depends_on=[
            "legacy.asset",
            phlo.ref("fct_orders", discover=False),
            phlo.source("raw", "orders", discover=False),
        ],
    )
    def orders_observe() -> None:
        return None

    assets = phlo.get_observe_assets()

    assert orders_observe() is None
    assert assets[0].deps == ["legacy.asset", "fct_orders", "raw.orders"]


def test_transform_sql_defers_context_aware_sql_rendering() -> None:
    """Context-aware SQL transforms should not execute during decoration."""
    import phlo

    transform = importlib.import_module("phlo.transform")
    transform.clear_transform_assets()

    @phlo.transform.sql(table="silver.orders_daily")
    def orders_sql(context: FakeRuntimeContext) -> str:
        return f"select * from bronze.orders where ds = '{context.partition_key}'"

    assets = transform.get_transform_assets()

    assert orders_sql(FakeRuntimeContext(partition_key="2026-05-18")) == (
        "select * from bronze.orders where ds = '2026-05-18'"
    )
    assert len(assets) == 1
    assert assets[0].metadata["sql"] is None
    assert assets[0].run is not None
    results = list(assets[0].run.fn(FakeRuntimeContext(partition_key="2026-05-18")))
    assert results[0].metadata["result"] == "select * from bronze.orders where ds = '2026-05-18'"


def test_transform_sql_does_not_call_required_keyword_only_functions() -> None:
    """Required keyword-only SQL parameters should not be treated as static SQL."""
    import phlo

    transform = importlib.import_module("phlo.transform")
    transform.clear_transform_assets()

    @phlo.transform.sql(table="silver.orders_daily")
    def orders_sql(*, ds: str) -> str:
        return f"select * from bronze.orders where ds = '{ds}'"

    assets = transform.get_transform_assets()

    assert orders_sql(ds="2026-05-18") == "select * from bronze.orders where ds = '2026-05-18'"
    assert len(assets) == 1
    assert assets[0].metadata["sql"] is None


def test_flow_run_rejects_unsupported_callable_signatures() -> None:
    """Runtime execution should fail clearly for ambiguous decorator callables."""
    from phlo._flow_authoring import build_run

    def needs_two_parameters(context: FakeRuntimeContext, extra: str) -> str:
        return f"{context.partition_key}:{extra}"

    def needs_keyword_only_parameter(*, ds: str) -> str:
        return ds

    for fn in [needs_two_parameters, needs_keyword_only_parameter]:
        run = build_run(fn)

        with pytest.raises(
            TypeError, match="must accept either no parameters or one context parameter"
        ):
            list(run.fn(FakeRuntimeContext(partition_key="2026-05-18")))


def test_publish_registers_dataset_surface() -> None:
    """Publish should mark curated tables as Dataset surfaces."""
    import phlo

    phlo.clear_publish_assets()

    @phlo.publish(
        table="gold.customer_health",
        audience=["cs", "sales"],
        owner="revops",
        freshness_hours=6,
        depends_on=["silver.customers"],
    )
    def customer_health() -> str:
        return "gold.customer_health"

    assets = phlo.get_publish_assets()

    assert customer_health() == "gold.customer_health"
    assert len(assets) == 1
    assert assets[0].key == "publish_gold_customer_health"
    assert assets[0].deps == ["silver.customers"]
    assert assets[0].kinds == {"publish", "dataset"}
    assert assets[0].tags["asset_type"] == "publish"
    assert assets[0].metadata["table"] == "gold.customer_health"
    assert assets[0].metadata["audience"] == ["cs", "sales"]
    assert assets[0].metadata["freshness_hours"] == 6
    assert assets[0].metadata["owner"] == "revops"


def test_observe_registers_operational_check_surface() -> None:
    """Observe should register operational health checks separately from quality rules."""
    import phlo

    phlo.clear_observe_assets()

    @phlo.observe(
        table="bronze.events",
        freshness_hours=2,
        row_count_change={"warn": 0.3, "fail": 0.6},
        depends_on=["bronze.events"],
    )
    def events_observability() -> None:
        return None

    assets = phlo.get_observe_assets()

    assert events_observability() is None
    assert len(assets) == 1
    assert assets[0].key == "observe_bronze_events"
    assert assets[0].deps == ["bronze.events"]
    assert assets[0].kinds == {"observe", "operational_check"}
    assert assets[0].tags["asset_type"] == "observe"
    assert assets[0].metadata["table"] == "bronze.events"
    assert assets[0].metadata["freshness_hours"] == 2
    assert assets[0].metadata["row_count_change"] == {"warn": 0.3, "fail": 0.6}
    assert [check.name for check in assets[0].checks] == [
        "freshness_hours",
        "row_count_change",
    ]


def test_backfill_registers_repeatable_backfill_job() -> None:
    """Backfill should capture partition window and write policy metadata."""
    import phlo

    phlo.clear_backfill_assets()

    @phlo.backfill(
        target="silver.orders",
        partitions={"start": "2026-01-01", "end": "2026-03-31"},
        mode="replace-partitions",
        depends_on=["bronze.orders"],
    )
    def orders_q1_backfill() -> str:
        return "orders_sql"

    assets = phlo.get_backfill_assets()

    assert orders_q1_backfill() == "orders_sql"
    assert len(assets) == 1
    assert assets[0].key == "backfill_silver_orders"
    assert assets[0].deps == ["bronze.orders"]
    assert assets[0].kinds == {"backfill"}
    assert assets[0].tags["asset_type"] == "backfill"
    assert assets[0].metadata["target"] == "silver.orders"
    assert assets[0].metadata["partitions"] == {
        "start": "2026-01-01",
        "end": "2026-03-31",
    }
    assert assets[0].metadata["mode"] == "replace-partitions"


def test_contract_registers_governance_contract() -> None:
    """Contract should declare ownership and lifecycle metadata once."""
    import phlo

    phlo.clear_contract_specs()

    @phlo.contract(
        table="gold.customer_health",
        owner="data-platform",
        consumers=["cs", Consumer(name="sales", contact="sales@example.com")],
        pii=True,
        freshness_hours=6,
        lifecycle="production",
    )
    def customer_health_contract() -> None:
        return None

    contracts = phlo.get_contract_specs()

    assert customer_health_contract() is None
    assert len(contracts) == 1
    assert contracts[0].key == "contract_gold_customer_health"
    assert contracts[0].table == "gold.customer_health"
    assert contracts[0].owner == "data-platform"
    assert contracts[0].pii is True
    assert contracts[0].lifecycle == "production"
    assert contracts[0].consumers == [
        {"name": "cs", "contact": None, "usage": None},
        {"name": "sales", "contact": "sales@example.com", "usage": None},
    ]
    assert contracts[0].sla == {
        "freshness_hours": 6,
        "quality_threshold": 1.0,
        "max_failures": None,
        "notify": None,
    }


def test_access_registers_access_policy() -> None:
    """Access should declare intended access policy for a table."""
    import phlo

    phlo.clear_access_policies()

    @phlo.access(
        table="gold.customer_health",
        roles=["cs_read", "sales_read"],
        pii_columns=["email"],
        policy="read",
    )
    def customer_health_access() -> None:
        return None

    policies = phlo.get_access_policies()

    assert customer_health_access() is None
    assert len(policies) == 1
    assert policies[0].key == "access_gold_customer_health"
    assert policies[0].table == "gold.customer_health"
    assert policies[0].roles == ["cs_read", "sales_read"]
    assert policies[0].pii_columns == ["email"]
    assert policies[0].policy == "read"


def test_schedule_registers_static_targets_and_dynamic_parameters() -> None:
    """Schedule targets should be static while the function returns run parameters."""
    import phlo

    phlo.clear_schedules()

    @phlo.schedule(
        name="daily_customer_health",
        cron="0 6 * * *",
        targets=["transform_silver_orders", "publish_gold_customer_health"],
        timezone="Europe/London",
    )
    def daily_customer_health() -> dict[str, str]:
        return {"partition_date": "2026-05-18"}

    schedules = phlo.get_schedules()

    assert daily_customer_health() == {"partition_date": "2026-05-18"}
    assert len(schedules) == 1
    assert schedules[0].key == "schedule_daily_customer_health"
    assert schedules[0].name == "daily_customer_health"
    assert schedules[0].cron == "0 6 * * *"
    assert schedules[0].targets == ["transform_silver_orders", "publish_gold_customer_health"]
    assert schedules[0].timezone == "Europe/London"
    assert schedules[0].fn() == {"partition_date": "2026-05-18"}


def test_top_level_exports_lazy_load_new_authoring_surfaces() -> None:
    """Top-level phlo exports should expose the new decorators lazily."""
    import phlo

    for name in [
        "phlo.transform",
        "phlo.publish",
        "phlo.observe",
        "phlo.backfill",
    ]:
        sys.modules.pop(name, None)

    assert callable(phlo.publish)
    assert callable(phlo.observe)
    assert callable(phlo.backfill)
    assert callable(phlo.contract)
    assert callable(phlo.access)
    assert callable(phlo.schedule)
    assert callable(phlo.transform.sql)
