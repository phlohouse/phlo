"""Import boundary checks for phlo-openmetadata optional peers.

Reloads package modules to prove they import cleanly without the optional peer
packages installed.
"""

from __future__ import annotations

import importlib
import sys


def test_openmetadata_modules_import_without_peer_packages(monkeypatch) -> None:
    """OpenMetadata generic modules should not import peer packages at import time."""
    peer_prefixes = ("phlo_nessie", "phlo_trino", "phlo_lineage")
    target_modules = (
        "phlo_openmetadata.cli_openmetadata",
        "phlo_openmetadata.settings",
        "phlo_openmetadata.lineage",
    )

    for name in list(sys.modules):
        if name.startswith(peer_prefixes) or name in target_modules:
            monkeypatch.delitem(sys.modules, name, raising=False)

    for module_name in target_modules:
        importlib.import_module(module_name)

    assert not any(name.startswith(peer_prefixes) for name in sys.modules)
