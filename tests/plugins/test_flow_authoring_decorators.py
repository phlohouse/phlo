"""Tests for terse flow authoring decorators.

Supported decorators register provider-neutral declarations. Dormant
backfill/schedule declarations and SQL without a provider fail immediately.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from phlo.contracts import SLA, Consumer
from phlo.helpers.testing import FakeRuntimeContext
from phlo.plugins.base import AssetProviderPlugin, PluginMetadata, TransformationProviderPlugin
from phlo.plugins.discovery.registry import PluginRegistry

pytestmark = [pytest.mark.core_regression, pytest.mark.filterwarnings("ignore::DeprecationWarning")]


@pytest.fixture
def sql_provider(monkeypatch: pytest.MonkeyPatch) -> PluginRegistry:
    """Use the provider contracts supplied by PR #961, without its open branch."""
    from phlo.transform import get_transform_assets

    class TransformProvider(TransformationProviderPlugin):
        @property
        def metadata(self):
            return PluginMetadata(name="transform", version="0.1.0", description="SQL")

        def get_asset_retriever(self):
            return get_transform_assets

    class TransformAssetProvider(AssetProviderPlugin):
        @property
        def metadata(self):
            return PluginMetadata(name="transform", version="0.1.0", description="SQL")

        def get_assets(self):
            return get_transform_assets()

    registry = PluginRegistry()
    providers = {
        "phlo.plugins.transformation_providers": TransformProvider,
        "phlo.plugins.assets": TransformAssetProvider,
    }
    monkeypatch.setattr(
        "phlo.plugins.discovery._plugin_loading.entry_points_for_group",
        lambda group: [
            SimpleNamespace(
                name="transform", value="test:TransformProvider", load=lambda: providers[group]
            )
        ],
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery._plugin_lifecycle.get_global_registry", lambda: registry
    )
    monkeypatch.setattr("phlo.plugins.discovery.get_global_registry", lambda: registry)
    return registry


@pytest.mark.usefixtures("sql_provider")
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


@pytest.mark.usefixtures("sql_provider")
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


@pytest.mark.usefixtures("sql_provider")
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


@pytest.mark.usefixtures("sql_provider")
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


def test_backfill_fails_without_registering_a_dormant_job() -> None:
    """The CLI is working; the similarly named decorator is not."""
    import phlo

    phlo.clear_backfill_assets()

    with pytest.raises(NotImplementedError, match=r"0\.19\.0.*phlo backfill CLI"):
        phlo.backfill(target="silver.orders", partitions={"start": "2026-01-01"})
    assert phlo.get_backfill_assets() == []


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


def test_schedule_fails_without_registering_a_dormant_schedule() -> None:
    """Point users to the provider cron or native scheduler instead."""
    import phlo

    phlo.clear_schedules()

    with pytest.raises(NotImplementedError, match=r"0\.19\.0.*native Dagster schedules"):
        phlo.schedule(name="daily", cron="0 6 * * *", targets=["orders"])
    assert phlo.get_schedules() == []


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


@pytest.mark.parametrize("missing_family", ["transformation_provider", "asset_provider"])
def test_sql_fails_without_both_provider_bridges(
    sql_provider: PluginRegistry, missing_family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transformation declaration provider alone cannot expose runnable assets."""
    from phlo.plugins.discovery import discover_plugins

    discover_plugins(plugin_type="transformation_provider")
    discover_plugins(plugin_type="asset_provider")
    monkeypatch.setattr("phlo.plugins.discovery.discover_plugins", lambda **kwargs: None)
    transform = importlib.import_module("phlo.transform")
    transform.clear_transform_assets()
    sql_provider.remove(missing_family, "transform")
    with pytest.raises(ModuleNotFoundError, match="Install phlo-transform.*dbt"):
        transform.sql(table="silver.orders")
    assert transform.get_transform_assets() == []


def test_sql_provider_bridge_exposes_executable_assets(sql_provider: PluginRegistry) -> None:
    """Keep working SQL providers, rather than retiring them based on an old audit."""
    import warnings

    from phlo.capabilities.registry import iter_provider_capabilities
    from phlo.transform import clear_transform_assets, sql

    clear_transform_assets()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)

        @sql(table="silver.orders")
        def orders() -> str:
            return "select 42"

    provider = sql_provider.get("asset_provider", "transform")
    families = dict(iter_provider_capabilities(provider))
    assets = families["asset"]
    assert len(assets) == 1
    assert assets[0].key == "transform_silver_orders"
    assert assets[0].metadata["sql"] == "select 42"
    assert list(assets[0].run.fn(FakeRuntimeContext()))[0].metadata["result"] == "select 42"


def test_governance_metadata_decorators_do_not_warn() -> None:
    """Supported governance decorators do not emit deprecation warnings."""
    import warnings

    import phlo

    phlo.clear_publish_assets()
    phlo.clear_observe_assets()
    phlo.clear_contract_specs()
    phlo.clear_access_policies()

    with warnings.catch_warnings():
        warnings.simplefilter("error")

        @phlo.publish(table="gold.orders")
        def _publish() -> str:
            return "gold.orders"

        @phlo.observe(table="bronze.events")
        def _observe() -> None:
            return None

        @phlo.contract(table="gold.orders", owner="data-platform")
        def _contract() -> None:
            return None

        @phlo.access(table="gold.orders", roles=["cs_read"])
        def _access() -> None:
            return None

    assert len(phlo.get_publish_assets()) == 1
    assert len(phlo.get_observe_assets()) == 1
    assert len(phlo.get_contract_specs()) == 1
    assert len(phlo.get_access_policies()) == 1
