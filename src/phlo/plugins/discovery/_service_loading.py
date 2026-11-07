"""Locate installed service plugin packages for manifests and file validation."""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any


def resolve_plugin_source_path(plugin: Any) -> Path | None:
    """Resolve plugin package path for companion service file discovery."""
    module_name = plugin.__class__.__module__
    package_name = module_name.split(".", 1)[0]
    spec = find_spec(package_name)
    if not spec or not spec.origin:
        return None
    return Path(spec.origin).parent
