"""Evidence summary helpers for governed workflow outputs.

EvidenceSummary is a frozen, JSON-serializable record of one workflow
action's inputs, outputs, checks, lineage, artifacts, and decisions. A
summary counts as passed only when every check payload reports passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """Serializable evidence summary for a workflow or publish action."""

    name: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    lineage_edges: list[tuple[str, str]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the evidence summary."""
        return {
            "name": self.name,
            "generated_at": self.generated_at.isoformat(),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "checks": self.checks,
            "lineage_edges": self.lineage_edges,
            "artifacts": self.artifacts,
            "decisions": self.decisions,
            "metadata": self.metadata,
        }


def collect_workflow_evidence(
    *,
    name: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    checks: list[dict[str, Any]] | None = None,
    lineage_edges: list[tuple[str, str]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceSummary:
    """Build an evidence summary for a workflow action."""
    return EvidenceSummary(
        name=name,
        inputs=inputs or [],
        outputs=outputs or [],
        checks=checks or [],
        lineage_edges=lineage_edges or [],
        artifacts=artifacts or [],
        decisions=decisions or [],
        metadata=metadata or {},
    )


def evidence_passed(summary: EvidenceSummary) -> bool:
    """Return whether all check payloads in an evidence summary passed."""
    return all(bool(check.get("passed", False)) for check in summary.checks)


def render_evidence_table(summary: EvidenceSummary) -> list[dict[str, Any]]:
    """Render evidence summary sections as rows for docs, APIs, or logs."""
    return [
        {"section": "inputs", "count": len(summary.inputs)},
        {"section": "outputs", "count": len(summary.outputs)},
        {"section": "checks", "count": len(summary.checks)},
        {"section": "lineage_edges", "count": len(summary.lineage_edges)},
        {"section": "artifacts", "count": len(summary.artifacts)},
        {"section": "decisions", "count": len(summary.decisions)},
    ]
