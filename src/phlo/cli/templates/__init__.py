"""Public API for discovering and rendering Phlo project templates.

Re-exports template models and the registry lookup/list functions as the
package's stable surface.
"""

from phlo.cli.templates.models import ProjectTemplate, TemplateMetadata, TemplateRenderContext
from phlo.cli.templates.registry import get_template, list_templates

__all__ = [
    "ProjectTemplate",
    "TemplateMetadata",
    "TemplateRenderContext",
    "get_template",
    "list_templates",
]
