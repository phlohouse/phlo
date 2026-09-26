"""Constrained declarative source generation for reviewed asset checks."""

from __future__ import annotations

import ast
import json
import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

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
