#!/usr/bin/env python3
"""Require a timeout on each executable GitHub Actions job."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def missing_timeouts(text: str) -> list[str]:
    """Find runnable jobs without a timeout; reusable workflow calls cannot set one."""
    jobs = re.search(r"(?m)^jobs:[ \t]*\n", text)
    if jobs is None:
        return []
    blocks = re.split(r"(?=^  [\w-]+:[ \t]*$)", text[jobs.end() :], flags=re.MULTILINE)
    return [
        block.split(":", 1)[0].strip()
        for block in blocks
        if block.startswith("  ")
        and not re.search(r"^    (?:timeout-minutes|uses):", block, re.MULTILINE)
    ]


def main() -> int:
    failures = [
        f"{path.name}: {job}"
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job in missing_timeouts(path.read_text(encoding="utf-8"))
    ]
    if failures:
        print("Jobs without timeouts:\n" + "\n".join(failures))
        return 1
    print("All executable workflow jobs declare a timeout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
