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
DYNAMIC_ALLOWLIST = Path(__file__).with_name("phlo_env_dynamic_reads_allowlist.json")


def _source_roots() -> list[Path]:
    return [ROOT / "src", *sorted((ROOT / "packages").glob("*/src"))]


def _environment_imports(tree: ast.AST) -> dict[str, str]:
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
        if alias.name in {"environ", "getenv"}
    }


def _is_environ(node: ast.expr, imports: dict[str, str]) -> bool:
    return (
        isinstance(node, ast.Name) and (node.id == "environ" or imports.get(node.id) == "environ")
    ) or (isinstance(node, ast.Attribute) and node.attr == "environ")


def _module_constants(tree: ast.AST) -> dict[str, str]:
    """Map module-level string constants, including known non-PHLO keys."""
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
        name = constants.get(node.id)
        return name if name and name.startswith("PHLO_") else None
    if isinstance(node, ast.JoinedStr) and node.values:
        prefix = node.values[0]
        if isinstance(prefix, ast.Constant) and str(prefix.value).startswith("PHLO_"):
            return ast.unparse(node)
    return None


def _read_key(node: ast.AST, imports: dict[str, str]) -> ast.expr | None:
    """Return the key expression when node reads (or pops) os.environ or os.getenv."""
    if isinstance(node, ast.Call):
        func = node.func
        is_get = (
            isinstance(func, ast.Attribute)
            and func.attr in {"get", "pop"}
            and _is_environ(func.value, imports)
        )
        is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv") or (
            isinstance(func, ast.Name) and (func.id == "getenv" or imports.get(func.id) == "getenv")
        )
        if is_get or is_getenv:
            return (
                node.args[0]
                if node.args
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
                )
            )
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        return node.slice if _is_environ(node.value, imports) else None
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.In | ast.NotIn)  # codespell:ignore notin
        and _is_environ(node.comparators[0], imports)
    ):
        return node.left
    return None


def _reads(tree: ast.AST) -> Iterator[str]:
    """Yield PHLO_* names read through os.environ or os.getenv.

    Keys are string literals or module-level constants bound to one.
    """
    constants = _module_constants(tree)
    imports = _environment_imports(tree)
    for node in ast.walk(tree):
        if name := _phlo_name(_read_key(node, imports), constants):
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


def _dynamic_reads(tree: ast.AST) -> Iterator[str]:
    """Yield unresolved read keys for a separate, reviewed dynamic inventory.

    This is deliberately conservative: a generic reader may receive PHLO keys
    even if its current callers do not. Whole-environment forwarding and
    iteration remain a manual inventory, documented in the settings guide.
    """
    constants = _module_constants(tree)
    imports = _environment_imports(tree)
    for node in ast.walk(tree):
        key = _read_key(node, imports)
        if key is None or isinstance(key, ast.Constant) or _phlo_name(key, constants):
            continue
        if isinstance(key, ast.Name) and key.id in constants:
            continue
        yield ast.unparse(key)


def find_dynamic_reads() -> dict[str, list[str]]:
    """Pin unresolved expressions, not line numbers or runtime secret values."""
    found = {}
    for root in _source_roots():
        for path in sorted(root.rglob("*.py")):
            keys = sorted(set(_dynamic_reads(ast.parse(path.read_text(encoding="utf-8")))))
            if keys:
                found[path.relative_to(ROOT).as_posix()] = keys
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


def test_dynamic_read_inventory_is_current() -> None:
    approved = json.loads(DYNAMIC_ALLOWLIST.read_text(encoding="utf-8"))
    assert find_dynamic_reads() == approved, (
        "Review and classify changed dynamic environment readers in "
        "docs/contributing/process-settings.md before updating the dynamic inventory."
    )


def test_scanner_detects_dynamic_phlo_and_unresolved_keys() -> None:
    tree = ast.parse(
        "os.getenv(f'PHLO_NEW_{target}')\nos.environ.get(key)\nKNOWN = 'OTHER'\nos.getenv(KNOWN)"
    )
    assert list(_reads(tree)) == ["f'PHLO_NEW_{target}'"]
    assert list(_dynamic_reads(tree)) == ["key"]


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


def test_scanner_detects_import_aliases_and_keyword_keys() -> None:
    source = """
from os import environ as env, getenv as read_env
os.getenv(key="PHLO_A")
env.get("PHLO_B")
env["PHLO_C"]
"PHLO_D" in env
read_env(key="PHLO_E")
env.get(dynamic_key)
"""
    tree = ast.parse(source)
    assert sorted(_reads(tree)) == [f"PHLO_{c}" for c in "ABCDE"]
    assert list(_dynamic_reads(tree)) == ["dynamic_key"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: test_phlo_env_reads.py --write")
    ALLOWLIST.write_text(json.dumps(find_direct_reads(), indent=2) + "\n", encoding="utf-8")
    DYNAMIC_ALLOWLIST.write_text(
        json.dumps(find_dynamic_reads(), indent=2) + "\n", encoding="utf-8"
    )
