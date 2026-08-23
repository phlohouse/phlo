"""Data models for phlo project templates.

TemplateMetadata describes a template's contract (required packages, generated
paths, next steps); TemplateRenderContext carries the target directory and
force flag; ProjectTemplate is the Protocol each template implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TemplateMetadata:
    name: str
    description: str
    required_packages: tuple[str, ...] = ()
    generated_paths: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateRenderContext:
    project_dir: Path
    project_name: str
    force: bool = False


class ProjectTemplate(Protocol):
    metadata: TemplateMetadata

    def render(self, context: TemplateRenderContext) -> None:
        """Render template files into the project directory."""
