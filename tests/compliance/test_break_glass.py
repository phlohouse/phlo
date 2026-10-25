"""Tests for break-glass denial and revocation attribution.

Verifies that denying or revoking a break-glass request records who
performed the transition, the reason and the timestamp so the full
lifecycle is reconstructable for audit.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from phlo.compliance.governance.break_glass import (
    BreakGlassManager,
    BreakGlassRequest,
    BreakGlassStatus,
)


def _create_request(manager: BreakGlassManager) -> BreakGlassRequest:
    return manager.create_request(
        principal_subject="alice@example.com",
        principal_type="user",
        resource_type="dataset",
        resource_id="dataset-emergency",
        action="dataset.write",
        justification="Emergency access needed for incident",
    )


def _create_approved_request(manager: BreakGlassManager) -> BreakGlassRequest:
    request = _create_request(manager)
    manager.approve(request.request_id, approved_by="supervisor@example.com")
    return manager.get_request(request.request_id)


class TestBreakGlassDeny:
    """Tests for BreakGlassManager.deny()."""

    def test_deny_records_actor_reason_and_timestamp(self) -> None:
        """Denying a pending request stores denied_by, denied_at and the reason."""
        manager = BreakGlassManager()
        request = _create_request(manager)

        manager.deny(
            request.request_id,
            denied_by="compliance-officer@example.com",
            reason="Insufficient justification",
        )

        denied = manager.get_request(request.request_id)
        assert denied.status == BreakGlassStatus.DENIED
        assert denied.denied_by == "compliance-officer@example.com"
        assert denied.denial_reason == "Insufficient justification"
        assert denied.denied_at is not None
        denied_at = datetime.fromisoformat(denied.denied_at)
        assert denied_at >= datetime.fromisoformat(request.requested_at)

    def test_deny_non_pending_request_raises(self) -> None:
        """Denying an approved or already-denied request raises ValueError."""
        manager = BreakGlassManager()
        approved = _create_approved_request(manager)
        denied = _create_request(manager)
        manager.deny(denied.request_id, denied_by="officer@example.com", reason="no")

        for request in (approved, denied):
            with pytest.raises(ValueError, match="Request not pending"):
                manager.deny(
                    request.request_id,
                    denied_by="officer@example.com",
                    reason="no",
                )

    def test_deny_missing_request_raises(self) -> None:
        """Denying an unknown request id raises ValueError."""
        manager = BreakGlassManager()

        with pytest.raises(ValueError, match="Request not found"):
            manager.deny("missing", denied_by="officer@example.com", reason="no")

    @pytest.mark.parametrize("field", ["denied_by", "reason"])
    def test_deny_rejects_empty_values(self, field: str) -> None:
        """Empty denied_by or reason is rejected with ValueError."""
        manager = BreakGlassManager()
        request = _create_request(manager)
        kwargs = {"denied_by": "officer@example.com", "reason": "no"}
        kwargs[field] = ""

        with pytest.raises(ValueError):
            manager.deny(request.request_id, **kwargs)

        assert manager.get_request(request.request_id).status == BreakGlassStatus.PENDING


class TestBreakGlassRevoke:
    """Tests for BreakGlassManager.revoke()."""

    def test_revoke_records_actor_reason_and_timestamp(self) -> None:
        """Revoking an approved request stores revoked_by, revoked_at and the reason."""
        manager = BreakGlassManager()
        request = _create_approved_request(manager)

        manager.revoke(
            request.request_id,
            revoked_by="security@example.com",
            reason="Incident resolved",
        )

        revoked = manager.get_request(request.request_id)
        assert revoked.status == BreakGlassStatus.REVOKED
        assert revoked.revoked_by == "security@example.com"
        assert revoked.revocation_reason == "Incident resolved"
        assert revoked.revoked_at is not None
        revoked_at = datetime.fromisoformat(revoked.revoked_at)
        assert revoked_at >= datetime.fromisoformat(request.approved_at)

    def test_revoke_non_approved_request_raises(self) -> None:
        """Revoking a pending or denied request raises ValueError."""
        manager = BreakGlassManager()
        pending = _create_request(manager)
        denied = _create_request(manager)
        manager.deny(denied.request_id, denied_by="officer@example.com", reason="no")

        for request in (pending, denied):
            with pytest.raises(ValueError, match="Request not approved"):
                manager.revoke(
                    request.request_id,
                    revoked_by="security@example.com",
                    reason="no",
                )

    def test_revoke_missing_request_raises(self) -> None:
        """Revoking an unknown request id raises ValueError."""
        manager = BreakGlassManager()

        with pytest.raises(ValueError, match="Request not found"):
            manager.revoke("missing", revoked_by="security@example.com", reason="no")

    @pytest.mark.parametrize("field", ["revoked_by", "reason"])
    def test_revoke_rejects_empty_values(self, field: str) -> None:
        """Empty revoked_by or reason is rejected with ValueError."""
        manager = BreakGlassManager()
        request = _create_approved_request(manager)
        kwargs = {"revoked_by": "security@example.com", "reason": "no"}
        kwargs[field] = ""

        with pytest.raises(ValueError):
            manager.revoke(request.request_id, **kwargs)

        assert manager.get_request(request.request_id).status == BreakGlassStatus.APPROVED

    def test_revoked_request_is_not_valid(self) -> None:
        """A revoked request no longer counts as valid emergency access."""
        manager = BreakGlassManager()
        request = _create_approved_request(manager)
        assert manager.is_valid(request.request_id) is True

        manager.revoke(request.request_id, revoked_by="security@example.com", reason="no")

        assert manager.is_valid(request.request_id) is False
