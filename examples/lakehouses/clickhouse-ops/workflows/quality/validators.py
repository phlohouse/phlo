"""Operational quality validators over plain DataFrames.

Every validator raises ``ValueError`` on violation so pytest can prove the
labeled failure fixtures break exactly one invariant, and so operators can
run the same screen against live ClickHouse tables. The two module-level
``check_*`` wrappers adapt validators to the ingestion decorator's
``quality_checks=[fn]`` contract (return None or a violation string).
"""

from __future__ import annotations

import pandas as pd

from workflows.schemas.contracts import ALLOWED_STATUS_CODES, TIER1_TENANTS


def assert_latency_within_bounds(events: pd.DataFrame) -> None:
    """Event latency_ms must stay inside the 0..60000 ms service bound."""
    violations = events[(events["latency_ms"] < 0) | (events["latency_ms"] > 60000)]
    if not violations.empty:
        offenders = violations[["event_id", "latency_ms"]].head(5).to_dict("records")
        raise ValueError(f"latency_ms out of bounds [0, 60000]: {offenders}")


def assert_status_codes_known(logs: pd.DataFrame) -> None:
    """Status codes must belong to the documented response catalog."""
    allowed = set(ALLOWED_STATUS_CODES)
    unknown = sorted(set(logs["status_code"]).difference(allowed))
    if unknown:
        raise ValueError(f"status_code values outside allowed catalog {sorted(allowed)}: {unknown}")


def check_event_types_known(frame: pd.DataFrame) -> str | None:
    """Blocking gate for platform events: event_type must be from the catalog."""
    known = {"api_request", "job_run", "deploy", "alert"}
    unknown = sorted(set(frame["event_type"]).difference(known))
    if unknown:
        return f"event_type values outside known catalog {sorted(known)}: {unknown}"
    return None


def check_paths_under_api(frame: pd.DataFrame) -> str | None:
    """Blocking gate for access logs: request paths stay under /api/."""
    off_api = frame[~frame["path"].str.startswith("/api/")]
    if not off_api.empty:
        paths = sorted(set(off_api["path"]))[:5]
        return f"request paths outside /api/ namespace: {paths}"
    return None


def _hour_floor(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(frame[column]).dt.floor("h")


def assert_tier1_tenant_freshness(
    events: pd.DataFrame,
    tier1_tenants: tuple[str, ...] = TIER1_TENANTS,
) -> None:
    """Every operating hour in the batch must carry tier-1 tenant traffic.

    Tier-1 tenants pay for per-hour freshness; an hour without events from
    any tier-1 tenant is a delivery gap even when tier-2 traffic keeps flows.
    """
    if events.empty:
        raise ValueError("Empty event batch cannot be screened for tier-1 freshness")
    hours = sorted(_hour_floor(events, "occurred_at").unique())
    missing: list[tuple[str, str]] = []
    for hour in hours:
        hour_frame = events[_hour_floor(events, "occurred_at") == hour]
        present = set(hour_frame["tenant_id"])
        for tenant_id in tier1_tenants:
            if tenant_id not in present:
                missing.append((str(pd.Timestamp(hour)), tenant_id))
    if missing:
        raise ValueError(f"tier-1 tenant freshness gap: {missing}")


def latest_versions(
    frame: pd.DataFrame,
    key: str,
    order_by: list[str],
) -> pd.DataFrame:
    """Read-time deduplication mirroring the dbt row_number() collapse.

    Keeps one row per ``key`` ordered by ``order_by`` (last wins), which is
    the pandas equivalent of ``row_number() over (partition by key order by
    ... desc) = 1`` in the serving marts.
    """
    ordered = frame.sort_values(order_by, kind="stable")
    return ordered.drop_duplicates(subset=key, keep="last")


def assert_hourly_matches_daily(
    hourly: pd.DataFrame,
    daily: pd.DataFrame,
    metrics: tuple[str, ...] = ("event_count", "request_count", "error_count"),
) -> None:
    """Hourly aggregate sums must equal daily totals per tenant.

    Mirrors the reconciliation between the append-only hourly marts and the
    replacing tenant_usage_daily aggregate; any drift between them means a
    refresh dropped or duplicated rows.
    """
    keys = ["tenant_id"]
    hourly_sum = hourly.groupby(keys, as_index=False)[list(metrics)].sum()
    merged = daily.merge(hourly_sum, on=keys, how="outer", suffixes=("_daily", "_hourly"))
    mismatches: list[str] = []
    for metric in metrics:
        left = merged[f"{metric}_daily"] if f"{metric}_daily" in merged else merged[metric]
        right = merged[f"{metric}_hourly"] if f"{metric}_hourly" in merged else merged[metric]
        bad = merged[left.fillna(-1) != right.fillna(-1)]
        for _, row in bad.iterrows():
            mismatches.append(f"{row['tenant_id']}/{metric}")
    if mismatches:
        raise ValueError(f"count reconciliation mismatch for {mismatches}")


def hourly_p95(values: pd.Series) -> int:
    """Nearest-rank p95 matching ClickHouse ``quantileExact(0.95)``.

    The fixture holds 21 samples per hour, so rank ceil(0.95*21)-1 = 19
    (zero-indexed) and the interpolated rank 0.95*(21-1) = 19 coincide: exact
    and interpolated quantile functions agree on this data.
    """
    ordered = sorted(int(v) for v in values)
    rank = -(-95 * len(ordered) // 100)  # ceil(0.95 * n), one-indexed
    return ordered[rank - 1]
