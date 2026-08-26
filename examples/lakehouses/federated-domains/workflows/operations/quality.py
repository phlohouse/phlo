"""Quality gates for the operations domain.

Validators run on plain DataFrames so pytest exercises them against generated
fixtures and operators can run them as diagnostics against live tables. The
``check_*`` wrappers adapt the raising validators into the non-raising
``str | None`` callables that ``phlo.ingest.dlt(quality_checks=[...])`` expects.
"""

from __future__ import annotations

import pandas as pd

SEVERITIES = ("sev1", "sev2", "sev3", "sev4")


def assert_severity_vocabulary(incidents: pd.DataFrame) -> None:
    """Every incident severity must belong to the standard scale."""
    unknown = sorted(set(incidents["severity"]).difference(SEVERITIES))
    if unknown:
        raise ValueError(f"Incidents carry out-of-scale severity value(s): {unknown}")


def assert_resolution_consistency(incidents: pd.DataFrame) -> None:
    """Resolved incidents need a non-negative duration; open ones need none."""
    resolved_at = pd.to_datetime(incidents["resolved_at"], errors="coerce")
    minutes = pd.to_numeric(incidents["resolution_minutes"], errors="coerce")
    resolved = resolved_at.notna()

    missing_duration = incidents.loc[resolved & minutes.isna(), "incident_id"].tolist()
    if missing_duration:
        raise ValueError(f"Resolved incidents without resolution duration: {missing_duration}")

    negative = incidents.loc[resolved & (minutes < 0), "incident_id"].tolist()
    if negative:
        raise ValueError(f"Resolved incidents with negative resolution duration: {negative}")

    phantom = incidents.loc[~resolved & minutes.notna(), "incident_id"].tolist()
    if phantom:
        raise ValueError(f"Open incidents carry a resolution duration: {phantom}")


def check_severity_vocabulary(frame: pd.DataFrame) -> str | None:
    """Blocking promotion gate for the ingestion asset."""
    try:
        assert_severity_vocabulary(frame)
    except ValueError as exc:
        return str(exc)
    return None


def check_resolution_consistency(frame: pd.DataFrame) -> str | None:
    """Blocking promotion gate for the ingestion asset."""
    try:
        assert_resolution_consistency(frame)
    except ValueError as exc:
        return str(exc)
    return None
