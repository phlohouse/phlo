"""Read the monitoring pack's source files.

These are the only places the preset reads the pull. The ingestion assets call
them; nothing else does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def read_readings(data_dir: Path) -> list[dict[str, Any]]:
    """Return every hourly reading from the generated pull."""
    path = data_dir / "raw" / "readings.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no readings at {path}; run `uv run python scripts/generate_fixtures.py` first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def read_notes(data_dir: Path) -> list[dict[str, Any]]:
    """Return the published notes, newest month last."""
    notes = []
    for path in sorted((data_dir / "notes").glob("*.md")):
        body = path.read_text(encoding="utf-8")
        first_line = body.splitlines()[0] if body else ""
        notes.append(
            {
                "path": f"notes/{path.name}",
                "month": path.stem,
                "title": first_line.removeprefix("# ").strip(),
                "body": body,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not notes:
        raise FileNotFoundError(f"no published notes in {data_dir / 'notes'}")
    return notes
