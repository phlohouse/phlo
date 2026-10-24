"""Shared fixtures for the preset's tests.

The full build runs once per session; the reproducibility test builds twice
more on purpose. Fixtures are generated into a temporary directory so tests
never depend on, or overwrite, a developer's checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def generate_fixtures(data_dir: Path, *, seed: int | None = None) -> None:
    """Write the deterministic source pull into ``data_dir``."""
    command = [
        sys.executable,
        str(ROOT / "scripts" / "generate_fixtures.py"),
        "--data-dir",
        str(data_dir),
    ]
    if seed is not None:
        command.extend(["--seed", str(seed)])
    subprocess.run(command, check=True, capture_output=True, text=True, cwd=ROOT)


@contextmanager
def using_data_dir(data_dir: Path):
    """Point the preset at a data directory for one build."""
    previous = os.environ.get("SOURCE_TO_REPORT_DATA")
    os.environ["SOURCE_TO_REPORT_DATA"] = str(data_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SOURCE_TO_REPORT_DATA", None)
        else:
            os.environ["SOURCE_TO_REPORT_DATA"] = previous


@pytest.fixture(scope="session")
def fixture_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A generated pull and its published notes, shared by the session."""
    data = tmp_path_factory.mktemp("fixtures")
    generate_fixtures(data)
    return data


@pytest.fixture(scope="session")
def lakehouse(tmp_path_factory: pytest.TempPathFactory, fixture_data: Path) -> SimpleNamespace:
    """Build the lakehouse once and return its summary and output directory."""
    from source_to_report.runner import run

    work = tmp_path_factory.mktemp("lakehouse")
    with using_data_dir(fixture_data):
        summary = run(
            db_path=work / "warehouse.duckdb",
            out_dir=work / "report",
            quiet=True,
        )
    return SimpleNamespace(summary=summary, out=work / "report", data=fixture_data)
