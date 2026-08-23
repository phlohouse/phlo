"""Run the curated executable docstring examples.

These modules carry doctest examples that document pure, side-effect-free
contracts. Every example here runs in CI; a failure means either the code or
its documented contract drifted. Add a module to CURATED_MODULES only when its
examples are deterministic and free of I/O.
"""

from __future__ import annotations

import doctest
import importlib

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
