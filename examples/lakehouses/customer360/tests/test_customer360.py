"""Fast deterministic contract tests for the Customer 360 example."""

from __future__ import annotations

import json
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets
from phlo_sling import get_sling_assets

from scripts.generate_fixtures import build_update_set, generate, write_failure_fixtures
from workflows.commerce import (
    ingestion as commerce_ingestion,  # noqa: F401 - registers sling assets
)
from workflows.commerce.quality import (
    assert_order_totals_reconcile,
    assert_orders_reference_known_customers,
    canonicalize_series,
)
from workflows.marketing import (
    ingestion as marketing_ingestion,  # noqa: F401 - registers dlt assets
)
from workflows.marketing.quality import (
    assert_consent_precedence_resolvable,
    assert_contacts_reference_known_identities,
)
from workflows.schedules import customer360 as customer360_schedules
from workflows.support import ingestion as support_ingestion  # noqa: F401 - registers dlt assets
from workflows.support.quality import assert_resolved_after_created


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    counts = generate(data)
    assert counts == {
        "commerce_customers": 10,
        "commerce_orders": 30,
        "support_tickets": 14,
        "marketing_contacts": 7,
        "consent_events": 13,
    }
    build_update_set(data)
    write_failure_fixtures(data)
    return data


def _read_csv(data_dir: Path, relative: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / relative)


def _read_json(data_dir: Path, relative: str) -> dict:
    return json.loads((data_dir / relative).read_text(encoding="utf-8"))


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


# --- Determinism -------------------------------------------------------------


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    build_update_set(first)
    write_failure_fixtures(first)
    generate(second)
    build_update_set(second)
    write_failure_fixtures(second)
    assert _tree_hash(first) == _tree_hash(second)


# --- Identity resolution -----------------------------------------------------


def _all_domain_emails(data_dir: Path) -> tuple[pd.Series, int]:
    customers = _read_csv(data_dir, "commerce/base/customers.csv")
    orders = _read_csv(data_dir, "commerce/base/orders.csv")
    tickets = pd.DataFrame(_read_json(data_dir, "support/tickets.json")["tickets"])
    contacts = _read_csv(data_dir, "marketing/contacts.csv")
    observed = pd.concat(
        [customers.email, orders.email, tickets.email, contacts.email], ignore_index=True
    )
    distinct_observed = observed.nunique()
    return observed, distinct_observed


def test_case_variant_identities_converge(data_dir: Path) -> None:
    observed, distinct_observed = _all_domain_emails(data_dir)
    canonical = canonicalize_series(observed)

    # Variants exist and collapse: nine people are seen under more addresses.
    assert distinct_observed > canonical.nunique()
    assert canonical.nunique() == 9

    # Alice appears under three spellings across all three domains.
    alice_mask = canonical == "alice.anderson@example.com"
    assert set(observed[alice_mask]) == {
        "alice.anderson@example.com",
        "Alice.Anderson+legacy@example.com",
        "ALICE.ANDERSON+orders@example.com",
    }

    # Plus-suffixes never leak into canonical form.
    assert not canonical.str.contains(r"\+").any()


# --- Type-2 customer dimension (pure-python replication of the SQL) ----------


def _build_dimension(customers: pd.DataFrame) -> pd.DataFrame:
    """Replicate customer_dimension.sql window logic on plain DataFrames."""
    frame = customers.copy()
    frame["canonical_email"] = canonicalize_series(frame.email)
    frame["valid_from"] = pd.to_datetime(frame.updated_at, utc=True)
    frame = frame.sort_values(["canonical_email", "valid_from", "customer_id"], kind="stable")
    frame = frame.drop_duplicates(subset=["canonical_email", "valid_from"], keep="last")
    frame["next_valid_from"] = frame.groupby("canonical_email").valid_from.shift(-1)
    frame["current_flag"] = frame.next_valid_from.isna()
    frame["valid_to"] = frame.next_valid_from.fillna(pd.Timestamp("9999-12-31", tz="UTC"))
    return frame.reset_index(drop=True)


def _assert_type2_invariants(dimension: pd.DataFrame) -> None:
    for _, group in dimension.groupby("canonical_email"):
        windows = group.sort_values("valid_from")
        # Windows are adjacent half-open intervals, so they never overlap...
        adjacent = windows.valid_to.iloc[:-1].to_numpy() == windows.valid_from.iloc[1:].to_numpy()
        assert adjacent.all()
        # ...and exactly one row is current.
        assert int(windows.current_flag.sum()) == 1


def test_type2_dimension_base_state_is_nonoverlapping_with_one_current_row(
    data_dir: Path,
) -> None:
    customers = _read_csv(data_dir, "commerce/base/customers.csv")
    dimension = _build_dimension(customers)

    assert len(dimension) == 10  # one version per commerce customer record
    current = dimension[dimension.current_flag]
    assert len(current) == 9  # one current row per canonical identity

    alice = current[current.canonical_email == "alice.anderson@example.com"]
    assert alice.customer_id.tolist() == ["C0002"]  # later legacy account wins

    _assert_type2_invariants(dimension)


def test_type2_dimension_update_opens_new_versions_and_flips_current(
    data_dir: Path,
) -> None:
    base = _read_csv(data_dir, "commerce/base/customers.csv")
    update = _read_csv(data_dir, "commerce/update/customers.csv")
    evolved = pd.concat([base, update], ignore_index=True)
    dimension = _build_dimension(evolved)

    assert len(dimension) == 13  # 10 base versions + 2 segment changes + 1 signup
    current = dimension[dimension.current_flag]
    assert len(current) == 10  # ivy.ibex joins as a tenth identity

    alice = current[current.canonical_email == "alice.anderson@example.com"]
    assert alice.customer_id.tolist() == ["C0001"]
    assert alice.segment.tolist() == ["business"]

    _assert_type2_invariants(dimension)


# --- Consent precedence ------------------------------------------------------


def _consent_current(events: pd.DataFrame) -> pd.DataFrame:
    """Replicate consent_current.sql: latest occurred_at wins per identity."""
    frame = events.copy()
    frame["identity"] = frame.email.astype(str).str.strip().str.lower()
    frame["occurred"] = pd.to_datetime(frame.occurred_at, utc=True)
    frame = frame.sort_values(["identity", "occurred"], kind="stable")
    return frame.groupby("identity", as_index=False).tail(1).reset_index(drop=True)


def test_consent_precedence_latest_wins_per_identity(data_dir: Path) -> None:
    events = pd.DataFrame(_read_json(data_dir, "marketing/consent_events.json")["events"])
    current = _consent_current(events)

    assert len(current) == 8  # everyone with a consent record except zoe
    state = dict(zip(current.identity, current.consent_status, strict=True))
    assert state["alice.anderson@example.com"] == "revoked"  # revoked after grant
    assert state["bob.belsky+news@example.com"] == "granted"  # recovered after revocation
    assert state["dana.dov@example.com"] == "granted"  # flipped twice, latest wins
    assert state["hana.holt@example.com"] == "revoked"  # never granted
    assert "zoe.zephyr@example.com" not in state  # no record at all

    assert assert_consent_precedence_resolvable(events) is None


def test_tied_timestamp_fixture_fails_consent_precedence_check(data_dir: Path) -> None:
    tied = pd.DataFrame(_read_json(data_dir, "failures/consent_tied_timestamps.json")["events"])
    violation = assert_consent_precedence_resolvable(tied)
    assert violation is not None
    assert "occurred_at" in violation
    assert "priya.patel@example.com" in violation.lower()


def test_unknown_order_email_fails_reconciliation_check(data_dir: Path) -> None:
    customers = _read_csv(data_dir, "commerce/base/customers.csv")
    orders = _read_csv(data_dir, "commerce/base/orders.csv")

    assert assert_orders_reference_known_customers(orders, customers) is None

    orphan = pd.DataFrame(_read_json(data_dir, "failures/orders_unknown_email.json")["orders"])
    violation = assert_orders_reference_known_customers(
        pd.concat([orders, orphan], ignore_index=True), customers
    )
    assert violation is not None
    assert "O-BAD-001" in violation
    assert "stranger.nowhere@example.com" in violation


def test_backdated_resolution_fixture_fails_support_check(data_dir: Path) -> None:
    tickets = pd.DataFrame(_read_json(data_dir, "support/tickets.json")["tickets"])
    open_count = int(tickets.resolved_at.isna().sum())

    assert open_count == 3
    assert assert_resolved_after_created(tickets) is None

    backdated = pd.DataFrame(
        _read_json(data_dir, "failures/ticket_backdated_resolution.json")["tickets"]
    )
    violation = assert_resolved_after_created(pd.concat([tickets, backdated], ignore_index=True))
    assert violation is not None
    assert "TCK-9001" in violation


def test_contacts_reconcile_to_known_commerce_identities_and_orders_reconcile(
    data_dir: Path,
) -> None:
    customers = _read_csv(data_dir, "commerce/base/customers.csv")
    contacts = _read_csv(data_dir, "marketing/contacts.csv")
    orders = _read_csv(data_dir, "commerce/base/orders.csv")

    # Case and plus-suffix variants reconcile through canonicalization.
    assert assert_contacts_reference_known_identities(contacts, customers) is None

    # Replicated book reconciles to source by count and revenue.
    assert assert_order_totals_reconcile(orders, orders.copy()) is None
    drifted = orders.copy()
    drifted.loc[drifted.index[0], "total_amount"] = float(drifted.total_amount.iloc[0]) + 10.0
    violation = assert_order_totals_reconcile(orders, drifted)
    assert violation is not None
    assert "revenue mismatch" in violation


# --- Multi-root dbt project --------------------------------------------------


def _workflow_root() -> Path:
    return Path(__file__).resolve().parents[1] / "workflows"


def test_multi_root_dbt_project_lists_every_domain_root() -> None:
    project = (_workflow_root() / "transforms/dbt/dbt_project.yml").read_text(encoding="utf-8")
    for root in ("../../commerce/models", "../../support/models", "../../marketing/models"):
        assert root in project


def test_every_domain_root_holds_sql_models_and_source_mappings() -> None:
    root = _workflow_root()
    expected_sources = {
        "commerce/models/schema.yml": {"phlo_asset_key: sling_c360_customers"},
        "support/models/schema.yml": {"phlo_asset_key: dlt_support_tickets"},
        "marketing/models/schema.yml": {
            "phlo_asset_key: dlt_marketing_contacts",
            "phlo_asset_key: dlt_consent_events",
        },
    }
    for domain in ("commerce", "support", "marketing"):
        models = sorted((root / domain / "models").glob("*.sql"))
        assert models, f"{domain} root owns no SQL models"
    for relative, fragments in expected_sources.items():
        text = (root / relative).read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text


def test_dbt_models_implement_identity_dimension_consent_and_engagement() -> None:
    root = _workflow_root()

    resolution = (root / "commerce/models/identity_resolution.sql").read_text(encoding="utf-8")
    assert "ref('stg_commerce_customers')" in resolution
    assert "ref('stg_support_tickets')" in resolution
    assert "ref('stg_marketing_contacts')" in resolution
    assert resolution.count("union all") == 3
    assert "min(observed_email) <> min(canonical_email) as is_variant" in resolution

    dimension = (root / "commerce/models/customer_dimension.sql").read_text(encoding="utf-8")
    assert "lead(valid_from) over (" in dimension
    assert "partition by d.canonical_email" in dimension
    assert "timestamp '9999-12-31 00:00:00'" in dimension
    assert "when next_valid_from is null then true" in dimension

    consent = (root / "marketing/models/consent_current.sql").read_text(encoding="utf-8")
    assert "row_number() over (" in consent
    assert "order by occurred_at desc" in consent
    assert "where recency_rank = 1" in consent

    safe = (root / "marketing/models/consent_safe_product.sql").read_text(encoding="utf-8")
    assert "left join {{ ref('consent_current') }} cc" in safe
    assert "when cc.consent_status = 'granted' then true" in safe
    assert "'no consent record'" in safe
    assert "'consent revoked'" in safe

    engagement = (root / "support/models/support_engagement.sql").read_text(encoding="utf-8")
    assert "left join {{ ref('stg_support_tickets') }} t" in engagement
    assert "group by d.canonical_email" in engagement


# --- Asset contracts ---------------------------------------------------------


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    sling = {asset.key: asset for asset in get_sling_assets()}
    assert set(sling) == {"sling_c360_customers", "sling_c360_orders"}
    modes = {key: asset.metadata["mode"] for key, asset in sling.items()}
    assert modes == {
        "sling_c360_customers": "incremental",
        "sling_c360_orders": "incremental",
    }
    assert sling["sling_c360_customers"].metadata["primary_key"] == ["email"]
    assert sling["sling_c360_orders"].metadata["primary_key"] == ["order_id"]
    assert sling["sling_c360_customers"].metadata["owner"] == "commerce-crm"
    assert sling["sling_c360_customers"].run.freshness_hours == (24, 48)
    assert sling["sling_c360_orders"].run.freshness_hours == (6, 12)
    assert sling["sling_c360_orders"].run.max_retries == 5

    dlt = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(dlt) == {
        "dlt_support_tickets",
        "dlt_marketing_contacts",
        "dlt_consent_events",
    }
    write_modes = {key: asset.metadata["write_mode"] for key, asset in dlt.items()}
    assert set(write_modes.values()) == {"merge"}
    assert dlt["dlt_support_tickets"].metadata["primary_key"] == ["ticket_id"]
    assert dlt["dlt_marketing_contacts"].metadata["primary_key"] == ["email"]
    assert dlt["dlt_consent_events"].metadata["primary_key"] == ["event_key"]
    assert dlt["dlt_support_tickets"].metadata["owner"] == "support-ops"
    assert dlt["dlt_marketing_contacts"].metadata["owner"] == "growth-marketing"
    assert dlt["dlt_consent_events"].metadata["owner"] == "privacy-office"
    assert dlt["dlt_marketing_contacts"].run.freshness_hours == (168, 192)
    assert dlt["dlt_consent_events"].run.freshness_hours == (24, 48)
    # The consent gate blocks publication: its checks are wired as blocking.
    assert all(check.blocking for check in dlt["dlt_consent_events"].checks)


def test_streams_read_source_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMERCE_SOURCE_URL", "postgresql://override@elsewhere/db")
    assert commerce_ingestion.source_url() == "postgresql://override@elsewhere/db"
    monkeypatch.delenv("COMMERCE_SOURCE_URL")
    assert commerce_ingestion.source_url().startswith(
        "postgresql://commerce:commerce@localhost:10432"
    )


# --- Schedules ---------------------------------------------------------------


def test_schedules_have_distinct_cadences_and_default_to_stopped() -> None:
    schedules = (
        customer360_schedules.commerce_incremental_schedule,
        customer360_schedules.support_marketing_schedule,
        customer360_schedules.identity_rebuild_schedule,
        customer360_schedules.publication_schedule,
        customer360_schedules.weekly_reconciliation_schedule,
    )
    crons = {schedule.cron_schedule for schedule in schedules}
    assert crons == {
        "*/20 * * * *",
        "15 * * * *",
        "30 2 * * *",
        "45 2 * * *",
        "0 4 * * 6",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in schedules
    )
