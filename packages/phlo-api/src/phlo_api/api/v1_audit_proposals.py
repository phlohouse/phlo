"""Constrained declarative source generation for reviewed asset checks."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from difflib import unified_diff
import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.v1_contract import WireModel

ColumnName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]


class NotNullRule(WireModel):
    kind: Literal["not_null"]
    column: ColumnName


class UniqueRule(WireModel):
    kind: Literal["unique"]
    column: ColumnName


class RangeRule(WireModel):
    kind: Literal["range"]
    column: ColumnName
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def require_ordered_bounds(self) -> RangeRule:
        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


AuditRule = Annotated[NotNullRule | UniqueRule | RangeRule, Field(discriminator="kind")]


class AssetAuditProposalRequest(WireModel):
    """Request for a review-only code proposal; never executes an audit."""

    check_name: Annotated[
        str,
        StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$"),
    ]
    rules: list[AuditRule] = Field(min_length=1, max_length=20)
    idempotency_key: Annotated[
        str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")
    ]


class AssetAuditProposal(WireModel):
    """A source-only quality check proposal awaiting human Git review."""

    proposal_id: str
    env: Literal["prod", "staging"]
    asset_id: str
    nessie_ref: str
    check_name: str
    file_path: str
    source_digest: str
    source: str
    patch: str
    status: Literal["pending_review"]
    created_at: AwareDatetime


_MAX_STORED_PROPOSALS = 200


def create_audit_proposal(
    *,
    project_root: Path,
    env: Literal["prod", "staging"],
    asset_id: str,
    nessie_ref: str,
    check_name: str,
    file_path: str,
    source: str,
) -> AssetAuditProposal:
    """Persist a deterministic, reviewable Git patch without writing project code."""
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    identity = json.dumps(
        [env, asset_id, nessie_ref, file_path, source_digest], separators=(",", ":")
    )
    proposal_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    patch = "".join(
        unified_diff(
            [],
            source.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{file_path}",
        )
    )
    record = AssetAuditProposal(
        proposal_id=proposal_id,
        env=env,
        asset_id=asset_id,
        nessie_ref=nessie_ref,
        check_name=check_name,
        file_path=file_path,
        source_digest=source_digest,
        source=source,
        patch=patch,
        status="pending_review",
        created_at=datetime.now(UTC),
    )
    state_dir = project_root / ".phlo" / "observatory"
    state_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = state_dir / "v1_audit_proposals.json"
    payload = record.model_dump(mode="json")

    def upsert(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = next((item for item in items if item.get("proposal_id") == proposal_id), None)
        if existing is not None:
            if (
                existing.get("source_digest") != source_digest
                or existing.get("file_path") != file_path
                or existing.get("nessie_ref") != nessie_ref
            ):
                raise ValueError("Audit proposal identity conflicts with stored content.")
            return items
        if len(items) >= _MAX_STORED_PROPOSALS:
            raise RuntimeError("Audit proposal storage limit reached.")
        return [*items, payload]

    stored = mutate_collection(project_root, "v1_audit_proposals", legacy_path, upsert)
    stored_record = next(item for item in stored if item.get("proposal_id") == proposal_id)
    return AssetAuditProposal.model_validate_json(json.dumps(stored_record))


def get_audit_proposal(project_root: Path, proposal_id: str) -> AssetAuditProposal | None:
    """Load a persisted proposal by its opaque stable identifier."""
    state_dir = project_root / ".phlo" / "observatory"
    state_dir.mkdir(parents=True, exist_ok=True)
    records = load_collection(
        project_root, "v1_audit_proposals", state_dir / "v1_audit_proposals.json"
    )
    stored = next((item for item in records if item.get("proposal_id") == proposal_id), None)
    if stored is None:
        return None
    proposal = AssetAuditProposal.model_validate_json(json.dumps(stored))
    if hashlib.sha256(proposal.source.encode("utf-8")).hexdigest() != proposal.source_digest:
        raise ValueError("Stored audit proposal integrity check failed.")
    return proposal


def generate_check_file(
    asset_key: list[str], check_name: str, rules: list[AuditRule]
) -> tuple[str, str]:
    """Return a safe workflow path and syntactically validated Pandera source."""
    if not asset_key or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in asset_key
    ):
        raise ValueError("Asset key cannot be represented as a project table identifier.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", check_name):
        raise ValueError("Check name is not a valid Python identifier.")
    if not 1 <= len(rules) <= 20:
        raise ValueError("A check proposal must contain 1 to 20 rules.")

    lines = ["        " + _render_rule(rule) + "," for rule in rules]
    table = ".".join(asset_key)
    source = (
        '"""Generated quality check proposal. Review before merging."""\n\n'
        "from phlo_pandera import NullCheck, RangeCheck, UniqueCheck, phlo_pandera\n\n\n"
        "@phlo_pandera(\n"
        f"    table={json.dumps(table)},\n"
        "    checks=[\n" + "\n".join(lines) + "\n    ],\n)\n"
        f"def {check_name}():\n"
        "    pass\n"
    )
    ast.parse(source, mode="exec")
    path = f"workflows/quality/{'_'.join(asset_key)}_{check_name}.py"
    return path, source


def _render_rule(rule: AuditRule) -> str:
    match rule:
        case NotNullRule(column=column):
            return f"NullCheck(columns=[{json.dumps(column)}])"
        case UniqueRule(column=column):
            return f"UniqueCheck(columns=[{json.dumps(column)}])"
        case RangeRule(column=column, minimum=minimum, maximum=maximum):
            return (
                f"RangeCheck(column={json.dumps(column)}, min_value={minimum!r}, "
                f"max_value={maximum!r})"
            )
