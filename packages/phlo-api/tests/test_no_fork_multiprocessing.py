"""Guard: phlo-api must not use the fork multiprocessing context.

Forking a child from the multi-threaded ASGI server can deadlock the child
process when another thread holds a lock (see #986). Workers must be spawned
through a clean interpreter instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import phlo_api

_PACKAGE_ROOT = Path(phlo_api.__file__).resolve().parents[2]
_FORK_CONTEXT_USE = re.compile(
    r"(?:get_context|set_start_method)\(\s*[\"']fork[\"']",
)


def test_phlo_api_does_not_use_fork_multiprocessing_context() -> None:
    offenders = [
        str(path.relative_to(_PACKAGE_ROOT))
        for path in sorted(_PACKAGE_ROOT.rglob("*.py"))
        if _FORK_CONTEXT_USE.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"fork multiprocessing context used in: {offenders}"
