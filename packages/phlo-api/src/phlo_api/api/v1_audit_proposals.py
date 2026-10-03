"""Constrained declarative source generation for reviewed asset checks."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from difflib import unified_diff
import hashlib
import json
import keyword
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
    failure_policy: Literal["block", "warn"] = "block"
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
    rules: list[AuditRule] = Field(default_factory=list, max_length=20)
    failure_policy: Literal["block", "warn"] = "block"
    schema_digest: str | None = None
    table_relation: str | None = None


class AuditRuleResult(WireModel):
    kind: Literal["not_null", "unique", "range"]
    column: str
    passed: bool
    failure_count: int = Field(ge=0)
    failure_message: str | None


class AssetAuditTestResult(WireModel):
    proposal_id: str
    source_digest: str
    env: Literal["prod", "staging"]
    nessie_ref: str
    engine: Literal["trino"] = "trino"
    executed_at: AwareDatetime
    rows_checked: int = Field(ge=0, le=100)
    sampled: bool
    passed: bool
    results: list[AuditRuleResult]


def audit_schema_digest(columns: list[tuple[str, str | None]]) -> str:
    """Bind review and execution to the observed column names and types."""
    return hashlib.sha256(json.dumps(sorted(columns)).encode()).hexdigest()


def evaluate_audit_rules(
    rules: list[AuditRule], rows: list[dict[str, Any]], columns: list[str]
) -> list[AuditRuleResult]:
    """Use the same check implementations as the generated runtime contract."""
    import pandas as pd
    from phlo_pandera import NullCheck, RangeCheck, UniqueCheck

    frame = pd.DataFrame(rows, columns=columns)
    results = []
    for rule in rules:
        match rule:
            case NotNullRule(column=column):
                check = NullCheck(columns=[column])
                metric_key = column
            case UniqueRule(column=column):
                check = UniqueCheck(columns=[column])
                metric_key = "duplicate_count"
            case RangeRule(column=column, minimum=minimum, maximum=maximum):
                check = RangeCheck(column=column, min_value=minimum, max_value=maximum)
                metric_key = "out_of_range"
        # phlo_pandera skips an empty table before calling individual rules.
        result = check.execute(frame, None) if rows else None
        results.append(
            AuditRuleResult(
                kind=rule.kind,
                column=rule.column,
                passed=bool(result.passed) if result is not None else True,
                failure_count=int(result.metric_value.get(metric_key, 0))
                if result is not None and isinstance(result.metric_value, dict)
                else 0,
                failure_message=result.failure_message if result is not None else None,
            )
        )
    return results


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
    rules: list[AuditRule] | None = None,
    failure_policy: Literal["block", "warn"] = "block",
    schema_digest: str | None = None,
    table_relation: str | None = None,
) -> AssetAuditProposal:
    """Persist a deterministic, reviewable Git patch without writing project code."""
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    identity = json.dumps(
        [env, asset_id, nessie_ref, file_path, source_digest, schema_digest], separators=(",", ":")
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
        rules=rules or [],
        failure_policy=failure_policy,
        schema_digest=schema_digest,
        table_relation=table_relation,
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
    if proposal.rules:
        path, source = generate_check_file(
            proposal.asset_id.split("/"),
            proposal.check_name,
            proposal.rules,
            failure_policy=proposal.failure_policy,
            table_relation=proposal.table_relation,
        )
        if source != proposal.source or path != proposal.file_path:
            raise ValueError("Stored audit rule contract conflicts with source.")
    return proposal


def generate_check_file(
    asset_key: list[str],
    check_name: str,
    rules: list[AuditRule],
    *,
    failure_policy: Literal["block", "warn"] = "block",
    table_relation: str | None = None,
) -> tuple[str, str]:
    """Return a safe workflow path and syntactically validated Pandera source."""
    if not asset_key or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in asset_key
    ):
        raise ValueError("Asset key cannot be represented as a project table identifier.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", check_name) or keyword.iskeyword(check_name):
        raise ValueError("Check name is not a valid Python identifier.")
    if not 1 <= len(rules) <= 20:
        raise ValueError("A check proposal must contain 1 to 20 rules.")

    lines = ["        " + _render_rule(rule) + "," for rule in rules]
    table = table_relation or ".".join(asset_key)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("Audit table must be a validated schema and table identifier.")
    source = (
        '"""Generated quality check proposal. Review before merging."""\n\n'
        "from phlo_pandera import NullCheck, RangeCheck, UniqueCheck, phlo_pandera\n\n\n"
        "@phlo_pandera(\n"
        f"    table={json.dumps(table)},\n"
        f"    asset_key={json.dumps('.'.join(asset_key))},\n"
        f"    blocking={failure_policy == 'block'!r},\n"
        f"    warn_threshold={0.0 if failure_policy == 'block' else 1.0},\n"
        "    full_table=True,\n"
        "    partition_aware=False,\n"
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
