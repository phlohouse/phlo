"""Record what a build was made from.

Two people can rebuild the same report only if they can see the exact inputs,
code and tool versions. This writes that record next to the output. The
report's own numbers come from the run's tables, so the provenance is the
only place a wall-clock timestamp appears.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from source_to_report import paths

PACKAGES = (
    "phlo",
    "phlo-dlt",
    "phlo-dbt",
    "phlo-pandera",
    "dbt-duckdb",
    "duckdb",
    "pandas",
    "jinja2",
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _git(*arguments: str) -> str | None:
    """Run a git command in the preset, returning None when git is unavailable."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=paths.project_dir(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.project_dir()))
    except ValueError:
        return str(path.resolve())


def document(
    *,
    inputs: list[tuple[str, Path]],
    tables: dict[str, int],
    as_of: str,
    db_path: Path,
) -> dict[str, Any]:
    """Build the provenance document for one run."""
    status = _git("status", "--porcelain")
    return {
        "as_of": as_of,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status) if status is not None else None,
        },
        "packages": _versions(),
        "warehouse": str(db_path),
        "tables": tables,
        "inputs": [
            {
                "label": label,
                "path": _relative(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for label, path in inputs
        ],
    }


def write(out_dir: Path, record: dict[str, Any]) -> Path:
    """Write ``provenance.json`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "provenance.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
