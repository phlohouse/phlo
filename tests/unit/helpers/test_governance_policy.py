"""Tests for fail-closed governance policy checks without a backend."""

from __future__ import annotations

from phlo.helpers.governance import policy_check


def test_policy_check_denies_when_no_governance_backend_is_available(monkeypatch) -> None:
    """A guarded operation cannot authorize without an enforcement backend."""
    monkeypatch.setattr("phlo.helpers.governance.resolve_capability", lambda _name: None)

    assert policy_check("alice", "gold.customer_health", "publish") is False
