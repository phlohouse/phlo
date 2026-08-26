"""FX-domain quality checks: cross-rate tolerance reconciliation.

The cross-rate check is a reconciliation with an explicit warning tolerance:
``fx_cross_violations`` reports breaches for dashboards, while
``assert_fx_cross_tolerance`` raises only when callers need a blocking gate.
Strict numeric validation itself happens in the ingestion contract.
"""

from __future__ import annotations

import pandas as pd

CROSS_TOLERANCE_PCT = 0.001  # 10 basis points


def fx_cross_violations(
    rates: pd.DataFrame, tolerance_pct: float = CROSS_TOLERANCE_PCT
) -> list[dict[str, object]]:
    """Return rate dates whose EURGBP quote deviates from the implied cross."""
    pivoted = rates.pivot_table(index="rate_date", columns="pair", values="rate", aggfunc="last")
    violations: list[dict[str, object]] = []
    for rate_date, row in pivoted.iterrows():
        eur_usd = float(row["EURUSD"])
        gbp_usd = float(row["GBPUSD"])
        eur_gbp = float(row["EURGBP"])
        implied = eur_usd / gbp_usd
        deviation = abs(eur_gbp - implied) / implied
        if deviation > tolerance_pct:
            violations.append(
                {
                    "rate_date": str(rate_date)[:10],
                    "quoted_eur_gbp": eur_gbp,
                    "implied_eur_gbp": round(implied, 6),
                    "deviation_pct": round(deviation, 6),
                }
            )
    return sorted(violations, key=lambda item: str(item["rate_date"]))


def assert_fx_cross_tolerance(
    rates: pd.DataFrame,
    tolerance_pct: float = CROSS_TOLERANCE_PCT,
    blocking: bool = True,
) -> list[dict[str, object]]:
    """Raise on cross-rate breaches when used as a blocking gate.

    Returns the violation list either way so non-blocking callers (warning
    tolerances) can surface the same evidence without failing the run.
    """
    violations = fx_cross_violations(rates, tolerance_pct)
    if blocking and violations:
        raise ValueError(f"FX cross-rate tolerance breached: {violations[:3]}")
    return violations
