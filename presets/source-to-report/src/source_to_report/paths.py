"""Where the preset reads its inputs and writes its outputs.

The local runner works from a project checkout. ``uv sync`` installs this
package in editable mode, so the project directory is the preset directory.
Set ``SOURCE_TO_REPORT_HOME`` when running an installed copy against another
checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def project_dir() -> Path:
    """Return the preset's project root."""
    override = os.environ.get("SOURCE_TO_REPORT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    # src/source_to_report/paths.py -> src/source_to_report -> src -> project
    return PACKAGE_DIR.parents[1]


def data_dir() -> Path:
    """Return the directory holding the generated pull and the published notes."""
    return (
        Path(os.environ.get("SOURCE_TO_REPORT_DATA", project_dir() / "data")).expanduser().resolve()
    )


def dbt_dir() -> Path:
    """Return the dbt project directory."""
    override = os.environ.get("SOURCE_TO_REPORT_DBT")
    return (
        Path(override).expanduser().resolve()
        if override
        else project_dir() / "workflows" / "transforms" / "dbt"
    )


def local_dbt_profiles_dir() -> Path:
    """Return the dbt profile directory the local runner owns.

    Kept outside ``workflows/transforms/dbt/profiles``: Phlo generates its
    Trino profile there on the full stack, and neither file may clobber the
    other.
    """
    override = os.environ.get("SOURCE_TO_REPORT_DBT_PROFILES")
    return (
        Path(override).expanduser().resolve()
        if override
        else project_dir() / "config" / "dbt-profiles"
    )


def build_dir() -> Path:
    """Return the directory for the local DuckDB file and dbt scratch state."""
    return (
        Path(os.environ.get("SOURCE_TO_REPORT_BUILD", project_dir() / "build"))
        .expanduser()
        .resolve()
    )


def default_reports_dir() -> Path:
    """Return the default output directory for tables, charts and the report."""
    return (
        Path(os.environ.get("SOURCE_TO_REPORT_REPORTS", project_dir() / "reports"))
        .expanduser()
        .resolve()
    )
