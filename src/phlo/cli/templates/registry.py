from __future__ import annotations

import importlib.util
from collections.abc import Iterable

from phlo.cli.templates.models import ProjectTemplate
from phlo.plugins.discovery._entry_points import entry_points_for_group


class TemplateDiscoveryError(RuntimeError):
    """A provider supplied an invalid or conflicting project template."""


def _builtin_templates() -> tuple[ProjectTemplate, ...]:
    from phlo.cli.templates.builtin import MinimalTemplate

    return (MinimalTemplate(),)


def _provider_templates() -> Iterable[ProjectTemplate]:
    for entry_point in entry_points_for_group("phlo.project_templates"):
        try:
            templates = entry_point.load()()
        except Exception as exc:
            raise TemplateDiscoveryError(
                f"could not load project templates from provider '{entry_point.name}'"
            ) from exc
        yield from templates


def list_templates() -> tuple[ProjectTemplate, ...]:
    templates = (*_builtin_templates(), *_provider_templates())
    names = [template.metadata.name for template in templates]
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicates:
        raise TemplateDiscoveryError(
            f"multiple providers registered project template(s): {', '.join(duplicates)}"
        )
    return tuple(sorted(templates, key=lambda template: template.metadata.name))


def get_template(name: str) -> ProjectTemplate:
    for template in list_templates():
        if template.metadata.name == name:
            return template
    raise KeyError(name)


def missing_required_packages(template: ProjectTemplate) -> tuple[str, ...]:
    missing: list[str] = []
    for package in template.metadata.required_packages:
        import_name = package.replace("-", "_")
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)
    return tuple(missing)
