"""Test-module import path for API-local helpers.

Also forces every test onto the non-durable memory observatory settings store
and resets it around each test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TEST_DIR = str(Path(__file__).parent)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)


@pytest.fixture(autouse=True)
def use_memory_observatory_settings_store(monkeypatch) -> None:
    """API unit tests explicitly use the non-durable development backend."""
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    from phlo.plugins.observatory_settings import _reset_memory_service

    _reset_memory_service()
