"""Provider-neutral workflow wizard contracts.

Dataclasses describing wizard stages, contributions, fields, proposals, and
apply actions, with to_browser_dict() serializers that strip sensitive keys
(_SENSITIVE_KEY_PARTS) before anything reaches the browser. Also provides
proposal request validation and file-conflict detection against disk state.
Surfaced through the phlo.capabilities package root and exercised by plugin
contract tests; defines browser-safe wizard contracts with no phlo imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

WorkflowStage = Literal["source", "transform", "quality", "publish"]
WorkflowFieldType = Literal["text", "textarea", "select", "checkbox", "fields"]
WorkflowFileMode = Literal["create", "modify"]
WorkflowConflictPolicy = Literal["preview", "skip-if-exists", "fail-on-conflict"]

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "dsn",
    "url",
    "uri",
    "key",
)


class WorkflowContributionMode(StrEnum):
    """Supported workflow contribution execution modes."""

    PROPOSAL = "proposal"
    APPLY = "apply"


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            continue
        if isinstance(value, str) and (
            "://" in value or "token=" in value.lower() or value.lower().startswith("bearer ")
        ):
            continue
        if isinstance(value, dict):
            nested = _safe_metadata(value)
            if nested:
                safe[key] = nested
        elif isinstance(value, list):
            safe_items = [
                _safe_metadata(item) if isinstance(item, dict) else item
                for item in value
                if not (isinstance(item, str) and ("://" in item or "token=" in item.lower()))
            ]
            if safe_items:
                safe[key] = safe_items
        elif value is None or isinstance(value, str | int | float | bool):
            safe[key] = value
    return safe


@dataclass(frozen=True, slots=True)
class WorkflowWizardField:
    """A simple field Observatory can render for a wizard contribution."""

    name: str
    label: str
    field_type: WorkflowFieldType = "text"
    required: bool = False
    description: str | None = None
    default: Any = None
    options: list[str] = field(default_factory=list)
    secret: bool = False

    def to_browser_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable rendering of this field for the browser."""
        return {
            "name": self.name,
            "label": self.label,
            "field_type": self.field_type,
            "required": self.required,
            "description": self.description,
            "default": self.default,
            "options": list(self.options),
            "secret": self.secret,
        }


@dataclass(frozen=True, slots=True)
class WorkflowWizardContribution:
    """One package-provided wizard option for a workflow stage."""

    id: str
    package: str
    stage: WorkflowStage
    label: str
    description: str
    required_capabilities: list[str] = field(default_factory=list)
    optional_capabilities: list[str] = field(default_factory=list)
    fields: list[WorkflowWizardField] = field(default_factory=list)
    modes: set[WorkflowContributionMode] = field(
        default_factory=lambda: {WorkflowContributionMode.PROPOSAL}
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_browser_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable rendering of this contribution, including its fields."""
        return {
            "id": self.id,
            "package": self.package,
            "stage": self.stage,
            "label": self.label,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "fields": [field.to_browser_dict() for field in self.fields],
            "modes": sorted(str(mode) for mode in self.modes),
            "metadata": _safe_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class WorkflowStageSelection:
    """User-selected contribution and values for one stage."""

    contribution_id: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowProposalRequest:
    """Request to build a side-effect-free workflow proposal."""

    workflow_name: str
    domain: str
    selections: dict[
        str,
        dict[str, Any] | WorkflowStageSelection | list[dict[str, Any] | WorkflowStageSelection],
    ] = field(default_factory=dict)

    def selection_for(self, stage: str) -> WorkflowStageSelection | None:
        """Return the first selection for a stage, or None when the stage has none."""
        selections = self.selections_for(stage)
        return selections[0] if selections else None

    def selections_for(self, stage: str) -> list[WorkflowStageSelection]:
        """Return the stage's selections coerced to WorkflowStageSelection; empty when unset."""
        raw = self.selections.get(stage)
        if raw is None:
            return []
        if isinstance(raw, list):
            return [_coerce_selection(item) for item in raw]
        return [_coerce_selection(raw)]


def _coerce_selection(raw: dict[str, Any] | WorkflowStageSelection) -> WorkflowStageSelection:
    if isinstance(raw, WorkflowStageSelection):
        return raw
    return WorkflowStageSelection(
        contribution_id=str(raw.get("contribution_id", "")),
        values=dict(raw.get("values", {})),
    )


@dataclass(frozen=True, slots=True)
class WorkflowFilePreview:
    """Generated or modified file preview for a workflow proposal."""

    path: str
    content: str
    mode: WorkflowFileMode = "create"

    def to_browser_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable rendering of this file preview."""
        return {"path": self.path, "content": self.content, "mode": self.mode}


@dataclass(frozen=True, slots=True)
class WorkflowApplyAction:
    """Guarded file-writing action for a workflow proposal."""

    id: str
    label: str
    target_files: list[str]
    conflict_policy: WorkflowConflictPolicy = "fail-on-conflict"
    enabled: bool = True
    reason: str | None = None
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    required_permission: str | None = "settings:manage"
    expected_evidence: list[str] = field(default_factory=list)

    def to_browser_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable rendering of this apply action."""
        return {
            "id": self.id,
            "label": self.label,
            "target_files": list(self.target_files),
            "conflict_policy": self.conflict_policy,
            "enabled": self.enabled,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "required_permission": self.required_permission,
            "expected_evidence": list(self.expected_evidence),
        }


@dataclass(frozen=True, slots=True)
class WorkflowProposal:
    """Side-effect-free workflow plan."""

    workflow_name: str
    domain: str
    selected_contributions: list[str] = field(default_factory=list)
    planned_assets: list[str] = field(default_factory=list)
    planned_tables: list[str] = field(default_factory=list)
    planned_models: list[str] = field(default_factory=list)
    files: list[WorkflowFilePreview] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    disabled_stages: dict[str, str] = field(default_factory=dict)
    actions: list[WorkflowApplyAction] = field(default_factory=list)

    def to_browser_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable rendering of this proposal, including files and actions."""
        return {
            "workflow_name": self.workflow_name,
            "domain": self.domain,
            "selected_contributions": list(self.selected_contributions),
            "planned_assets": list(self.planned_assets),
            "planned_tables": list(self.planned_tables),
            "planned_models": list(self.planned_models),
            "files": [file.to_browser_dict() for file in self.files],
            "warnings": list(self.warnings),
            "missing_capabilities": list(self.missing_capabilities),
            "disabled_stages": dict(self.disabled_stages),
            "actions": [action.to_browser_dict() for action in self.actions],
        }


def validate_proposal_request(request: WorkflowProposalRequest) -> dict[str, list[str]]:
    """Validate workflow proposal input independently from package logic."""

    errors: dict[str, list[str]] = {}
    if not request.workflow_name.strip():
        errors.setdefault("workflow_name", []).append("Enter a workflow name.")
    if not request.domain.strip():
        errors.setdefault("domain", []).append("Enter a domain.")
    source = request.selection_for("source")
    if source is None or not source.contribution_id:
        errors.setdefault("source", []).append("Select a source contribution.")
    return errors


def detect_file_conflicts(project_root: Path, proposal: WorkflowProposal) -> list[str]:
    """Return proposal file paths that already exist in the project."""

    conflicts: list[str] = []
    for preview in proposal.files:
        if preview.mode == "create" and (project_root / preview.path).exists():
            conflicts.append(preview.path)
    return conflicts
