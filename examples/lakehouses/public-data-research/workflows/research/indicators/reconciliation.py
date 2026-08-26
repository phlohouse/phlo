"""Python mirror of the indicators staging and reconciliation arithmetic.

The dbt models own the SQL that runs on Trino; these functions re-derive the
same numbers over plain DataFrames so pytest can prove the fixture
arithmetic (unit conversion, monthly-to-annual rollup equality) without a
warehouse, and operators can screen live tables diagnostically.
"""

from __future__ import annotations

import pandas as pd


def to_celsius(temp_value: float, unit_f: bool) -> float:
    """Normalize one raw archive temperature to Celsius."""
    return (temp_value - 32.0) * 5.0 / 9.0 if unit_f else temp_value


def normalize_observations(observations: pd.DataFrame) -> pd.DataFrame:
    """Apply the staging model's unit conversion over a frame."""
    staged = observations.copy()
    staged["temp_c"] = [
        to_celsius(value, bool(flag))
        for value, flag in zip(staged["temp_c"], staged["unit_f"], strict=True)
    ]
    return staged


def _month_key(observations: pd.DataFrame) -> pd.Series:
    months = pd.to_datetime(observations["observed_at"], utc=True).dt.strftime("%Y-%m")
    return pd.to_datetime(months, format="%Y-%m")


def monthly_indicators(observations: pd.DataFrame) -> pd.DataFrame:
    """Per station-month averages and totals, mirroring ``monthly_indicators``."""
    staged = normalize_observations(observations)
    staged["obs_month"] = _month_key(staged)
    return staged.groupby(["station_id", "obs_month"], as_index=False).agg(
        observation_count=("station_id", "size"),
        avg_temp_c=("temp_c", "mean"),
        precip_mm_total=("precip_mm", "sum"),
    )


def rollup_reconciliation(observations: pd.DataFrame) -> pd.DataFrame:
    """Compare annual precipitation via months against the direct sum.

    The fixture arithmetic guarantees both paths aggregate the same rows, so
    ``precip_delta`` must be zero for every station-year.
    """
    via_months = (
        monthly_indicators(observations)
        .assign(
            census_year=lambda frame: pd.to_datetime(frame["obs_month"]).dt.year.map(
                lambda y: pd.Timestamp(f"{y}-01-01")
            )
        )
        .groupby(["station_id", "census_year"], as_index=False)
        .agg(precip_mm_via_months=("precip_mm_total", "sum"))
    )
    direct = observations.copy()
    direct["census_year"] = pd.to_datetime(direct["observed_at"]).dt.year.map(
        lambda y: pd.Timestamp(f"{y}-01-01")
    )
    direct = direct.groupby(["station_id", "census_year"], as_index=False).agg(
        precip_mm_direct=("precip_mm", "sum")
    )
    reconciled = via_months.merge(direct, on=["station_id", "census_year"])
    reconciled["precip_delta"] = reconciled["precip_mm_via_months"] - reconciled["precip_mm_direct"]
    return reconciled
