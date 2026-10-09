"""Run the curated executable docstring examples.

These modules carry doctest examples that document pure, side-effect-free
contracts. Every example here runs in CI; a failure means either the code or
its documented contract drifted. Add a module to CURATED_MODULES only when its
examples are deterministic and free of I/O.
"""

from __future__ import annotations

import doctest
import importlib
import re
from pathlib import Path

CURATED_MODULES = (
    "phlo._attempt",
    "phlo_trino.type_mapping",
)


def test_curated_docstring_examples() -> None:
    """Every curated module's doctest examples pass."""
    failures = []
    for module_name in CURATED_MODULES:
        result = doctest.testmod(importlib.import_module(module_name), verbose=False)
        if result.failed:
            failures.append(f"{module_name}: {result.failed} failed of {result.attempted}")
        assert result.attempted > 0, f"{module_name}: no doctest examples found"
    assert not failures, "doctest failures: " + "; ".join(failures)


def test_schedule_replacement_example_creates_a_provider_schedule() -> None:
    """The documented replacement must register a real cron, not a dormant spec."""
    from phlo_dlt.decorator import clear_ingestion_assets

    import phlo

    document = Path(__file__).resolve().parents[1] / "docs/reference/python-api.md"
    section = document.read_text(encoding="utf-8").split("### `schedule`", 1)[1]
    example = re.search(r"```python\n(.*?)```", section, flags=re.DOTALL)
    assert example is not None
    clear_ingestion_assets()
    try:
        namespace = {}
        exec(compile(example.group(1), str(document), "exec"), namespace)
        assets = phlo.ingest.assets("dlt")
        assert len(assets) == 1
        assert assets[0].run.cron == "0 2 * * *"
        assert assets[0].key == "dlt_daily_sales"
        assert list(namespace["daily_sales_source"]()) == [{"date": "2025-01-01", "sales": 42}]
    finally:
        clear_ingestion_assets()
