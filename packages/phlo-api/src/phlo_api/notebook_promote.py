"""Turn a notebook's Trino SQL cell into a dbt model in the project.

The model lands in ``workflows/transforms/dbt/models/notebooks/<name>.sql`` with
a small ``.yml`` describing where it came from. Tables the SQL reads become
``ref()`` when the project already has a dbt model of that name, ``source()``
when a dbt source declares them, and plain ``schema.table`` otherwise, so the
model keeps its lineage wherever dbt can see it. Nothing is overwritten: an
existing model of the same name is a conflict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from phlo_api.notebook_git import PROMOTED_DIR

DBT_ROOT = "workflows/transforms/dbt"
_MODEL = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


class PromoteInvalid(ValueError):
    """The SQL or model name cannot become a dbt model."""


class PromoteConflict(RuntimeError):
    """A model with that name already exists."""


@dataclass(frozen=True)
class PromotedFile:
    path: str
    content: str
    exists: bool


def _models(project: Path) -> set[str]:
    root = project / DBT_ROOT / "models"
    return {file.stem for file in root.rglob("*.sql")} if root.is_dir() else set()


def _sources(project: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """(schema, table) -> (source name, table name) from dbt source declarations."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    root = project / DBT_ROOT / "models"
    if not root.is_dir():
        return found
    for file in [*root.rglob("*.yml"), *root.rglob("*.yaml")]:
        try:
            data: Any = yaml.safe_load(file.read_text())
        except (OSError, yaml.YAMLError):
            continue
        for source in (data or {}).get("sources", []) if isinstance(data, dict) else []:
            if not isinstance(source, dict) or not isinstance(source.get("name"), str):
                continue
            schema = str(source.get("schema") or source["name"]).lower()
            for table in source.get("tables") or []:
                if isinstance(table, dict) and isinstance(table.get("name"), str):
                    identifier = str(table.get("identifier") or table["name"]).lower()
                    found[(schema, identifier)] = (source["name"], table["name"])
    return found


def render_model(project: Path, sql: str, catalog: str) -> str:
    """Rewrite table references for dbt and return the model body."""
    try:
        statement = parse_one(sql, read="trino")
    except ParseError as exc:
        raise PromoteInvalid("The cell's SQL could not be parsed.") from exc
    if not isinstance(statement, exp.Query):
        raise PromoteInvalid("Only a read-only query can become a model.")
    models, sources = _models(project), _sources(project)
    replacements: dict[str, str] = {}
    for index, table in enumerate(list(statement.find_all(exp.Table))):
        reference = table.args.get("catalog")
        if reference is not None and reference.name != catalog:
            raise PromoteInvalid("The SQL reads a catalog outside this environment.")
        schema, name = table.db.lower(), table.name.lower()
        if not name:
            continue
        if name in models:
            jinja = f"{{{{ ref('{name}') }}}}"
        elif (schema, name) in sources:
            source, source_table = sources[(schema, name)]
            jinja = f"{{{{ source('{source}', '{source_table}') }}}}"
        elif schema:
            jinja = f"{schema}.{name}"
        else:
            continue
        token = f"__phlo_ref_{index}__"
        replacements[token] = jinja
        alias = table.args.get("alias")
        replacement = exp.Table(this=exp.to_identifier(token))
        if alias is not None:
            replacement.set("alias", alias)
        table.replace(replacement)
    body = statement.sql(dialect="trino", pretty=True)
    for token, jinja in replacements.items():
        body = body.replace(token, jinja)
    return body


def plan(
    project: Path, *, notebook: str, cell_name: str | None, sql: str, model: str, catalog: str
) -> list[PromotedFile]:
    if not _MODEL.fullmatch(model):
        raise PromoteInvalid("Model names use lowercase letters, digits and '_'.")
    if not (project / DBT_ROOT / "dbt_project.yml").is_file():
        raise PromoteInvalid(f"This project has no dbt project at {DBT_ROOT}.")
    if model in _models(project) and not (project / PROMOTED_DIR / f"{model}.sql").exists():
        raise PromoteConflict(f"The project already has a dbt model called {model}.")
    origin = f"notebooks/{notebook}" + (f", cell {cell_name}" if cell_name else "")
    body = render_model(project, sql, catalog)
    content = f"-- Promoted from {origin}.\n{{{{ config(materialized='table') }}}}\n\n{body}\n"
    description = yaml.safe_dump(
        {
            "version": 2,
            "models": [
                {
                    "name": model,
                    "description": f"Promoted from {origin} in Observatory.",
                    "meta": {"phlo": {"notebook": notebook, "cell": cell_name}},
                }
            ],
        },
        sort_keys=False,
    )
    return [
        PromotedFile(
            path=f"{PROMOTED_DIR}/{model}.sql",
            content=content,
            exists=(project / PROMOTED_DIR / f"{model}.sql").exists(),
        ),
        PromotedFile(
            path=f"{PROMOTED_DIR}/{model}.yml",
            content=description,
            exists=(project / PROMOTED_DIR / f"{model}.yml").exists(),
        ),
    ]


def apply(project: Path, files: list[PromotedFile]) -> None:
    """Write the planned files, refusing to replace any that exist."""
    if any(item.exists or (project / item.path).exists() for item in files):
        raise PromoteConflict("A model with that name already exists.")
    folder = project / PROMOTED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for item in files:
        target = project / item.path
        if target.parent.resolve() != folder.resolve():
            raise PromoteInvalid("Promoted models stay in the notebooks models folder.")
        with open(target, "x", encoding="utf-8") as stream:
            stream.write(item.content)
