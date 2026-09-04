"""Pure, versioned, project-configured Dataset policy evaluator.

The evaluator carries no rules of its own: every control, condition, and
required-evidence kind comes from a versioned :class:`DatasetPolicy` the
project supplies, so promotion and publication policy are project decisions.
The policy is owned by core, configured per project, and versioned with the
Dataset record. Evaluation is a pure function of policy plus evidence:
same inputs, same verdict, in the same order, regardless of evidence discovery
order. Missing evidence stays missing -- it is reported as its own finding and
blocks readiness without ever being counted as a failed control.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from phlo.dataset.evidence import EvidenceRecord

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"

CONDITION_OPERATORS = frozenset({"eq", "ne", "in", "not_in", "true", "false"})

CONTROL_PASSED = "passed"
CONTROL_FAILED = "failed"
CONTROL_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class EvidenceCondition:
    """Declarative assertion against one evidence payload field."""

    field: str
    operator: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.operator not in CONDITION_OPERATORS:
            raise ValueError(f"Unknown condition operator: {self.operator}")

    def matches(self, payload: Mapping[str, Any]) -> bool:
        """Evaluate the condition against one evidence payload."""
        if self.operator in {"true", "false"}:
            actual = bool(payload.get(self.field))
            return actual if self.operator == "true" else not actual
        if self.field not in payload:
            return False
        actual = payload[self.field]
        if self.operator == "eq":
            return actual == self.value
        if self.operator == "ne":
            return actual != self.value
        if self.operator == "in":
            return actual in self.value
        return actual not in self.value


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One project-configured control over a named evidence kind."""

    control: str
    evidence_kind: str
    condition: EvidenceCondition | None = None
    severity: str = SEVERITY_BLOCKER
    message: str = ""
    applies_to: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.control:
            raise ValueError("policy rule control is required")
        if not self.evidence_kind:
            raise ValueError("policy rule evidence_kind is required")
        if self.severity not in {SEVERITY_BLOCKER, SEVERITY_WARNING}:
            raise ValueError(f"Unknown policy rule severity: {self.severity}")

    def applies_to_action(self, action: str) -> bool:
        return not self.applies_to or action in self.applies_to


@dataclass(frozen=True, slots=True)
class TransitionPolicy:
    """Evidence kinds a project requires for one transition action."""

    action: str
    required_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DatasetPolicy:
    """Versioned, project-supplied policy for Dataset transitions."""

    policy_version: str
    rules: tuple[PolicyRule, ...] = ()
    transitions: tuple[TransitionPolicy, ...] = ()

    def __post_init__(self) -> None:
        if not self.policy_version:
            raise ValueError("DatasetPolicy requires a policy_version")

    def for_action(self, action: str) -> TransitionPolicy:
        """Return the project's evidence requirements for one action."""
        for transition in self.transitions:
            if transition.action == action:
                return transition
        return TransitionPolicy(action=action)


@dataclass(frozen=True, slots=True)
class MissingEvidence:
    """A required evidence kind the source could not provide.

    ``blocks`` is True when the missing evidence decides readiness (required
    kinds and blocker-severity controls); warning-severity controls report
    their gap without blocking the transition.
    """

    kind: str
    control: str | None
    message: str
    blocks: bool = True

    def to_read_model(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "control": self.control,
            "message": self.message,
            "blocks": self.blocks,
        }


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    """One failed control (blocker or warning) raised by the evaluator."""

    control: str
    severity: str
    message: str
    evidence_kind: str

    def to_read_model(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "severity": self.severity,
            "message": self.message,
            "evidence_kind": self.evidence_kind,
        }


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """Deterministic readiness verdict for one action over one Dataset."""

    dataset_id: str
    action: str
    policy_version: str
    ready: bool
    controls: tuple[dict[str, Any], ...]
    blockers: tuple[PolicyFinding, ...]
    warnings: tuple[PolicyFinding, ...]
    missing_evidence: tuple[MissingEvidence, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        """Human-readable reasons behind the verdict, in deterministic order."""
        reasons = [finding.message for finding in self.blockers]
        reasons.extend(missing.message for missing in self.missing_evidence)
        reasons.extend(finding.message for finding in self.warnings)
        return tuple(reasons)

    def to_read_model(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "action": self.action,
            "policy_version": self.policy_version,
            "ready": self.ready,
            "controls": list(self.controls),
            "blockers": [finding.to_read_model() for finding in self.blockers],
            "warnings": [finding.to_read_model() for finding in self.warnings],
            "missing_evidence": [missing.to_read_model() for missing in self.missing_evidence],
            "reasons": list(self.reasons),
        }


def evaluate_policy(
    policy: DatasetPolicy,
    dataset_id: str,
    action: str,
    evidence: tuple[EvidenceRecord, ...],
) -> PolicyVerdict:
    """Evaluate one project policy against evidence for one action.

    Pure function: no I/O, no global state, no defaults beyond the policy
    itself. Controls, blockers, warnings, and missing evidence are sorted, so
    evidence discovery order cannot change the verdict.
    """
    required_kinds = policy.for_action(action).required_evidence
    available: dict[str, list[EvidenceRecord]] = {}
    for record in evidence:
        if record.is_missing:
            continue
        available.setdefault(record.kind, []).append(record)

    missing: list[MissingEvidence] = []
    blockers: list[PolicyFinding] = []
    warnings: list[PolicyFinding] = []
    controls: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()

    for kind in sorted(required_kinds):
        seen_kinds.add(kind)
        if kind not in available:
            missing.append(
                MissingEvidence(
                    kind=kind,
                    control=None,
                    message=f"Required evidence of kind {kind!r} is missing for {dataset_id}.",
                    blocks=True,
                )
            )

    for rule in sorted((r for r in policy.rules if r.applies_to_action(action)), key=_rule_order):
        seen_kinds.add(rule.evidence_kind)
        records = available.get(rule.evidence_kind, [])
        if not records:
            missing.append(
                MissingEvidence(
                    kind=rule.evidence_kind,
                    control=rule.control,
                    message=(
                        rule.message
                        or f"Control {rule.control!r} lacks required evidence of kind "
                        f"{rule.evidence_kind!r} for {dataset_id}."
                    ),
                    blocks=rule.severity == SEVERITY_BLOCKER,
                )
            )
            controls.append(
                {
                    "control": rule.control,
                    "status": CONTROL_MISSING,
                    "severity": rule.severity,
                    "evidence_kind": rule.evidence_kind,
                }
            )
            continue

        failed = [
            record
            for record in records
            if rule.condition is not None and not rule.condition.matches(record.payload)
        ]
        if failed:
            finding = PolicyFinding(
                control=rule.control,
                severity=rule.severity,
                message=rule.message or f"Control {rule.control!r} failed for {dataset_id}.",
                evidence_kind=rule.evidence_kind,
            )
            (blockers if rule.severity == SEVERITY_BLOCKER else warnings).append(finding)
            controls.append(
                {
                    "control": rule.control,
                    "status": CONTROL_FAILED,
                    "severity": rule.severity,
                    "evidence_kind": rule.evidence_kind,
                }
            )
            continue

        controls.append(
            {
                "control": rule.control,
                "status": CONTROL_PASSED,
                "severity": rule.severity,
                "evidence_kind": rule.evidence_kind,
            }
        )

    for record in sorted(evidence, key=lambda item: (item.kind, item.subject)):
        if record.kind in seen_kinds:
            continue
        controls.append(
            {
                "control": None,
                "status": record.status,
                "severity": None,
                "evidence_kind": record.kind,
            }
        )

    controls.sort(key=lambda item: (item["evidence_kind"], item["control"] or ""))
    blockers.sort(key=_finding_order)
    warnings.sort(key=_finding_order)
    missing.sort(key=lambda item: (item.kind, item.control or ""))

    return PolicyVerdict(
        dataset_id=dataset_id,
        action=action,
        policy_version=policy.policy_version,
        ready=not blockers and not any(entry.blocks for entry in missing),
        controls=tuple(controls),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        missing_evidence=tuple(missing),
    )


def _rule_order(rule: PolicyRule) -> tuple[str, str]:
    return (rule.evidence_kind, rule.control)


def _finding_order(finding: PolicyFinding) -> tuple[str, str]:
    return (finding.evidence_kind, finding.control)
