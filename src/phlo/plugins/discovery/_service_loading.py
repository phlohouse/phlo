"""Service loading helpers for plugin and file-backed service definitions.

Definitions merge into one dict keyed by service name: already-present names
win over plugin-provided ones, and invalid definitions are logged as warnings
and skipped instead of aborting discovery.
Imported by sibling phlo.plugins.discovery modules (services, service_manifest) and the plugin
check CLI command.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

from phlo.logging_context import get_logger
from phlo.plugins.discovery._plugin_loading import discover_plugins as _discover_plugins

logger = get_logger(__name__)


def discover_plugins(plugin_type: str = "service", auto_register: bool = True):
    """Compatibility wrapper used by service discovery call sites and tests."""
    return _discover_plugins(plugin_type=plugin_type, auto_register=auto_register)


def resolve_plugin_source_path(plugin: Any) -> Path | None:
    """Resolve plugin package path for companion service file discovery."""
    module_name = plugin.__class__.__module__
    package_name = module_name.split(".", 1)[0]
    spec = find_spec(package_name)
    if not spec or not spec.origin:
        return None
    return Path(spec.origin).parent
