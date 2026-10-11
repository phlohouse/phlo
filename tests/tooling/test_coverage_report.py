"""Regression checks for the per-source line and branch coverage gate."""

import runpy
from pathlib import Path

module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/coverage_report.py"))
check, totals = module["check"], module["totals"]


def test_each_metric_and_new_source_requires_a_floor(tmp_path):
    floors = {"core": {"line": 81.3, "branch": 67.2}}
    assert not check(floors, floors)
    assert check({"core": {"line": 81.29, "branch": 67.2}}, floors)
    assert check({"core": {"line": 81.3, "branch": 67.19}}, floors)
    assert check({**floors, "new-package": {"line": 100, "branch": 100}}, floors)

    (tmp_path / "packages/example/src").mkdir(parents=True)
    summary = {"num_statements": 10, "covered_lines": 7, "num_branches": 4, "covered_branches": 1}
    report = {
        "files": {
            f"{path}/a.py": {"summary": summary}
            for path in ("src/phlo", "scripts", "packages/example/src")
        }
    }
    assert totals(report, tmp_path) == {
        name: {"line": 70, "branch": 25} for name in ("core", "scripts", "example")
    }
