"""Check that the ADR index lists every record with its recorded status."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "docs/architecture/decisions"
INDEX = DECISIONS / "index.md"
ROW = re.compile(r"^\| \[(\d{4})\]\(([^)]+)\) \|.*\| ([^|]+) \|$", re.MULTILINE)
STATUS = re.compile(r"\b(Accepted|Proposed|Rejected|Superseded|Deprecated)\b", re.IGNORECASE)


def record_status(path: Path) -> str | None:
    text = path.read_text()
    inline = re.search(r"^- \*\*Status:\*\*\s*(.+)$", text, re.MULTILINE)
    if inline:
        match = STATUS.search(inline.group(1))
    else:
        lines = text.splitlines()
        try:
            start = next(index for index, line in enumerate(lines) if line == "## Status") + 1
        except StopIteration:
            return None
        status_lines = []
        for line in lines[start:]:
            if line.startswith("## "):
                break
            status_lines.append(line)
        match = STATUS.search(" ".join(status_lines))
    return match.group(1).title() if match else None


def main() -> int:
    rows = {
        number: (target, status.strip())
        for number, target, status in ROW.findall(INDEX.read_text())
    }
    expected_files = {path.stem[:4]: path for path in DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md")}
    failures: list[str] = []
    for number in sorted(set(rows) | set(expected_files)):
        if number not in rows:
            failures.append(f"ADR {number} is missing from the index")
            continue
        if number not in expected_files:
            failures.append(f"ADR {number} links to a missing record: {rows[number][0]}")
            continue
        target = rows[number][0]
        linked_record = (INDEX.parent / unquote(urlsplit(target).path)).resolve()
        expected_record = expected_files[number].resolve()
        if linked_record != expected_record:
            failures.append(
                f"ADR {number} links to {target}, expected {expected_files[number].name}"
            )
            continue
        actual = record_status(expected_files[number])
        indexed = STATUS.search(rows[number][1])
        if actual is None:
            failures.append(f"ADR {number} has no recognised status")
        elif indexed is None or indexed.group(1).title() != actual:
            failures.append(
                f"ADR {number} status mismatch: record={actual}, index={indexed.group(1).title() if indexed else rows[number][1]}"
            )
    if failures:
        print("ADR index mismatches:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("ADR index records and statuses match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
