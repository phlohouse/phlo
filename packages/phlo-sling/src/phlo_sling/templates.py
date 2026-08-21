"""Sling-owned project templates."""

from __future__ import annotations

from phlo.cli.templates.builtin import MinimalTemplate, _write_pyproject_toml, _write_text
from phlo.cli.templates.models import ProjectTemplate, TemplateMetadata, TemplateRenderContext


class SlingReplicationTemplate:
    metadata = TemplateMetadata(
        name="sling-replication",
        description="Sling replication starter",
        required_packages=("phlo", "phlo-sling"),
        generated_paths=("replication/sling.yaml",),
        next_steps=("phlo sling --help",),
    )

    def render(self, context: TemplateRenderContext) -> None:
        MinimalTemplate().render(context)
        _write_pyproject_toml(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
        _write_text(
            context.project_dir / "replication" / "sling.yaml",
            "source: LOCAL\ntarget: POSTGRES\nstreams:\n  file://data/events.csv:\n    object: public.events\n",
        )
        _write_text(context.project_dir / "data" / "events.csv", "id,name\n1,alpha\n")


def templates() -> tuple[ProjectTemplate, ...]:
    return (SlingReplicationTemplate(),)
