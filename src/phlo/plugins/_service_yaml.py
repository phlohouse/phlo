"""Shared, mtime-aware YAML reads for package and directory service manifests."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@lru_cache(maxsize=256)
def _read_yaml(path: Path, mtime_ns: int) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_handle:
        data = yaml.safe_load(file_handle)
    if not isinstance(data, dict):
        raise ValueError(f"Service definition must be a mapping: {path}")
    return data


def load_service_yaml(path: Path) -> dict[str, Any]:
    """Return independent data without reparsing unchanged manifest files."""
    path = path.resolve()
    return deepcopy(_read_yaml(path, path.stat().st_mtime_ns))


def refresh() -> None:
    """Forget parsed manifests, including files whose mtime was preserved."""
    _read_yaml.cache_clear()
