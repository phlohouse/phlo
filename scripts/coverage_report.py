"""Report measured line/branch coverage and enforce independently measured floors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def totals(report: dict, root: Path) -> dict[str, dict[str, float]]:
    """Include every source tree, including trees with no executed statements."""
    groups = {"core": "src/phlo/", "scripts": "scripts/"}
    groups.update(
        {
            path.parent.name: f"{path.relative_to(root).as_posix()}/"
            for path in root.glob("packages/*/src")
        }
    )
    result = {}
    for name, prefix in groups.items():
        summaries = [
            data["summary"]
            for filename, data in report["files"].items()
            if filename.startswith(prefix)
        ]
        if not summaries:
            raise ValueError(f"No coverage data for {name}")
        lines = sum(s["num_statements"] for s in summaries)
        branches = sum(s["num_branches"] for s in summaries)
        result[name] = {
            "line": 100 * sum(s["covered_lines"] for s in summaries) / lines if lines else 100,
            "branch": 100 * sum(s["covered_branches"] for s in summaries) / branches
            if branches
            else 100,
        }
    return result


# A source tree with no committed floor must still meet this minimum, so a new
# package can land without editing the floors file but cannot land untested.
DEFAULT_FLOOR = {"line": 70.0, "branch": 50.0}


def check(measured: dict, floors: dict) -> list[str]:
    """Reject stale floors and any percentage below its committed or default floor."""
    errors = []
    for name in measured.keys() | floors.keys():
        if name not in measured:
            errors.append(f"{name}: floor has no measurement; remove it with its source tree")
            continue
        floor = floors.get(name, DEFAULT_FLOOR)
        for metric in ("line", "branch"):
            if measured[name][metric] + 1e-9 < floor[metric]:
                errors.append(
                    f"{name} {metric}: {measured[name][metric]:.2f}% < {floor[metric]:.2f}%"
                )
    return errors


def main() -> int:
    """Write reviewable totals before failing a regression."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--floors", type=Path, default=Path("tests/coverage-floors.json"))
    parser.add_argument("--output", type=Path, default=Path("test-results/coverage-totals.json"))
    args = parser.parse_args()
    measured = totals(json.loads(args.report.read_text()), Path())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
    print("| Source | Line % | Branch % |\n|---|---:|---:|")
    for name, values in sorted(measured.items()):
        print(f"| {name} | {values['line']:.2f} | {values['branch']:.2f} |")
    errors = check(measured, json.loads(args.floors.read_text()))
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
