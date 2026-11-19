"""Read and write project notebooks as plain ``.ipynb`` files.

Notebooks live under ``<project>/notebooks``. They are standard nbformat 4
documents so they open in JupyterLab and VS Code. Phlo keeps its own cell
details (SQL engine, result name) in ``metadata.phlo`` and never writes
outputs, so diffs stay readable and results never land in git.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

NOTEBOOK_DIR = "notebooks"
MAX_NOTEBOOK_BYTES = 2 * 1024 * 1024
MAX_NOTEBOOKS = 500
CellKind = Literal["markdown", "python", "sql"]
SqlEngine = Literal["trino", "duckdb"]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_WRITE_LOCK = threading.Lock()

_fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:
    pass  # Windows: the in-process lock still serialises saves.


class NotebookPathError(ValueError):
    """The requested path is not a notebook inside the project's notebooks folder."""


class NotebookConflict(RuntimeError):
    """The notebook changed on disk since the caller read it."""


class NotebookInvalid(ValueError):
    """The file is not a notebook Phlo can read."""


@dataclass(frozen=True)
class NotebookCell:
    id: str
    kind: CellKind
    source: str
    name: str | None = None
    engine: SqlEngine | None = None


@dataclass(frozen=True)
class NotebookSummary:
    path: str
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class NotebookDocument:
    path: str
    digest: str
    cells: tuple[NotebookCell, ...]


def notebook_root(project: Path) -> Path:
    return project / NOTEBOOK_DIR


def resolve_notebook_path(project: Path, path: str) -> Path:
    """Map a request path such as ``market/trials.ipynb`` to a file in the notebooks folder."""
    if not path or "\\" in path or path.startswith("/"):
        raise NotebookPathError("Give a path relative to the notebooks folder.")
    parts = PurePosixPath(path).parts
    if not parts or any(not _SEGMENT.fullmatch(part) for part in parts):
        raise NotebookPathError("Notebook paths use letters, digits, '_', '-' and '.' only.")
    if not parts[-1].endswith(".ipynb"):
        raise NotebookPathError("Notebook paths end in .ipynb.")
    root = notebook_root(project).resolve()
    target = (root / PurePosixPath(*parts)).resolve()
    if root not in target.parents:
        raise NotebookPathError("Notebook paths must stay inside the notebooks folder.")
    return target


def list_notebooks(project: Path) -> tuple[list[NotebookSummary], bool]:
    root = notebook_root(project).resolve()
    if not root.is_dir():
        return [], False
    found: list[NotebookSummary] = []
    for file in sorted(root.rglob("*.ipynb")):
        relative = file.relative_to(root).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            continue
        if not file.is_file() or file.is_symlink():
            continue
        stat = file.stat()
        found.append(
            NotebookSummary(
                path=relative,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
        if len(found) > MAX_NOTEBOOKS:
            return found[:MAX_NOTEBOOKS], True
    return found, False


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    if isinstance(value, str):
        return value
    raise NotebookInvalid("A cell's source must be text.")


def _cell_from_json(value: object, index: int) -> NotebookCell:
    if not isinstance(value, dict):
        raise NotebookInvalid(f"Cell {index + 1} is not an object.")
    raw = cast(dict[str, Any], value)
    cell_type = raw.get("cell_type")
    source = _source(raw.get("source", ""))
    cell_id = str(raw.get("id") or f"cell-{index}")
    phlo = (raw.get("metadata") or {}).get("phlo") or {}
    name = phlo.get("name") if isinstance(phlo.get("name"), str) else None
    if name is not None and not _NAME.fullmatch(name):
        name = None
    if cell_type == "markdown" or cell_type == "raw":
        return NotebookCell(id=cell_id, kind="markdown", source=source)
    if cell_type != "code":
        raise NotebookInvalid(f"Cell {index + 1} has an unknown type.")
    if phlo.get("kind") == "sql":
        engine = phlo.get("engine") if phlo.get("engine") in ("trino", "duckdb") else "trino"
        return NotebookCell(id=cell_id, kind="sql", source=source, name=name, engine=engine)
    return NotebookCell(id=cell_id, kind="python", source=source, name=name)


def read_notebook(project: Path, path: str) -> NotebookDocument:
    file = resolve_notebook_path(project, path)
    if not file.is_file() or file.is_symlink():
        raise FileNotFoundError(path)
    raw = file.read_bytes()
    if len(raw) > MAX_NOTEBOOK_BYTES:
        raise NotebookInvalid("This notebook is larger than 2 MB.")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotebookInvalid("This file is not valid notebook JSON.") from exc
    if not isinstance(document, dict) or document.get("nbformat") != 4:
        raise NotebookInvalid("Only nbformat 4 notebooks are supported.")
    cells = document.get("cells")
    if not isinstance(cells, list):
        raise NotebookInvalid("The notebook has no cell list.")
    return NotebookDocument(
        path=path,
        digest=_digest(raw),
        cells=tuple(_cell_from_json(cell, index) for index, cell in enumerate(cells)),
    )


def _lines(source: str) -> list[str]:
    return source.splitlines(keepends=True)


def _cell_to_json(cell: NotebookCell) -> dict[str, Any]:
    if cell.name is not None and not _NAME.fullmatch(cell.name):
        raise NotebookInvalid(f"'{cell.name}' is not a valid result name.")
    if cell.kind == "markdown":
        return {
            "cell_type": "markdown",
            "id": cell.id,
            "metadata": {},
            "source": _lines(cell.source),
        }
    phlo: dict[str, Any] = {}
    if cell.kind == "sql":
        phlo = {"kind": "sql", "engine": cell.engine or "trino"}
    if cell.name:
        phlo["name"] = cell.name
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell.id,
        "metadata": {"phlo": phlo} if phlo else {},
        "outputs": [],
        "source": _lines(cell.source),
    }


def render_notebook(cells: list[NotebookCell]) -> bytes:
    document = {
        "cells": [_cell_to_json(cell) for cell in cells],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return (json.dumps(document, indent=1, ensure_ascii=False) + "\n").encode()


def write_notebook(
    project: Path, path: str, cells: list[NotebookCell], expected_digest: str | None
) -> NotebookDocument:
    """Write a notebook, refusing when the file changed since ``expected_digest`` was read.

    ``expected_digest`` is ``None`` only when creating a notebook that must not exist yet.
    """
    file = resolve_notebook_path(project, path)
    if file.is_symlink():
        raise NotebookPathError("Notebook paths cannot be symbolic links.")
    raw = render_notebook(cells)
    if len(raw) > MAX_NOTEBOOK_BYTES:
        raise NotebookInvalid("Notebooks are limited to 2 MB.")
    file.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive(file.parent):
        # The digest is checked under the lock, so two saves of one version cannot both win.
        current = _digest(file.read_bytes()) if file.exists() else None
        if current != expected_digest:
            raise NotebookConflict(path)
        handle, temp = tempfile.mkstemp(dir=file.parent, prefix=".notebook-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
            os.replace(temp, file)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    return NotebookDocument(path=path, digest=_digest(raw), cells=tuple(cells))


@contextmanager
def _exclusive(folder: Path) -> Iterator[None]:
    """Serialise check-and-replace in a folder across threads and API processes."""
    with _WRITE_LOCK:
        if _fcntl is None:
            yield
            return
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)
