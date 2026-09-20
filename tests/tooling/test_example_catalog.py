"""Check that the machine-readable example catalogue covers every lakehouse."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAKEHOUSES = ROOT / "examples/lakehouses"


def test_example_catalog_matches_example_directories() -> None:
    catalog = json.loads((LAKEHOUSES / "catalog.json").read_text(encoding="utf-8"))
    entries = catalog["examples"]
    names = [entry["name"] for entry in entries]
    directories = sorted(path.name for path in LAKEHOUSES.iterdir() if path.is_dir())

    assert catalog["schema_version"] == 1
    assert names == sorted(names)
    assert names == directories
    assert len(names) == len(set(names))

    for entry in entries:
        assert entry["title"].strip()
        assert entry["focus"]
        assert (LAKEHOUSES / entry["name"] / "README.md").is_file()
