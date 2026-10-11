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


def check(measured: dict, floors: dict) -> list[str]:
    """Reject missing measurements and any percentage below its committed floor."""
    errors = []
    for name in measured.keys() | floors.keys():
        if name not in measured or name not in floors:
            errors.append(f"{name}: measurement/floor missing")
            continue
        for metric in ("line", "branch"):
            if measured[name][metric] + 1e-9 < floors[name][metric]:
                errors.append(
                    f"{name} {metric}: {measured[name][metric]:.2f}% < {floors[name][metric]:.2f}%"
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
