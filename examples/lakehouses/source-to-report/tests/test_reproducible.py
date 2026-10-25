"""Two builds from the same pull are byte-for-byte identical.

The run record is the only thing allowed to differ: it carries the wall clock
and the warehouse path by design. Everything a reader consumes must be stable.
"""

from __future__ import annotations

import hashlib
import json

from conftest import using_data_dir


def _digest(root) -> dict[str, str]:
    files = [root / "index.html", root / "summary.json"]
    files += sorted((root / "data").glob("*.csv"))
    files += sorted((root / "charts").glob("*.svg"))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def test_two_builds_produce_identical_outputs(tmp_path, fixture_data) -> None:
    from source_to_report.runner import run

    with using_data_dir(fixture_data):
        run(db_path=tmp_path / "a.duckdb", out_dir=tmp_path / "a", quiet=True)
        run(db_path=tmp_path / "b.duckdb", out_dir=tmp_path / "b", quiet=True)

    first = _digest(tmp_path / "a")
    second = _digest(tmp_path / "b")

    assert first == second
    assert len(first) == 11  # report, summary, five tables, four charts
    assert str(tmp_path / "a") not in (tmp_path / "a" / "index.html").read_text(encoding="utf-8")


def test_only_the_run_record_differs(tmp_path, fixture_data) -> None:
    from source_to_report.runner import run

    with using_data_dir(fixture_data):
        run(db_path=tmp_path / "a.duckdb", out_dir=tmp_path / "a", quiet=True)
        run(db_path=tmp_path / "b.duckdb", out_dir=tmp_path / "b", quiet=True)

    first = json.loads((tmp_path / "a" / "provenance.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "b" / "provenance.json").read_text(encoding="utf-8"))

    assert (
        first["generated_at"] != second["generated_at"] or first["warehouse"] != second["warehouse"]
    )
    for document in (first, second):
        document.pop("generated_at")
        document.pop("warehouse")
    assert first == second
