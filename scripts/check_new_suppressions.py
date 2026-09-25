#!/usr/bin/env python3
"""Inventory newly added suppressions and require a nearby reason comment."""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import tokenize
from pathlib import Path

SUPPRESSION = re.compile(
    r"#\s*(?:noqa\b|type:\s*ignore\b|ty:\s*ignore\b|nosec\b|pragma:\s*no cover\b|fmt:\s*skip\b)"
    r"|\bpytest\.skip\s*\(|@pytest\.mark\.skip\b|eslint-disable"
)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
REASON = re.compile(r"(?:#|//|/\*|\*)\s*reason:\s*\S", re.IGNORECASE)


def added_suppressions(diff: str, root: Path) -> list[str]:
    """Report additions with no reason on the same or immediately preceding line."""
    inventory: list[str] = []
    violations: list[str] = []
    path = ""
    lines: list[str] = []
    comments: set[int] = set()
    line_no = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            file = root / path
            lines = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
            comments = set()
            if path.endswith(".py"):
                with contextlib.suppress(tokenize.TokenError):
                    comments = {
                        token.start[0]
                        for token in tokenize.generate_tokens(
                            io.StringIO("\n".join(lines)).readline
                        )
                        if token.type == tokenize.COMMENT
                    }
        elif match := HUNK.match(line):
            line_no = int(match[1])
        elif line.startswith("+") and not line.startswith("+++"):
            is_comment = not path.endswith(".py") or line_no in comments
            if (is_comment and SUPPRESSION.search(line[1:])) or re.search(
                r"\bpytest\.skip\s*\(|@pytest\.mark\.skip\b", line[1:]
            ):
                label = f"{path}:{line_no}: {line[1:].strip()}"
                inventory.append(label)
                previous = lines[line_no - 2] if 1 < line_no <= len(lines) else ""
                if not REASON.search(line[1:]) and not REASON.search(previous):
                    violations.append(label)
            line_no += 1
        elif line.startswith(" "):
            line_no += 1
    for entry in inventory:
        print(entry)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    diff = subprocess.check_output(
        ["git", "diff", "--unified=0", "--no-ext-diff", args.base, args.head, "--"],
        text=True,
    )
    violations = added_suppressions(diff, Path.cwd())
    if violations:
        print(f"{len(violations)} new suppression(s) lack an adjacent reason comment")
        return 1
    print("New suppression inventory checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
