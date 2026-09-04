"""Retail Files project-template provider.

The package is discovered through the `phlo.project_templates` entry-point
group and renders the canonical Retail Files project from packaged resources.
Its rendered pyproject uses the contract's exact released phlo-family pins and
declared third-party dependencies.
"""

from __future__ import annotations

import shutil

from phlo.cli.templates.models import TemplateMetadata, TemplateRenderContext

from phlo_retail_files.contract import RESOURCES_DIR, load_contract

_RENDERED_PYPROJECT = """\
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools>=75", "wheel"]

[project]
description = "{description}"
name = "{project_name}"
requires-python = ">=3.11"
version = "0.1.0"
dependencies = [
{dependencies}
]

[dependency-groups]
dev = [
{dev_dependencies}
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I"]

[tool.setuptools.packages.find]
include = ["scripts*", "workflows*"]
"""


def _rendered_requirement_lines(requirements: tuple[str, ...], indent: str) -> str:
    return "\n".join(f'{indent}"{requirement}",' for requirement in requirements)


def _rendered_pyproject(project_name: str, contract: dict) -> str:
    runtime = tuple(contract["rendered_dependencies"]["runtime"])
    dev = tuple(contract["rendered_dependencies"]["dev"])
    return _RENDERED_PYPROJECT.format(
        project_name=project_name,
        description=f"{project_name} lakehouse workflows",
        dependencies=_rendered_requirement_lines(runtime, "    "),
        dev_dependencies=_rendered_requirement_lines(dev, "    "),
    )


def _rendered_phlo_yaml(project_name: str) -> str:
    """Return the packaged phlo.yaml with the project name substituted."""
    content = (RESOURCES_DIR / "phlo.yaml").read_text(encoding="utf-8")
    return content.replace("name: retail-files", f"name: {project_name}", 1)


class RetailFilesTemplate:
    """Render the canonical Retail Files lakehouse as a consumer-owned project."""

    metadata = TemplateMetadata(
        name="retail-files",
        description="Retail Files lakehouse: file ingestion, dbt, and WAP",
        required_packages=("phlo", "phlo-dagster", "phlo-dbt", "phlo-pandera"),
        generated_paths=(
            "phlo.yaml",
            "pyproject.toml",
            "README.md",
            ".gitignore",
            "workflows/ingestion/retail/files.py",
            "workflows/transforms/dbt/",
            "scripts/generate_fixtures.py",
            "tests/test_retail_files.py",
            "data/",
            "docs/retail-files-e2e.md",
        ),
        next_steps=(
            "phlo services init --force --no-dev",
            "phlo services start --build",
            "phlo doctor",
            "uv run python scripts/generate_fixtures.py --scale default",
            "phlo materialize retail_wap_job --partition 2025-01-15",
        ),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Copy the packaged canonical project and write rendered project files."""
        contract = load_contract()
        context.project_dir.mkdir(parents=True, exist_ok=True)

        for resource in sorted(RESOURCES_DIR.rglob("*")):
            target = context.project_dir / resource.relative_to(RESOURCES_DIR)
            if resource.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif resource.name == "phlo.yaml":
                target.write_text(_rendered_phlo_yaml(context.project_name), encoding="utf-8")
            else:
                shutil.copyfile(resource, target)

        (context.project_dir / "pyproject.toml").write_text(
            _rendered_pyproject(context.project_name, contract), encoding="utf-8"
        )

        shutil.copyfile(RESOURCES_DIR / ".gitignore", context.project_dir / ".gitignore")


def templates() -> tuple[RetailFilesTemplate, ...]:
    """Entry-point callable for the `phlo.project_templates` group."""
    return (RetailFilesTemplate(),)
