"""Makes sibling test helper modules importable.

Inserts the tests directory at the front of ``sys.path`` before collection so
local fixture modules resolve without installation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TEST_DIR = str(Path(__file__).parent)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)


@pytest.fixture
def synthetic_wap_context(monkeypatch):
    """Routing context for existing isolated CLI, telemetry and lifecycle fakes.

    Real environment/backend/origin enforcement is exercised without this
    fixture in test_wap_environment.py.
    """
    from phlo_dagster import wap_context, wap_launch, wap_sensors

    context = wap_context.WapContext("test", "main", "phlo_dagster", "test-backend")
    monkeypatch.setattr(
        wap_context,
        "select_wap_target",
        lambda *a: (
            "test",
            SimpleNamespace(dagster_location=context.location, nessie_ref=context.target_ref),
        ),
    )
    monkeypatch.setattr(wap_context, "resolve_wap_context", lambda *a, **k: context)
    monkeypatch.setattr(wap_launch, "resolve_wap_context", lambda *a, **k: context)
    monkeypatch.setattr(wap_context, "run_location", lambda run: context.location)
    monkeypatch.setattr(
        wap_context,
        "resolve_capability",
        lambda *a, **k: SimpleNamespace(provider=wap_sensors._load_wap_catalog()),
    )
    return context
