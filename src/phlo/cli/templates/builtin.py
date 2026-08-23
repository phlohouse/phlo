"""Built-in project template: scaffolds a minimal starter Phlo project.

MinimalTemplate is the base every other template composes. It writes
phlo.yaml, pyproject.toml, a README, and a .env.example that lists
secret variable names discovered from installed services — names only,
never values.
"""

from __future__ import annotations

from pathlib import Path

from phlo.cli.templates.models import TemplateMetadata, TemplateRenderContext


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _build_env_example_content() -> str:
    """Build `.env.example`: secret variable names only, never values.

    The file documents which secrets each discovered service expects; real
    values belong in the uncommitted `.phlo/.env.local`.
    """
    from phlo.plugins.discovery import ServiceDiscovery

    lines = [
        "# Phlo Local Secrets Template",
        "# Copy to .phlo/.env.local after running `phlo services init`.",
        "",
    ]

    discovery = ServiceDiscovery()
    services = discovery.discover()
    if not services:
        lines.append(
            "# No service plugins discovered; install service packages to populate secrets."
        )
        return "\n".join(lines) + "\n"

    for service in sorted(services.values(), key=lambda item: item.name):
        secrets = {key: cfg for key, cfg in service.env_vars.items() if cfg.get("secret") is True}
        if not secrets:
            continue
        lines.append(f"# {service.name}")
        for key in sorted(secrets.keys()):
            desc = secrets[key].get("description")
            if desc:
                lines.append(f"# {desc}")
            lines.append(f"{key}=")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _pyproject_toml(project_name: str, required_packages: tuple[str, ...]) -> str:
    dependencies = "\n".join(f'    "{package}",' for package in required_packages)
    return f"""[project]
name = "{project_name}"
version = "0.1.0"
description = "Phlo data workflows"
requires-python = ">=3.11"
dependencies = [
{dependencies}
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I"]

[tool.setuptools.packages.find]
include = ["workflows*"]
exclude = ["contracts*", "data*", "plugins*", "tests*"]
"""


def _write_pyproject_toml(
    project_dir: Path, project_name: str, required_packages: tuple[str, ...]
) -> None:
    _write_text(project_dir / "pyproject.toml", _pyproject_toml(project_name, required_packages))


def _write_project_readme(
    project_dir: Path,
    project_name: str,
    *,
    template_commands: tuple[str, ...] = (),
) -> None:
    command_lines = [
        "- `phlo services init` - Generate local runtime files under `.phlo/`",
        "- `phlo services start` - Start the local stack",
        "- `phlo services status` - Check generated service state",
        "- `phlo doctor` - Diagnose setup and service readiness",
        "- `phlo test` - Run project tests",
    ]
    command_lines.extend(f"- `{command}`" for command in template_commands)
    rendered_commands = "\n".join(command_lines)
    template_section = ""
    if template_commands:
        rendered_template_commands = "\n".join(f"   {command}" for command in template_commands)
        template_section = f"""
5. **Run the starter workflow:**
   ```bash
{rendered_template_commands}
   ```

   For daily partitioned assets, use a completed partition date rather than today's date.
"""

    _write_text(
        project_dir / "README.md",
        f"""# {project_name}

Phlo data workflows for {project_name}.

## Getting Started

1. **Install project dependencies:**
   ```bash
   uv pip install -e .
   ```

2. **Generate local runtime state:**
   ```bash
   phlo services init
   ```

   This creates `.phlo/docker-compose.yml`, `.phlo/.env`, and `.phlo/.env.local`.
   Keep `.phlo/` out of source control; it is generated runtime state.

3. **Start and inspect the local stack:**
   ```bash
   phlo services start
   phlo services status
   ```

4. **Verify setup health:**
   ```bash
   phlo doctor
   ```

   Dagster is available at http://localhost:10006 when the default stack is running.
{template_section}
## Project Structure

```
{project_name}/
├── data/              # Local source data or examples
├── workflows/         # Workflow definitions
│   ├── ingestion/     # Data ingestion workflows
│   ├── schemas/       # Pandera validation schemas
│   └── transforms/dbt/ # dbt transformation models when enabled
├── plugins/           # Project-local plugin modules
├── contracts/         # Contract snapshots and migration inputs
└── tests/             # Workflow tests
```

## Commands

{rendered_commands}

## Documentation

- [Phlo Documentation](https://github.com/iamgp/phlo)
- [Workflow Development Guide](https://github.com/iamgp/phlo/blob/main/docs/guides/workflow-development.md)
""",
    )


def _write_common_project_files(
    project_dir: Path, project_name: str, required_packages: tuple[str, ...]
) -> None:
    _write_text(
        project_dir / ".env.example",
        _build_env_example_content(),
    )
    for local_dir in ("contracts", "data", "plugins"):
        (project_dir / local_dir).mkdir(exist_ok=True)
        _write_text(project_dir / local_dir / ".gitkeep", "")
    _write_pyproject_toml(project_dir, project_name, required_packages)
    _write_text(
        project_dir / ".gitignore",
        """.env
.env.local
.phlo/
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
*.egg-info/
dist/
build/
.pytest_cache/
.coverage
htmlcov/
.ruff_cache/
""",
    )
    _write_project_readme(project_dir, project_name)
    _write_text(
        project_dir / "AGENTS.md",
        """# Agent Instructions

Prefer Phlo MCP tools over shell commands when available:

- Inspect assets with `runtime_assets`, `runtime_asset`, `inspect_materialization`, and `get_lineage`.
- Diagnose failures with `phlo.debug_run`, `get_run_logs`, `get_run_trace_spans`, and `render_run_trace_tree`.
- Validate authoring changes with `validate_workflow`, `validate_schema`, `lint_project`, and `run_doctor`.
- For mutations, start with dry runs: `materialize_asset(dry_run=true)` and `backfill_asset(dry_run=true)`.

Use `lakehouse:read` for inspection, `lakehouse:operate` for materialize/retry/cancel/backfill, and `project:write` for scaffold or validation tools that touch project files.
""",
    )

    from phlo.cli.commands.services.utils import PHLO_CONFIG_TEMPLATE

    _write_text(
        project_dir / "phlo.yaml",
        PHLO_CONFIG_TEMPLATE.format(
            name=project_name,
            description=f"{project_name} data workflows",
        ),
    )


class MinimalTemplate:
    """Project template generating the smallest runnable Phlo project skeleton."""

    metadata = TemplateMetadata(
        name="minimal",
        description="Empty Phlo project",
        required_packages=("phlo",),
        generated_paths=(
            "phlo.yaml",
            "pyproject.toml",
            ".env.example",
            ".gitignore",
            "README.md",
            "workflows/__init__.py",
            "tests/__init__.py",
        ),
        next_steps=("phlo services init", "phlo workflow create"),
    )

    def render(self, context: TemplateRenderContext) -> None:
        """Write the workflow/test package skeletons and common project files."""
        context.project_dir.mkdir(parents=True, exist_ok=True)
        workflows_dir = context.project_dir / "workflows"
        _write_text(workflows_dir / "__init__.py", '"""User workflows."""\n')
        _write_text(workflows_dir / "ingestion" / "__init__.py", '"""Ingestion workflows."""\n')
        _write_text(
            workflows_dir / "schemas" / "__init__.py",
            '"""Pandera validation schemas."""\n',
        )
        _write_text(context.project_dir / "tests" / "__init__.py", "")
        _write_common_project_files(
            context.project_dir, context.project_name, self.metadata.required_packages
        )
