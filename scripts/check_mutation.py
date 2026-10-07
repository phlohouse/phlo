#!/usr/bin/env python3
"""Gate selected mutmut results; its successful run exit code alone is not a verdict."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def verdict(output: str, patterns: list[str], exceptions: list[dict]) -> list[str]:
    """Reject absent selections and every unproved mutant, except exact reviewed diffs."""
    allowed = {record["mutant"]: record for record in exceptions}
    if len(allowed) != len(exceptions) or any(not record["reason"] for record in exceptions):
        raise ValueError("Mutation exceptions need unique names and explicit reasons")
    failures = []
    matched = dict.fromkeys(patterns, False)
    for line in output.splitlines():
        name, separator, status = line.strip().partition(": ")
        selected = [pattern for pattern in patterns if fnmatch.fnmatchcase(name, pattern)]
        if not separator or not selected:
            continue
        for pattern in selected:
            matched[pattern] = True
        if status == "killed":
            continue
        if status == "survived" and name in allowed:
            shown = subprocess.check_output(
                [sys.executable, "-m", "mutmut", "show", name], text=True
            )
            diff = shown.split("\n", 1)[1].strip()
            if hashlib.sha256(diff.encode()).hexdigest() == allowed[name]["diff_sha256"]:
                print(f"Reviewed non-contract mutation: {name}: {allowed[name]['reason']}")
                continue
        failures.append(f"{name}: {status}")
    failures.extend(
        f"No mutant results for {pattern}" for pattern, seen in matched.items() if not seen
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="+")
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--results-only", action="store_true")
    args = parser.parse_args()
    if not args.results_only:
        subprocess.run(
            [sys.executable, "-m", "mutmut", "run", *args.patterns, "--max-children", "2"],
            check=True,
        )
    output = subprocess.check_output(
        [sys.executable, "-m", "mutmut", "results", "--all", "true"], text=True
    )
    exceptions = json.loads(args.exceptions.read_text()) if args.exceptions else []
    failures = verdict(output, args.patterns, exceptions)
    for failure in failures:
        print(failure, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
