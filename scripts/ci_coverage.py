"""Combine complete workspace shards and required PostgreSQL branch coverage.

Every expected producer must provide successful test results and nonempty branch
data before the combined report can be passed to the existing source-floor gate.
"""

from __future__ import annotations

import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException


def _validate_results(path: Path, suite_name: str, *, required: bool) -> None:
    suites = ET.parse(path).getroot().findall("testsuite")
    if len(suites) != 1 or suites[0].get("name") != suite_name:
        raise ValueError(f"{path}: expected test suite {suite_name}")
    suite = suites[0]
    cases = suite.findall("testcase")
    failures = int(suite.get("failures", "0")) + int(suite.get("errors", "0"))
    if failures or any(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    ):
        raise ValueError(f"{path}: test failures or collection errors")
    skipped = max(
        int(suite.get("skipped", "0")),
        sum(case.find("skipped") is not None for case in cases),
    )
    if not cases or len(cases) <= skipped:
        raise ValueError(f"{path}: no executed tests")
    if required and skipped:
        raise ValueError(f"{path}: required PostgreSQL tests were skipped or xfailed")


def _validate_data(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"{path}: missing coverage data")
    data = CoverageData(basename=str(path))
    try:
        data.read()
        if not data.has_arcs() or not data.measured_files():
            raise ValueError(f"{path}: expected nonempty branch coverage data")
    finally:
        # A failed read of a corrupt file leaves its SQLite connection open.
        data.close(force=True)


def combine(inputs: Path, output: Path, shards: int) -> Path:
    """Write combined data, JSON and JUnit reports only after every producer passes."""
    if shards < 1:
        raise ValueError("Coverage shard count must be positive")
    expected = {f"workspace-{index}": f"workspace-{index}-of-{shards}" for index in range(shards)}
    expected["postgres"] = "postgres"
    actual = {path.name for path in inputs.iterdir() if path.is_dir()}
    if actual != expected.keys():
        raise ValueError(
            f"Coverage producers differ: missing={sorted(expected.keys() - actual)}, "
            f"unexpected={sorted(actual - expected.keys())}"
        )
    for producer, suite_name in expected.items():
        directory = inputs / producer
        _validate_results(directory / "results.xml", suite_name, required=producer == "postgres")
        _validate_data(directory / "coverage.data")

    output.mkdir(parents=True, exist_ok=True)
    coverage = Coverage(data_file=str(output / ".coverage"))
    coverage.erase()
    coverage.combine(
        data_paths=[str(inputs / producer / "coverage.data") for producer in expected],
        strict=True,
        keep=True,
    )
    coverage.save()
    report = output / "coverage.json"
    coverage.json_report(outfile=str(report))
    for producer in expected:
        shutil.copyfile(inputs / producer / "results.xml", output / f"{producer}.xml")
    return report


def main() -> int:
    """Reject incomplete coverage before producing a report for the floor gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path("test-results/coverage-inputs"))
    parser.add_argument("--output", type=Path, default=Path("test-results/coverage"))
    parser.add_argument("--shards", type=int, default=4)
    args = parser.parse_args()
    try:
        combine(args.inputs, args.output, args.shards)
    except (OSError, ValueError, ET.ParseError, CoverageException) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
