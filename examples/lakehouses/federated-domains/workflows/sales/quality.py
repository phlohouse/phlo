"""Quality gates for the sales domain.

Validators run on plain DataFrames so pytest exercises them against generated
fixtures and operators can run them as diagnostics against live tables. The
``check_*`` wrappers adapt the raising validators into the non-raising
``str | None`` callables that ``phlo.ingest.dlt(quality_checks=[...])`` expects.
"""

from __future__ import annotations

import pandas as pd

DEAL_STAGES = ("prospecting", "qualification", "proposal", "negotiation", "won", "lost")


def assert_stage_in_pipeline(deals: pd.DataFrame) -> None:
    """Every deal stage must belong to the CRM pipeline vocabulary."""
    unknown = sorted(set(deals["stage"]).difference(DEAL_STAGES))
    if unknown:
        raise ValueError(f"Deals carry out-of-pipeline stage(s): {unknown}")


def assert_deal_ids_unique(deals: pd.DataFrame) -> None:
    """Deal ids must be unique inside one extract."""
    duplicated = deals["deal_id"][deals["deal_id"].duplicated()].tolist()
    if duplicated:
        raise ValueError(f"Duplicate deal ids in extract: {sorted(set(duplicated))}")


def check_stage_vocabulary(frame: pd.DataFrame) -> str | None:
    """Blocking promotion gate for the ingestion asset."""
    try:
        assert_stage_in_pipeline(frame)
    except ValueError as exc:
        return str(exc)
    return None


def check_deal_ids_unique(frame: pd.DataFrame) -> str | None:
    """Secondary gate: duplicate primary keys would corrupt the merge."""
    try:
        assert_deal_ids_unique(frame)
    except ValueError as exc:
        return str(exc)
    return None
