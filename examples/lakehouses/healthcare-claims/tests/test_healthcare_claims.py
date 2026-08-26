"""Fast deterministic contract tests for the healthcare claims example."""

from __future__ import annotations

import csv
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import (
    build_claims,
    build_eligibility,
    build_providers,
    generate,
)
from workflows.claims import ingestion as claims_ingestion
from workflows.claims.quality import (
    assert_amount_reconciliation,
    assert_service_dates_covered,
    assert_versions_unique_and_advancing,
)
from workflows.eligibility import (
    ingestion as eligibility_ingestion,  # noqa: F401 - registers assets
)
from workflows.eligibility.quality import assert_no_overlapping_periods
from workflows.providers.ingestion import read_providers  # noqa: F401 - registers assets
from workflows.shared.contracts.privacy import assert_curated_privacy, forbidden_curated_columns
from workflows.shared.contracts.schemas import (
    ClaimSchema,
    EligibilityPeriodSchema,
    ProviderSchema,
)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


@pytest.fixture(scope="module")
def baseline(data_dir: Path) -> dict[str, pd.DataFrame]:
    claims = pd.DataFrame([row for rows in build_claims().values() for row in rows])
    for column in ("service_date",):
        claims[column] = pd.to_datetime(claims[column])
    eligibility = pd.DataFrame(build_eligibility())
    for column in ("effective_start", "effective_end"):
        eligibility[column] = pd.to_datetime(eligibility[column])
    providers = pd.DataFrame(build_providers())
    return {"claims": claims, "eligibility": eligibility, "providers": providers}


def _read_failure_csv(data_dir: Path, name: str) -> pd.DataFrame:
    path = data_dir / "failures" / name
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(
            csv.DictReader(handle, delimiter="|" if name.startswith("eligibility") else ",")
        )
    frame = pd.DataFrame(rows)
    numeric = ["billed_amount", "allowed_amount", "paid_amount", "version"]
    for column in numeric:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column])
    for column in ("service_date", "effective_start", "effective_end"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def _latest(claims: pd.DataFrame) -> pd.DataFrame:
    ranked = claims.sort_values(["claim_id", "version"])
    return ranked.groupby("claim_id", as_index=False).tail(1)


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary_one = generate(first)
    summary_two = generate(second)
    assert summary_one == summary_two
    for relative in sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file()):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_baseline_passes_strict_contracts_and_domain_checks(
    baseline: dict[str, pd.DataFrame],
) -> None:
    claims = baseline["claims"]
    eligibility = baseline["eligibility"]
    assert len(claims) == 44
    ClaimSchema.validate(claims)
    EligibilityPeriodSchema.validate(eligibility)
    ProviderSchema.validate(baseline["providers"])

    latest = _latest(claims)
    assert len(latest) == 40
    assert_versions_unique_and_advancing(claims)
    assert_amount_reconciliation(latest)
    assert_no_overlapping_periods(eligibility)
    assert_service_dates_covered(latest, eligibility)
    # Every re-filed claim carries a corrected (lower) billed amount.
    v2 = claims[claims.version == 2]
    originals = claims[(claims.version == 1) & claims.claim_id.isin(v2.claim_id)]
    merged = v2.merge(originals, on="claim_id", suffixes=("_v2", "_v1"))
    assert (merged.billed_amount_v2 < merged.billed_amount_v1).all()


def test_labeled_failures_break_their_invariants(
    data_dir: Path, baseline: dict[str, pd.DataFrame]
) -> None:
    breach = _read_failure_csv(data_dir, "claims_amount_breach.csv")
    with pytest.raises(ValueError, match="Paid amount exceeds allowed"):
        assert_amount_reconciliation(breach)

    duplicated = _read_failure_csv(data_dir, "claims_duplicate_version.csv")
    with pytest.raises(ValueError, match="Duplicate claim versions"):
        assert_versions_unique_and_advancing(duplicated)

    uncovered = _read_failure_csv(data_dir, "claims_outside_eligibility.csv")
    with pytest.raises(ValueError, match="outside any coverage period") as raised:
        assert_service_dates_covered(uncovered, baseline["eligibility"])
    assert "mbr..." in str(raised.value)  # diagnostics mask member identifiers

    overlap = _read_failure_csv(data_dir, "eligibility_overlap.csv")
    with pytest.raises(ValueError, match="Overlapping coverage periods"):
        assert_no_overlapping_periods(overlap)


def test_arrival_reader_reads_only_requested_partition(data_dir: Path) -> None:
    rows = claims_ingestion.read_arrival("2026-08-18", data_dir / "inbound" / "claims")
    assert len(rows) == CLAIMS_PER_DAY_08_18
    with pytest.raises(FileNotFoundError, match="2031-01-01"):
        claims_ingestion.read_arrival("2031-01-01", data_dir / "inbound" / "claims")


CLAIMS_PER_DAY_08_18 = 9  # 8 new claims plus one corrected re-file


def test_ingestion_assets_carry_regulated_contracts() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {"dlt_claims", "dlt_eligibility_periods", "dlt_providers"}
    claims_asset = assets["dlt_claims"]
    assert claims_asset.metadata["write_mode"] == "append"
    assert claims_asset.metadata["primary_key"] == ["claim_version_key"]
    assert claims_asset.metadata["owner"] == "claims-operations"
    assert claims_asset.run.max_retries == 1  # conservative retry budget
    assert claims_asset.run.freshness_hours == (26, 30)
    assert assets["dlt_eligibility_periods"].metadata["owner"] == "enrollment-operations"
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_schedules_order_daily_arrival_before_downstream() -> None:
    from workflows.schedules import healthcare

    registered = (
        healthcare.daily_arrival_schedule,
        healthcare.ordered_downstream_schedule,
        healthcare.monthly_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "10 2 * * *",
        "40 2 * * *",
        "0 4 1 * *",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )


def test_dbt_models_implement_normalization_temporal_join_and_privacy() -> None:
    root = Path(__file__).resolve().parents[1] / "workflows" / "transforms" / "dbt" / "models"

    latest = (root / "claims_latest.sql").read_text(encoding="utf-8")
    assert "partition by claim_id order by version desc" in latest

    codes = (root / "claim_codes.sql").read_text(encoding="utf-8")
    assert "unnest(split(procedure_codes, '|'))" in codes
    assert "upper(trim(code))" in codes

    valid = (root / "valid_claims.sql").read_text(encoding="utf-8")
    assert "c.service_date between e.effective_start and e.effective_end" in valid

    utilization = (root / "provider_utilization_monthly.sql").read_text(encoding="utf-8")
    cost = (root / "claim_cost_summary.sql").read_text(encoding="utf-8")
    # Curated marts aggregate away member identifiers entirely.
    for curated_sql in (utilization, cost):
        selected = curated_sql.lower()
        assert "member_id" not in selected.split("from")[0]
    assert forbidden_curated_columns(["provider_id", "service_month"]) == []
    with pytest.raises(ValueError, match="restricted identifiers"):
        assert_curated_privacy(["member_id", "claim_count"])


def test_provider_directory_is_merged_wholesale() -> None:
    providers = build_providers()
    assert len(providers) == 5
    assert all(len(provider["npi"]) == 10 for provider in providers)
    assert any(provider["network_status"] == "out_of_network" for provider in providers)
