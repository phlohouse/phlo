"""The domain's contracts and the dbt project agree with one another."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
import yaml

from source_to_report.domain import (
    CLAIM_TOLERANCE,
    PERIOD_END,
    PERIOD_START,
    POLLUTANTS,
    POLLUTANTS_BY_ID,
    STATIONS,
)
from workflows.schemas.monitoring import ClaimSchema, ReadingSchema

ROOT = Path(__file__).resolve().parents[1]


def _dbt_vars() -> dict:
    project = yaml.safe_load(
        (ROOT / "workflows" / "transforms" / "dbt" / "dbt_project.yml").read_text()
    )
    return project["vars"]


def test_dbt_period_and_counts_match_the_domain() -> None:
    variables = _dbt_vars()

    assert variables["period_start"] == PERIOD_START.isoformat()
    assert variables["period_end"] == PERIOD_END.isoformat()
    assert variables["station_count"] == len(STATIONS)
    assert variables["pollutant_count"] == len(POLLUTANTS)
    assert variables["expected_hours"] == (PERIOD_END - PERIOD_START).days * 24 + 24


def test_dbt_objectives_and_tolerance_match_the_domain() -> None:
    variables = _dbt_vars()
    pm25 = POLLUTANTS_BY_ID["PM2.5"]
    no2 = POLLUTANTS_BY_ID["NO2"]

    assert variables["pm25_daily_objective"] == pm25.short_term_objective
    assert variables["no2_daily_objective"] == no2.short_term_objective
    assert variables["claim_tolerance"] == CLAIM_TOLERANCE


def test_readings_contract_rejects_an_unknown_station(fixture_data) -> None:
    readings = json.loads((fixture_data / "raw" / "readings.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame(readings[:20])
    ReadingSchema.validate(frame)

    frame.loc[0, "station_id"] = "atlantis"
    with pytest.raises(pa.errors.SchemaError):
        ReadingSchema.validate(frame)


def test_claim_contract_rejects_the_wrong_unit(fixture_data) -> None:
    from source_to_report.domain.extraction import extract_claims
    from source_to_report.domain.sources import read_notes

    frame = pd.DataFrame(extract_claims(read_notes(fixture_data)))
    ClaimSchema.validate(frame)

    frame.loc[0, "unit"] = "mg/m³"
    with pytest.raises(pa.errors.SchemaError):
        ClaimSchema.validate(frame)
