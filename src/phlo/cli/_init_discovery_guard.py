"""Disable eager plugin discovery for built-in init invocations.

Imported for its side effect before the CLI loads: when argv's first root
command is `init` (global flags short-circuit), PHLO_NO_AUTO_DISCOVER is
set unless already present, so a fresh project can initialize without any
plugins installed.
"""

from __future__ import annotations

import os
import sys

_GLOBAL_FLAGS_WITHOUT_VALUES = {"--help", "-h", "--version"}


def _root_command_name(argv: list[str]) -> str | None:
    for token in argv[1:]:
        if token in _GLOBAL_FLAGS_WITHOUT_VALUES:
            return None
        if token.startswith("-"):
            continue
        return token
    return None


def is_init_command_invocation(argv: list[str] | None = None) -> bool:
    args = sys.argv if argv is None else argv
    return _root_command_name(args) == "init"


if is_init_command_invocation():
    os.environ.setdefault("PHLO_NO_AUTO_DISCOVER", "1")
