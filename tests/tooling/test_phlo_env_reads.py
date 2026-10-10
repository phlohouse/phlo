"""Guard against new direct ``PHLO_*`` environment reads in production code.

Supported ``PHLO_*`` variables belong in package-owned settings models so they
are typed, defaulted once and listed in ``docs/reference/settings.md`` (#1012).
Keys may be string literals or module-level constants bound to one.
Reads that predate that rule are pinned in ``phlo_env_reads_allowlist.json``.
The allow-list only shrinks: a new read fails the first test, and moving a read
into a settings model fails the second until its entry is removed.

Regenerate the allow-list after moving reads with
``uv run python tests/tooling/test_phlo_env_reads.py --write``.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("phlo_env_reads_allowlist.json")


def _source_roots() -> list[Path]:
    return [ROOT / "src", *sorted((ROOT / "packages").glob("*/src"))]


def _is_environ(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "environ") or (
        isinstance(node, ast.Attribute) and node.attr == "environ"
    )


def _module_constants(tree: ast.AST) -> dict[str, str]:
    """Map module-level names bound once to a PHLO_* string literal."""
    constants: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value.startswith("PHLO_")
        ):
            constants[target.id] = value.value
    return constants


def _phlo_name(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("PHLO_")
    ):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _read_key(node: ast.AST) -> ast.expr | None:
    """Return the key expression when node reads (or pops) os.environ or os.getenv."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        is_get = (
            isinstance(func, ast.Attribute)
            and func.attr in {"get", "pop"}
            and _is_environ(func.value)
        )
        is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
            isinstance(func, ast.Name) and func.id == "getenv"
        )
        return node.args[0] if is_get or is_getenv else None
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        return node.slice if _is_environ(node.value) else None
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.In | ast.NotIn)
        and _is_environ(node.comparators[0])
    ):
        return node.left
    return None


def _reads(tree: ast.AST) -> Iterator[str]:
    """Yield PHLO_* names read through os.environ or os.getenv.

    Keys are string literals or module-level constants bound to one.
    """
    constants = _module_constants(tree)
    for node in ast.walk(tree):
        if name := _phlo_name(_read_key(node), constants):
            yield name


def find_direct_reads() -> dict[str, list[str]]:
    """Map each production source file to the PHLO_* names it reads directly."""
    found: dict[str, list[str]] = {}
    for root in _source_roots():
        for path in sorted(root.rglob("*.py")):
            names = sorted(set(_reads(ast.parse(path.read_text(encoding="utf-8")))))
            if names:
                found[path.relative_to(ROOT).as_posix()] = names
    return found


def _pairs(mapping: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {(path, name) for path, names in mapping.items() for name in names}


def _allowlist() -> dict[str, list[str]]:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))


def test_no_new_direct_phlo_env_reads() -> None:
    new = sorted(_pairs(find_direct_reads()) - _pairs(_allowlist()))
    assert not new, (
        "Declare PHLO_* variables in a package settings model (phlo.config.base.BaseConfig) "
        "instead of reading os.environ directly:\n"
        + "\n".join(f"  {path}: {name}" for path, name in new)
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(_pairs(_allowlist()) - _pairs(find_direct_reads()))
    assert not stale, (
        "These direct reads are gone; remove them from "
        f"{ALLOWLIST.relative_to(ROOT)} (or rerun this file with --write):\n"
        + "\n".join(f"  {path}: {name}" for path, name in stale)
    )


def test_scanner_detects_each_read_form() -> None:
    source = (
        "import os\nfrom os import environ, getenv\n"
        "os.environ.get('PHLO_A')\nos.getenv('PHLO_B')\nos.environ['PHLO_C']\n"
        "'PHLO_D' in os.environ\nenviron.get('PHLO_E')\ngetenv('PHLO_F')\n"
        "_KEY = 'PHLO_G'\n_TYPED: str = 'PHLO_H'\nos.environ.get(_KEY)\nos.getenv(_TYPED)\n"
        "os.environ.pop('PHLO_I', None)\n"
        "os.environ['PHLO_WRITE'] = '1'\nos.environ.get('OTHER')\n"
    )
    assert sorted(_reads(ast.parse(source))) == [f"PHLO_{c}" for c in "ABCDEFGHI"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: test_phlo_env_reads.py --write")
    ALLOWLIST.write_text(json.dumps(find_direct_reads(), indent=2) + "\n", encoding="utf-8")
