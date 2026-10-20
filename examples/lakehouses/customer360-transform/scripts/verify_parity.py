#!/usr/bin/env python3
"""Prove dbt and Phlo Transform produce identical tables from identical bytes.

The script is the example's executable proof, not a mock:

1. generate the deterministic Customer 360 fixtures;
2. load the same raw rows into two DuckDB databases;
3. build every model with the Phlo Transform engine (native workspace);
4. build every model with dbt against the untouched dbt project (dbt-duckdb);
5. run both test suites, then compare each table row-for-row and type-for-type.

Exit code 0 means the two engines agree on all ten models. Any difference is
printed with the table name, row counts, and the unmatched row counts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DBT_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "dbt"
NATIVE_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "native"
MODEL_SCHEMA = "customer360"


def _load_raw(connection, data_dir: Path) -> None:
    """Load the generated fixtures into the ``raw`` schema."""

    connection.execute("create schema raw")
    connection.execute(
        "create table raw.c360_customers as "
        f"select * from read_csv_auto('{data_dir}/commerce/base/customers.csv', header=true)"
    )
    connection.execute(
        "create table raw.c360_orders as "
        f"select * from read_csv_auto('{data_dir}/commerce/base/orders.csv', header=true)"
    )
    connection.execute(
        "create table raw.marketing_contacts as "
        f"select * from read_csv_auto('{data_dir}/marketing/contacts.csv', header=true)"
    )
    tickets = json.loads((data_dir / "support/tickets.json").read_text(encoding="utf-8"))["tickets"]
    connection.execute(
        """
        create table raw.support_tickets as
        select j->>'ticket_id' as ticket_id,
               j->>'email' as email,
               j->>'subject' as subject,
               (j->>'created_at')::timestamptz as created_at,
               (j->>'resolved_at')::timestamptz as resolved_at
        from (select unnest(?::JSON[]) as j)
        """,
        [json.dumps(tickets)],
    )
    events = json.loads((data_dir / "marketing/consent_events.json").read_text(encoding="utf-8"))[
        "events"
    ]
    connection.execute(
        """
        create table raw.consent_events as
        select j->>'event_key' as event_key,
               j->>'email' as email,
               j->>'consent_status' as consent_status,
               j->>'source' as source,
               (j->>'occurred_at')::timestamptz as occurred_at
        from (select unnest(?::JSON[]) as j)
        """,
        [json.dumps(events)],
    )


def _run_engine(database: Path) -> dict:
    """Build every native model with the Phlo Transform engine."""

    from phlo_transform.binary import require_binary
    from phlo_transform.settings import get_settings

    binary = require_binary(PROJECT_ROOT, get_settings(PROJECT_ROOT))
    command = [
        str(binary),
        "--root",
        str(NATIVE_ROOT),
        "--adapter",
        "duckdb",
        "--duckdb-path",
        str(database),
        "--json",
        "run",
    ]
    print(f"\n$ {' '.join(command)}")
    completed = subprocess.run(  # noqa: S603 - local executable resolved above
        command, capture_output=True, text=True, check=False
    )
    report = json.loads(completed.stdout) if completed.stdout.strip() else {}
    for model in report.get("models", []):
        print(f"  phlo-transform  {model.get('status', '?'):8} {model.get('model')}")
    passed = sum(1 for test in report.get("tests", []) if test.get("status") == "passed")
    print(f"  phlo-transform  tests: {passed}/{len(report.get('tests', []))} passed")
    if completed.returncode != 0 or report.get("status") != "passed":
        print(completed.stderr, file=sys.stderr)
        raise SystemExit("phlo-transform run failed")
    return report


def _write_profile(directory: Path, database: Path) -> Path:
    """Write a dbt-duckdb profile that names its tables like the native build."""

    profile = directory / "profiles.yml"
    profile.write_text(
        f"""customer360:
  target: parity
  outputs:
    parity:
      type: duckdb
      path: {database}
      schema: {MODEL_SCHEMA}
      threads: 4
""",
        encoding="utf-8",
    )
    return profile


def _dbt_executable() -> str:
    executable = shutil.which("dbt")
    if executable:
        return executable
    candidate = Path(sys.executable).with_name("dbt")
    if candidate.is_file():
        return str(candidate)
    raise SystemExit("dbt executable not found; run: uv sync --group dev")


def _run_dbt(database: Path, work_dir: Path) -> None:
    """Build and test the untouched dbt project against the same raw rows."""

    profiles_dir = work_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    _write_profile(profiles_dir, database)
    dbt = _dbt_executable()
    for subcommand in ("run", "test"):
        command = [
            dbt,
            subcommand,
            "--project-dir",
            str(DBT_ROOT),
            "--profiles-dir",
            str(profiles_dir),
            "--target",
            "parity",
        ]
        print(f"\n$ {' '.join(command)}")
        completed = subprocess.run(  # noqa: S603 - dbt resolved above
            command, capture_output=True, text=True, check=False
        )
        tail = completed.stdout.strip().splitlines()[-3:]
        for line in tail:
            print(f"  dbt  {line}")
        if completed.returncode != 0:
            print(completed.stdout, file=sys.stderr)
            print(completed.stderr, file=sys.stderr)
            raise SystemExit(f"dbt {subcommand} failed")


def _compare(native_database: Path, dbt_database: Path) -> bool:
    """Compare every model table row-for-row and column-for-column."""

    import duckdb

    connection = duckdb.connect(str(native_database))
    connection.execute(f"attach '{dbt_database}' as dbt (read_only)")
    tables = [
        row[0]
        for row in connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema = ? and table_catalog = current_database() order by 1",
            [MODEL_SCHEMA],
        ).fetchall()
    ]
    if len(tables) != 10:
        print(f"expected 10 model tables, found {len(tables)}: {tables}", file=sys.stderr)
        return False

    identical = True
    print(f"\n{'table':32} {'rows':>6} {'only native':>12} {'only dbt':>9} {'types':>6}")
    print("-" * 70)
    for table in tables:
        native_rows = connection.execute(
            f'select count(*) from {MODEL_SCHEMA}."{table}"'
        ).fetchone()[0]
        dbt_rows = connection.execute(
            f'select count(*) from dbt.{MODEL_SCHEMA}."{table}"'
        ).fetchone()[0]
        only_native = connection.execute(
            f'select count(*) from (select * from {MODEL_SCHEMA}."{table}" '
            f'except all select * from dbt.{MODEL_SCHEMA}."{table}")'
        ).fetchone()[0]
        only_dbt = connection.execute(
            f'select count(*) from (select * from dbt.{MODEL_SCHEMA}."{table}" '
            f'except all select * from {MODEL_SCHEMA}."{table}")'
        ).fetchone()[0]
        native_columns = connection.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = ? and table_name = ? and table_catalog = current_database() "
            "order by ordinal_position",
            [MODEL_SCHEMA, table],
        ).fetchall()
        dbt_columns = connection.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = ? and table_name = ? and table_catalog = 'dbt' "
            "order by ordinal_position",
            [MODEL_SCHEMA, table],
        ).fetchall()
        types_match = native_columns == dbt_columns
        row_match = native_rows == dbt_rows and only_native == 0 and only_dbt == 0 and types_match
        identical = identical and row_match
        print(
            f"{table:32} {native_rows:>6} {only_native:>12} {only_dbt:>9} "
            f"{'same' if types_match else 'DIFF':>6}  {'OK' if row_match else 'DIFF'}"
        )
    return identical


def main() -> int:
    """Run the full translation parity proof and return a process exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for generated fixtures and databases (default: temporary).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a non-empty --work-dir.",
    )
    args = parser.parse_args()

    from scripts.generate_fixtures import generate

    temporary = None
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="customer360-parity-")
        work_dir = Path(temporary.name)
    else:
        work_dir = args.work_dir
        if work_dir.exists() and any(work_dir.iterdir()):
            if not args.force:
                raise SystemExit(f"{work_dir} is not empty; pass --force to replace it")
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    print(f"Working directory: {work_dir}")

    data_dir = work_dir / "data"
    counts = generate(data_dir)
    print(f"Fixtures: {counts}")

    import duckdb

    native_database = work_dir / "native.duckdb"
    dbt_database = work_dir / "dbt.duckdb"
    connection = duckdb.connect(str(native_database))
    _load_raw(connection, data_dir)
    connection.close()
    shutil.copyfile(native_database, dbt_database)

    engine_report = _run_engine(native_database)
    _run_dbt(dbt_database, work_dir)
    identical = _compare(native_database, dbt_database)

    engine_tests = len(engine_report.get("tests", []))
    if engine_tests != 15:
        print(f"expected 15 engine tests, found {engine_tests}", file=sys.stderr)
        identical = False

    print()
    if identical:
        print("PARITY PASS: 10/10 models identical row-for-row; 15/15 engine tests passed.")
        return 0
    print("PARITY FAIL: engines disagree; inspect the tables above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
