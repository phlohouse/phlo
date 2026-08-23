"""Import boundary tests for phlo-api optional integrations.

Verifies that importing the phlo API entrypoint does not eagerly load the
phlo-observatory package, keeping optional integrations lazy.
"""

from __future__ import annotations

import importlib
import sys


def test_phlo_api_main_import_does_not_load_phlo_observatory() -> None:
    """phlo-api should load without importing the Observatory package."""
    for name in list(sys.modules):
        if name.startswith("phlo_observatory"):
            sys.modules.pop(name, None)
    # Keep the already-loaded application module intact. Removing it from
    # sys.modules creates a second `phlo_api.main` module while FastAPI route
    # handlers still reference the first one, which makes later monkeypatches
    # target the wrong module object.
    importlib.import_module("phlo_api.main")

    assert not any(name.startswith("phlo_observatory") for name in sys.modules)
