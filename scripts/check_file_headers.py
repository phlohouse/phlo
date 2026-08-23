#!/usr/bin/env python3
"""Enforce the top-of-file header convention on changed source files.

Every tracked .py, .ts, .tsx, .js, .sql, and .sh file must begin with a
header block: a module docstring for Python, a comment block for the other
languages. A file may opt out by putting `phlo: no-header` on its first
line (or second, after a shebang). See CONTRIBUTING.md, "Comments and
docstrings".

Runs under pre-commit with the changed file paths as arguments.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

OPT_OUT = "phlo: no-header"
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sql", ".sh"}


def has_header(path: Path) -> bool:
    """Report whether path starts with the required header block."""
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    if not lines:
        return False
    if OPT_OUT in lines[0] or (len(lines) > 1 and OPT_OUT in lines[1]):
        return True
    if path.suffix == ".py":
        try:
            return bool(ast.get_docstring(ast.parse(text)))
        except SyntaxError:
            return False
    if path.suffix == ".sh":
        if lines[0].startswith("#!"):
            return len(lines) > 1 and lines[1].lstrip().startswith("#")
        return bool(lines[0].lstrip().startswith("#"))
    first = lines[1].lstrip() if len(lines) > 1 and lines[0].startswith("#!") else lines[0].lstrip()
    if path.suffix == ".sql":
        return first.startswith("--")
    return first.startswith(("/*", "//"))


def main(argv: list[str]) -> int:
    """Check each passed file; return nonzero listing any without a header."""
    missing = [
        arg
        for arg in argv
        if Path(arg).suffix in EXTENSIONS and Path(arg).exists() and not has_header(Path(arg))
    ]
    for path in missing:
        print(f"{path}: missing top-of-file header (see CONTRIBUTING.md)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
