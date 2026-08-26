"""Contract checks for the support artifact bundled into distributable wheels.

Verifies that built wheels ship the bundled support status manifest.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from shutil import which

import pytest


@pytest.mark.integration
def test_wheel_includes_the_bundled_support_manifest(tmp_path: Path) -> None:
    """The support command must not depend on the repository registry at runtime."""
    import subprocess

    subprocess.run(
        [which("uv") or "uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("phlo-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "phlo/support_data/v1.json" in archive.namelist()
