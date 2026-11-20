"""Cell outputs and notebook run history, kept beside the project but out of git.

Notebook files never hold outputs, so results stay out of commits. The last
output of each cell is kept here instead, under ``<project>/.phlo`` (ignored
by git in every Phlo project), so reopening a notebook shows its results. Each
output records the hash of the source that produced it, so the page can say
when a cell changed since it last ran. Outputs expire after a retention period
and each notebook's outputs are capped in size, oldest dropped first.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

_fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:
    pass  # Windows: the in-process lock still serialises writes.

STATE_DIR = ".phlo"
OUTPUTS_DIR = "notebook-outputs"
RUNS_DIR = "notebook-runs"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_RUNS = 50
_LOCK = threading.Lock()


def _now() -> float:
    """Wall-clock seconds; tests replace this rather than the global time module."""
    return time.time()


def _key(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:32]


def _folder(project: Path, kind: str) -> Path:
    folder = project / STATE_DIR / kind
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


@contextmanager
def _locked(file: Path) -> Iterator[None]:
    with _LOCK:
        if _fcntl is None:
            yield
            return
        descriptor = os.open(file.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _load(file: Path) -> dict[str, Any]:
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(file: Path, payload: dict[str, Any]) -> None:
    handle, temp = tempfile.mkstemp(dir=file.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(payload, stream, default=str, separators=(",", ":"))
        os.replace(temp, file)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _update(
    project: Path, kind: str, path: str, change: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    file = _folder(project, kind) / f"{_key(path)}.json"
    with _locked(file):
        data = change(_load(file))
        data["path"] = path
        _store(file, data)
    return data


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def record_output(
    project: Path,
    path: str,
    cell_id: str,
    *,
    source_sha256: str,
    actor: str,
    run: dict[str, Any],
    retention_days: int,
) -> None:
    """Keep a cell's latest output, dropping expired ones and the oldest past the size cap."""

    def change(data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("cells")
        cells: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        cells[cell_id] = {
            "source_sha256": source_sha256,
            "actor": actor,
            "finished_at": _now(),
            "run": run,
        }
        cells = _within_budget(_unexpired(cells, retention_days))
        return {"cells": cells}

    _update(project, OUTPUTS_DIR, path, change)


def _unexpired(cells: dict[str, Any], retention_days: int) -> dict[str, Any]:
    cutoff = _now() - retention_days * 86400
    return {
        key: value
        for key, value in cells.items()
        if isinstance(value, dict) and float(value.get("finished_at") or 0) >= cutoff
    }


def _within_budget(cells: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(cells.items(), key=lambda item: float(item[1].get("finished_at") or 0))
    while ordered and len(json.dumps(dict(ordered), default=str)) > MAX_OUTPUT_BYTES:
        ordered.pop(0)
    return dict(ordered)


def read_outputs(project: Path, path: str, retention_days: int) -> dict[str, Any]:
    file = project / STATE_DIR / OUTPUTS_DIR / f"{_key(path)}.json"
    cells = _load(file).get("cells")
    return _unexpired(cells, retention_days) if isinstance(cells, dict) else {}


def record_run(project: Path, path: str, run: dict[str, Any]) -> None:
    """Add or replace a notebook run (matched by ``id``), keeping the latest ``MAX_RUNS``."""

    def change(data: dict[str, Any]) -> dict[str, Any]:
        runs = [item for item in data.get("runs", []) if isinstance(item, dict)]
        runs = [item for item in runs if item.get("id") != run["id"]]
        runs.append(run)
        runs.sort(key=lambda item: float(item.get("started_at") or 0))
        return {"runs": runs[-MAX_RUNS:]}

    _update(project, RUNS_DIR, path, change)


def read_runs(project: Path, path: str) -> list[dict[str, Any]]:
    file = project / STATE_DIR / RUNS_DIR / f"{_key(path)}.json"
    runs = _load(file).get("runs")
    if not isinstance(runs, list):
        return []
    return [cast(dict[str, Any], run) for run in reversed(runs) if isinstance(run, dict)]


def move_state(project: Path, path: str, to: str) -> None:
    for kind in (OUTPUTS_DIR, RUNS_DIR):
        source = project / STATE_DIR / kind / f"{_key(path)}.json"
        if source.exists():
            data = _load(source)
            data["path"] = to
            _store(_folder(project, kind) / f"{_key(to)}.json", data)
            source.unlink(missing_ok=True)


def delete_state(project: Path, path: str) -> None:
    for kind in (OUTPUTS_DIR, RUNS_DIR):
        (project / STATE_DIR / kind / f"{_key(path)}.json").unlink(missing_ok=True)
