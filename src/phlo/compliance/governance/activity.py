"""Access activity monitoring and SoD violation detection.

Monitors access patterns and detects separation of duties violations,
dormancy issues, and other compliance-relevant activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phlo.compliance.governance.types import (
        SeparationOfDutiesPolicy,
        SeparationOfDutiesViolation,
    )


@dataclass(frozen=True, kw_only=True)
class AccessActivity:
    """A recorded access activity event."""

    activity_id: str
    principal_subject: str
    principal_type: str
    action: str
    resource_type: str
    resource_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    result: str = "success"
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ActivitySummary:
    """Summary of access activity for a principal."""

    principal_subject: str
    total_accesses: int
    unique_resources: int
    actions_performed: tuple[str, ...]
    last_access: str | None
    detected_violations: int


class ActivityMonitor:
    """Monitors access activity and detects compliance issues.

    Tracks access patterns and correlates with governance policies
    to detect SoD violations, dormancy, and other issues.
    """

    def __init__(
        self,
        sod_policies: list[SeparationOfDutiesPolicy] | None = None,
    ) -> None:
        self._policies = sod_policies or []
        self._activities: dict[str, list[AccessActivity]] = {}
        self._violations: list[SeparationOfDutiesViolation] = []

    def record_activity(
        self,
        principal_subject: str,
        principal_type: str,
        action: str,
        resource_type: str,
        resource_id: str,
        roles: tuple[str, ...] | None = None,
        result: str = "success",
    ) -> AccessActivity:
        """Record an access attempt and file it under the principal's history."""
        activity = AccessActivity(
            activity_id=self._generate_id(),
            principal_subject=principal_subject,
            principal_type=principal_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            roles=roles or (),
            result=result,
        )

        if principal_subject not in self._activities:
            self._activities[principal_subject] = []
        self._activities[principal_subject].append(activity)

        return activity

    def check_sod_violations(
        self,
        principal_subject: str,
        roles: tuple[str, ...],
    ) -> list[SeparationOfDutiesViolation]:
        """Check a subject's current roles against SoD policies, accumulating violations."""
        from phlo.compliance.governance.types import check_separation_of_duties

        violations = check_separation_of_duties(
            principal_subject=principal_subject,
            roles=roles,
            policies=self._policies,
        )

        self._violations.extend(violations)
        return violations

    def get_principal_activities(
        self,
        principal_subject: str,
        limit: int | None = None,
    ) -> list[AccessActivity]:
        """Return a principal's activities, most recent first, optionally bounded."""
        activities = self._activities.get(principal_subject, [])
        sorted_activities = sorted(
            activities,
            key=lambda a: a.timestamp,
            reverse=True,
        )
        if limit is not None:
            return sorted_activities[:limit]
        return sorted_activities

    def get_summary(self, principal_subject: str) -> ActivitySummary:
        """Summarize a principal's recorded activity and detected violations."""
        activities = self._activities.get(principal_subject, [])

        if not activities:
            return ActivitySummary(
                principal_subject=principal_subject,
                total_accesses=0,
                unique_resources=0,
                actions_performed=(),
                last_access=None,
                detected_violations=0,
            )

        unique_resources = len({(a.resource_type, a.resource_id) for a in activities})
        actions = tuple({a.action for a in activities})
        last_access = max(a.timestamp for a in activities)
        violations = sum(1 for v in self._violations if v.principal_subject == principal_subject)

        return ActivitySummary(
            principal_subject=principal_subject,
            total_accesses=len(activities),
            unique_resources=unique_resources,
            actions_performed=actions,
            last_access=last_access,
            detected_violations=violations,
        )

    def get_all_violations(self) -> list[SeparationOfDutiesViolation]:
        """Return every SoD violation detected by this monitor so far."""
        return list(self._violations)

    def _generate_id(self) -> str:
        from uuid import uuid4

        return str(uuid4())


def record_enforcement_activity(
    principal_subject: str,
    principal_type: str,
    action: str,
    resource_type: str,
    resource_id: str,
    roles: tuple[str, ...],
    result: str,
) -> AccessActivity:
    """Record enforcement-driven access activity on the shared default monitor."""
    monitor = _get_default_monitor()
    return monitor.record_activity(
        principal_subject=principal_subject,
        principal_type=principal_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        roles=roles,
        result=result,
    )


_default_monitor: ActivityMonitor | None = None


def _get_default_monitor() -> ActivityMonitor:
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = ActivityMonitor()
    return _default_monitor
