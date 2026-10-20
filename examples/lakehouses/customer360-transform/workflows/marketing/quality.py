"""Marketing-domain quality checks over plain DataFrames.

Consent precedence is the load-bearing invariant: publication of any
consent-gated product depends on being able to say, for every identity, which
consent state is current. Two events sharing an exact ``occurred_at`` for one
email make that question unanswerable, so the check blocks ingestion.
"""

from __future__ import annotations

import pandas as pd

from workflows.commerce.quality import canonicalize_series

VALID_CONSENT_STATUSES = ("granted", "revoked")


def assert_consent_precedence_resolvable(events: pd.DataFrame) -> str | None:
    """Latest ``occurred_at`` must win per email - so no ties are allowed.

    Runs on staged consent batches. Emails are compared on their canonical
    form because case variants of one address are the same person.
    """
    normalized = events.assign(
        identity=events.email.astype(str).str.strip().str.lower(),
        occurred=pd.to_datetime(events.occurred_at, utc=True),
    )
    tied = normalized[normalized.duplicated(subset=["identity", "occurred"], keep=False)]
    if not tied.empty:
        offenders = sorted({f"{row.email}@{row.occurred.isoformat()}" for row in tied.itertuples()})
        return f"consent events tie on occurred_at; precedence unresolvable: {offenders[:5]}"
    return None


def assert_consent_status_domain(events: pd.DataFrame) -> str | None:
    """Consent status is a closed two-value domain."""
    invalid = events[~events.consent_status.isin(VALID_CONSENT_STATUSES)]
    if not invalid.empty:
        offenders = [f"{row.event_key}={row.consent_status}" for row in invalid.itertuples()][:5]
        return f"consent_status outside {{{', '.join(VALID_CONSENT_STATUSES)}}}: {offenders}"
    return None


def assert_contacts_reference_known_identities(
    contacts: pd.DataFrame, customers: pd.DataFrame
) -> str | None:
    """Every marketing contact must collapse onto a known commerce identity.

    This is the reconciliation seam between domains: contacts are captured by
    forms and imports under arbitrary address casing, so comparison runs on
    canonical emails rather than raw strings.
    """
    known = set(canonicalize_series(customers.email))
    unknown = contacts[~canonicalize_series(contacts.email).isin(known)]
    if not unknown.empty:
        offenders = [row.email for row in unknown.itertuples()][:5]
        return f"contacts map to no known customer identity: {offenders}"
    return None
