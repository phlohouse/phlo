"""dbt-owned project templates.

Both templates build on the CLI's MinimalTemplate and add a dbt
scaffold under workflows/transforms/dbt; DbtMedallionTemplate layers
bronze/silver/gold sample models on top of BasicTemplate. templates()
is the discovery entry point consumed by the template registry.
"""

from __future__ import annotations

from phlo.cli.templates.builtin import MinimalTemplate, _write_pyproject_toml, _write_text
from phlo.cli.templates.models import ProjectTemplate, TemplateMetadata, TemplateRenderContext
from phlo_dbt.scaffold import write_dbt_scaffold


class BasicTemplate:
    metadata = TemplateMetadata(
        name="basic",
        description="dbt-ready Phlo project",
        required_packages=("phlo", "phlo-dbt"),
        generated_paths=(
            "phlo.yaml",
            "pyproject.toml",
            "workflows/transforms/dbt/dbt_project.yml",
        ),
        next_steps=("phlo services init", "phlo workflow create"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Scaffold a dbt-ready project on top of the minimal template."""
        MinimalTemplate().render(context)
        _write_pyproject_toml(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
        transforms_dir = context.project_dir / "workflows" / "transforms" / "dbt"
        write_dbt_scaffold(context.project_name, transforms_dir, context.project_dir)
        _write_text(transforms_dir / "models" / ".gitkeep", "")


class DbtMedallionTemplate:
    metadata = TemplateMetadata(
        name="dbt-medallion",
        description="Bronze/silver/gold dbt project",
        required_packages=("phlo", "phlo-dbt"),
        generated_paths=("workflows/transforms/dbt/models/silver/stg_events.sql",),
        next_steps=("phlo dbt compile", "phlo services restart --service dagster"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Scaffold a basic project plus bronze/silver/gold sample models."""
        BasicTemplate().render(context)
        base = context.project_dir / "workflows" / "transforms" / "dbt" / "models"
        _write_text(base / "bronze" / "source_events.sql", "select 1 as id, 'sample' as name\n")
        _write_text(
            base / "silver" / "stg_events.sql",
            "select id, name from {{ ref('source_events') }}\n",
        )
        _write_text(
            base / "gold" / "dim_events.sql",
            "select id, name from {{ ref('stg_events') }}\n",
        )
        _write_text(base / "sources.yml", "version: 2\nsources: []\n")


def templates() -> tuple[ProjectTemplate, ...]:
    return (BasicTemplate(), DbtMedallionTemplate())
