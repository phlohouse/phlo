"""Quality gates for the finance domain.

The known-deal gate reads the SALES domain's fixture extract directly: it is a
working cross-domain contract at the ingestion layer, which is exactly what
the dbt layer cannot express across project manifests (see
FEDERATION_FINDINGS.md).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SALES_EXTRACT = PROJECT_ROOT / "generated-data" / "sales" / "deals.csv"


def assert_amounts_positive(invoices: pd.DataFrame) -> None:
    """Invoice amounts must be strictly positive."""
    nonpositive = invoices[invoices["amount_usd"] <= 0]["invoice_id"].tolist()
    if nonpositive:
        raise ValueError(f"Invoices carry non-positive amounts: {nonpositive}")


def assert_known_deals_only(invoices: pd.DataFrame, deals: pd.DataFrame) -> None:
    """Every invoice must attribute to a deal present in the sales extract."""
    unknown = sorted(set(invoices["deal_id"]).difference(set(deals["deal_id"])))
    if unknown:
        raise ValueError(f"Invoice references unknown sales deal id(s): {unknown}")


def check_amounts_positive(frame: pd.DataFrame) -> str | None:
    """Blocking promotion gate for the ingestion asset."""
    try:
        assert_amounts_positive(frame)
    except ValueError as exc:
        return str(exc)
    return None


def make_known_deals_check(deals_path: Path = SALES_EXTRACT):
    """Build the blocking cross-domain attribution gate bound to one extract."""

    def _check(frame: pd.DataFrame) -> str | None:
        try:
            deals = pd.read_csv(deals_path)
        except FileNotFoundError:
            return f"Sales deal extract unavailable for attribution check: {deals_path}"
        try:
            assert_known_deals_only(frame, deals)
        except ValueError as exc:
            return str(exc)
        return None

    return _check
