"""Compare actual quality paths in two checkouts in fresh Linux processes.

Run with the repository's locked development environment. The baseline must
include #1095. Fixture generation and imports are outside the timed operation;
peak RSS includes imports and the whole operation (Linux ru_maxrss). Merge
measures real key preparation/deletion expressions with a no-I/O transaction,
not catalog, network or object-store performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pandera.typing import Series


def fixture(directory: Path, rows: int) -> str:
    """Write deterministic wide rows in ten ordered Parquet files."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    digest = hashlib.sha256()
    for part, start in enumerate(range(0, rows, 100_000)):
        sequence = np.arange(start, min(start + 100_000, rows), dtype=np.int64)
        values = (sequence % 1000).astype(float)
        values[sequence == rows - 1] = -1
        columns = {"id": sequence // 2, "value": values}
        columns.update({f"wide_{index}": sequence + index for index in range(32)})
        path = directory / f"part-{part:04d}.parquet"
        pq.write_table(pa.table(columns), path, row_group_size=65_536)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validation(paths: list[Path], optimized: bool, rows: int, batch_size: int) -> dict:
    """Check a late failure independently of the adapter's implementation."""
    from pandera.pandas import DataFrameModel, Field
    from phlo_dlt.pandera_checks import evaluate_pandera_contract_parquet_files

    class Contract(DataFrameModel):
        id: Series[int] = Field(ge=0)
        value: Series[float] = Field(ge=0)

    options = {"mode": "full", "batch_size": batch_size} if optimized else {}
    started = time.perf_counter()
    result = evaluate_pandera_contract_parquet_files(paths, schema_class=Contract, **options)
    seconds = time.perf_counter() - started
    assert not result.passed and result.total_count == rows and result.failed_count == 1
    assert result.sample[0]["index"] == rows - 1
    return {"seconds": seconds, "total": rows, "failures": 1, "failure_index": rows - 1}


def read_once(paths: list[Path], _optimized: bool, rows: int, _batch_size: int) -> dict:
    """Measure the shipped read-once path as an unchanged control."""
    import pandas as pd
    from phlo_dlt.executor import _evaluate_domain_quality_checks

    def count(frame):
        return None if len(frame) == rows else "wrong row count"

    def nulls(frame):
        return None if frame.id.isna().sum() == 0 else "unexpected null"

    def bounds(frame):
        return None if (frame.value < 0).sum() == 1 else "wrong failures"

    started = time.perf_counter()
    with patch("phlo_dlt.executor.pd.read_parquet", wraps=pd.read_parquet) as reads:
        results = _evaluate_domain_quality_checks(
            parquet_paths=paths, quality_checks=[count, nulls, bounds]
        )
        assert reads.call_count == len(paths)
    seconds = time.perf_counter() - started
    assert all(result.passed for result in results)
    return {"seconds": seconds, "total": rows, "checks": 3, "reads": len(paths)}


def aggregates(paths: list[Path], optimized: bool, rows: int, _batch_size: int) -> dict:
    """Exercise real DuckDB SQL, not a mock returning expected aggregates."""
    import duckdb
    from phlo_pandera.checks import CountCheck, NullCheck, RangeCheck, UniqueCheck
    from phlo_pandera.decorator_helpers import _load_data

    connection = duckdb.connect(config={"threads": 1, "memory_limit": "256MB"})
    connection.execute(
        f"CREATE VIEW fixture AS SELECT * FROM read_parquet('{paths[0].parent}/*.parquet')"
    )
    runtime = SimpleNamespace(
        resources={"duckdb": connection}, logger=SimpleNamespace(info=lambda *a, **k: None)
    )
    checks = [
        CountCheck(min_rows=rows),
        NullCheck(columns=["id"]),
        UniqueCheck(columns=["id"]),
        RangeCheck(column="value", min_value=0),
    ]
    started = time.perf_counter()
    if optimized:
        from phlo_pandera.decorator_helpers import QueryCheckRunner

        runner = QueryCheckRunner(runtime, "SELECT * FROM fixture", "duckdb")
        results = [runner.execute(check, runtime) for check in checks]
    else:
        frame = _load_data(runtime, "SELECT * FROM fixture", "duckdb")
        results = [check.execute(frame, runtime) for check in checks]
    seconds = time.perf_counter() - started
    assert [result.passed for result in results] == [True, True, False, False]
    assert results[2].metadata["duplicate_count"] == rows
    assert results[3].metadata["out_of_range"] == 1
    connection.close()
    return {"seconds": seconds, "total": rows, "duplicates": rows, "range_failures": 1}


def grouped(paths: list[Path], _optimized: bool, rows: int, _batch_size: int) -> dict:
    """Compare matching groups and one deliberate late mismatch."""
    import numpy as np
    import pandas as pd
    from phlo_pandera.reconciliation import AggregateSpec, MultiAggregateConsistencyCheck

    keys = np.arange(rows, dtype=np.int64) % 100_000
    values = np.ones(rows, dtype=np.int64)
    values[-1] = 2
    frame = pd.DataFrame({"group": keys, "total": values})
    context = SimpleNamespace(
        resources={
            "trino": SimpleNamespace(
                execute_query=lambda _query: [(index, 1) for index in range(100_000)]
            )
        },
        partition_key=None,
    )
    check = MultiAggregateConsistencyCheck(
        source_table="source",
        group_by=["group"],
        aggregates=[AggregateSpec(name="total", expression="SUM(value)", target_column="total")],
    )
    started = time.perf_counter()
    result = check.execute(frame, context)
    seconds = time.perf_counter() - started
    assert not result.passed and result.metric_value["mismatches"] == 1
    return {"seconds": seconds, "total": rows, "mismatches": 1}


def merge_keys(paths: list[Path], _optimized: bool, rows: int, _batch_size: int) -> dict:
    """Run actual Iceberg key conversion and expressions without provider I/O."""
    import pyarrow.parquet as pq
    from phlo_iceberg.tables import _write_arrow_table

    arrow = pq.ParquetDataset(paths).read(columns=["id"])

    class Writer:
        count = 0
        checksum = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def delete(self, expression):
            literals = [literal.value for literal in expression.literals]
            self.count += len(literals)
            self.checksum += sum(literals)

        def append(self, frame):
            assert len(frame) == rows

    writer = Writer()
    started = time.perf_counter()
    with patch("phlo_iceberg.tables.prepare_arrow_write", return_value=(writer, arrow, [])):
        deleted = _write_arrow_table(
            None,
            arrow,
            table_name="fixture",
            schema_policy="strict",
            operation="merge",
            unique_key="id",
        )
    seconds = time.perf_counter() - started
    expected = rows // 2
    assert deleted == writer.count == expected
    assert writer.checksum == expected * (expected - 1) // 2
    return {"seconds": seconds, "unique_keys": expected, "checksum": writer.checksum}


SCENARIOS = {
    "validation": validation,
    "read_once": read_once,
    "aggregates": aggregates,
    "grouped": grouped,
    "merge_keys": merge_keys,
}


def worker(args: argparse.Namespace) -> None:
    """Load selected checkout before editable-install import finders."""
    for path in [args.root / "src", *sorted((args.root / "packages").glob("*/src"))]:
        sys.path.insert(0, str(path))
    os.environ["PHLO_NO_AUTO_DISCOVER"] = "1"
    paths = sorted(args.fixture.glob("*.parquet"))
    result = SCENARIOS[args.scenario](paths, args.optimized, args.rows, args.batch_size)
    loaded = {
        name: module.__file__
        for name, module in sys.modules.items()
        if name
        in {
            "phlo_dlt.executor",
            "phlo_dlt.pandera_checks",
            "phlo_iceberg.tables",
            "phlo_pandera.reconciliation",
            "phlo_pandera.decorator_helpers",
        }
    }
    assert all(Path(path).is_relative_to(args.root) for path in loaded.values())
    result["loaded_modules"] = loaded
    result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    result["scenario"] = args.scenario
    result["root"] = str(args.root)
    print("BENCHMARK " + json.dumps(result), flush=True)


def compare(args: argparse.Namespace) -> None:
    """Run independent child processes with identical fixtures and expectations."""
    records = []
    with TemporaryDirectory(prefix="phlo-quality-benchmark-") as temporary:
        directory = Path(temporary)
        digest = fixture(directory, args.rows)
        for scenario in args.scenarios:
            for label, root in [("before", args.baseline), ("after", args.root)]:
                for repeat in range(args.repeats):
                    command = [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        "--root",
                        str(root),
                        "--fixture",
                        str(directory),
                        "--scenario",
                        scenario,
                        "--rows",
                        str(args.rows),
                        "--batch-size",
                        str(args.batch_size),
                    ]
                    if label == "after":
                        command.append("--optimized")
                    output = subprocess.run(command, capture_output=True, text=True, check=False)
                    if output.returncode:
                        raise RuntimeError(output.stdout + output.stderr)
                    result = json.loads(
                        next(
                            line[10:]
                            for line in output.stdout.splitlines()
                            if line.startswith("BENCHMARK ")
                        )
                    )
                    result.update(variant=label, repeat=repeat)
                    records.append(result)
                    print(json.dumps(result), flush=True)
    args.output.write_text(
        json.dumps(
            {
                "rows": args.rows,
                "batch_size": args.batch_size,
                "fixture_sha256": digest,
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    """Parse benchmark configuration or internal worker arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=Path("quality-benchmark.json"))
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=65_536)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--optimized", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--scenario", choices=list(SCENARIOS))
    args = parser.parse_args()
    if args.worker:
        worker(args)
    else:
        if args.baseline is None:
            parser.error("--baseline is required")
        compare(args)


if __name__ == "__main__":
    main()
