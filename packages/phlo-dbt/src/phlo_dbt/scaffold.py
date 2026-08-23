"""Project scaffold helpers for dbt assets.

This module provides utilities for scaffolding new dbt projects with
Phlo-compatible configuration. It generates dbt_project.yml files with
appropriate settings and SQLFluff configuration for linting.

Example:
    >>> from phlo_dbt.scaffold import write_dbt_scaffold
    >>> from pathlib import Path
    >>> write_dbt_scaffold(
    ...     project_name="analytics",
    ...     transforms_dir=Path("workflows/transforms/dbt"),
    ...     project_dir=Path(".")
    ... )
    # Creates dbt_project.yml and .sqlfluff configuration files

"""

from __future__ import annotations

from pathlib import Path


def build_dbt_project(project_name: str) -> str:
    """Render dbt_project.yml content with Phlo-compatible defaults.

    Hyphens in the project name become underscores for dbt configuration.

    Example:
        >>> content = build_dbt_project("my-analytics")
        >>> print(content)
        name: my_analytics
        version: 1.0.0
        ...

        >>> # Write to file
        >>> Path("dbt_project.yml").write_text(content)

    """
    safe_name = project_name.replace("-", "_")
    return f"""name: {safe_name}
version: 1.0.0
config-version: 2

profile: phlo

model-paths: ["models"]
seed-paths: ["seeds"]

# Opt into new SSL behavior to suppress trino-dbt SSL warning
flags:
  require_certificate_validation: true

models:
  {safe_name}:
    +materialized: table
"""


def build_sqlfluff_config() -> str:
    """Build SQLFluff config tuned for Trino + dbt Jinja templating.

    Example:
        >>> config = build_sqlfluff_config()
        >>> Path(".sqlfluff").write_text(config)
        >>> # Now SQLFluff will properly lint your dbt SQL files

    """
    return """[sqlfluff]
dialect = trino
templater = jinja
max_line_length = 120
# Only exclude keywords-as-identifiers rule (requires column renames)
exclude_rules = RF04

[sqlfluff:templater:jinja]
# Ignore undefined jinja variables in dbt
ignore = templating

[sqlfluff:rules]
# Allow trailing commas
allow_trailing_commas = True

[sqlfluff:rules:layout.long_lines]
# Increase line length limit
max_line_length = 120

[sqlfluff:rules:layout.indent]
# Use 4 spaces for indentation
indent_unit = space
tab_space_size = 4

[sqlfluff:rules:capitalisation.keywords]
# SQL keywords should be lowercase
capitalisation_policy = lower

[sqlfluff:rules:aliasing.table]
# Table aliases are required
aliasing = explicit
"""


def write_dbt_scaffold(project_name: str, transforms_dir: Path, project_dir: Path) -> None:
    """Write dbt_project.yml into ``transforms_dir`` and .sqlfluff into ``project_dir``.

    Creates the transforms directory when missing; OSError propagates when
    directory creation or file writing fails.

    Example:
        >>> from pathlib import Path
        >>> write_dbt_scaffold(
        ...     "analytics_project",
        ...     transforms_dir=Path("workflows/transforms/dbt"),
        ...     project_dir=Path(".")
        ... )
        # Creates:
        #   workflows/transforms/dbt/dbt_project.yml
        #   .sqlfluff

    """
    transforms_dir.mkdir(parents=True, exist_ok=True)

    dbt_project_content = build_dbt_project(project_name)
    (transforms_dir / "dbt_project.yml").write_text(dbt_project_content)

    sqlfluff_content = build_sqlfluff_config()
    (project_dir / ".sqlfluff").write_text(sqlfluff_content)
