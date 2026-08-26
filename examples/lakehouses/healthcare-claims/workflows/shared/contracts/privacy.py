"""Curated-output privacy rules shared across domains.

Published marts are aggregates: they must never carry direct member
identifiers. The rule is enforced in tests against model definitions and
live against catalog columns.
"""

from __future__ import annotations

FORBIDDEN_CURATED_COLUMNS = frozenset({"member_id", "member", "dob", "date_of_birth", "ssn"})


def forbidden_curated_columns(columns: list[str]) -> list[str]:
    """Return curated column names that violate the privacy rule."""
    normalized = {column.strip().lower() for column in columns}
    return sorted(FORBIDDEN_CURATED_COLUMNS & normalized)


def assert_curated_privacy(columns: list[str]) -> None:
    """Raise listing any forbidden identifier present in a curated output."""
    violations = forbidden_curated_columns(columns)
    if violations:
        raise ValueError(f"Curated outputs expose restricted identifiers: {violations}")
