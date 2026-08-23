"""Observatory extension manifest and asset endpoints.

Provides endpoints for discovering and serving Observatory extension plugins.
Extensions are Python packages that register via entry points and provide
UI components, pages, and asset files to customize the Observatory interface.

Key Endpoints:
    GET /api/observatory/extensions: List all installed extensions.
    GET /api/observatory/extensions/{name}: Get single extension manifest.
    GET /api/observatory/extensions/{name}/assets/{path}: Serve extension assets.

Example:
    Listing available extensions:

    .. code-block:: bash

        curl http://localhost:4000/api/observatory/extensions

    Response includes manifest and base paths for each extension's assets.

"""

from __future__ import annotations

import importlib.metadata
import itertools
import re
import shutil
import tempfile
import threading
import time
from importlib.resources import as_file
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from phlo.logging import get_logger

try:
    from phlo.plugins.observatory import discover_observatory_extensions
except Exception:  # noqa: BLE001 - observatory package is optional

    def discover_observatory_extensions() -> list[Any]:
        """Return no extensions when Observatory package is unavailable."""
        return []


logger = get_logger(__name__)

router = APIRouter(prefix="/api/observatory", tags=["observatory"])

_CACHE_TTL_SECONDS = 5.0
_cache_lock = threading.Lock()
_cached_extensions: list[Any] | None = None
_cache_timestamp: float | None = None


def _load_extensions() -> list[Any]:
    global _cached_extensions, _cache_timestamp
    now = time.monotonic()
    with _cache_lock:
        if _cached_extensions is not None and _cache_timestamp is not None:
            if now - _cache_timestamp < _CACHE_TTL_SECONDS:
                return _cached_extensions

    # Discovery runs outside the lock so a slow plugin scan cannot serialize
    # requests; concurrent discoverers simply race and the last publish wins.
    observatory_version = _get_observatory_version()
    extensions = []
    for plugin in discover_observatory_extensions():
        if not _is_compatible(plugin, observatory_version):
            logger.warning(
                "Skipping incompatible Observatory extension: %s",
                plugin.metadata.name,
            )
            continue
        extensions.append(plugin)

    with _cache_lock:
        _cached_extensions = extensions
        _cache_timestamp = now

    return extensions


def _extension_payload(plugin: Any) -> dict[str, Any]:
    manifest = plugin.get_manifest()
    return {
        "manifest": manifest.model_dump(),
        "assets_base_path": f"/api/observatory/extensions/{plugin.metadata.name}/assets",
    }


def _get_observatory_version() -> str | None:
    try:
        return importlib.metadata.version("phlo-observatory")
    except importlib.metadata.PackageNotFoundError:
        return None


def _parse_version(value: str) -> tuple[int, ...]:
    parts = re.split(r"[.+-]", value)
    numbers = tuple(int(p) for p in itertools.takewhile(str.isdigit, parts))
    return numbers or (0,)


def _is_compatible(plugin: Any, observatory_version: str | None) -> bool:
    if not observatory_version:
        return True
    manifest = plugin.get_manifest()
    required = manifest.compat.observatory_min
    return _parse_version(observatory_version) >= _parse_version(required)


@router.get("/extensions")
def list_extensions() -> dict[str, list[dict[str, Any]]]:
    """List all installed Observatory extensions with manifests and asset paths."""
    extensions = _load_extensions()
    return {"extensions": [_extension_payload(plugin) for plugin in extensions]}


@router.get("/extensions/{name}")
def get_extension(name: str) -> dict[str, Any]:
    """Get a single extension payload with its manifest and assets_base_path.

    Raises HTTPException when the extension is not found (404).

    """
    extensions = _load_extensions()
    plugin = next((p for p in extensions if p.metadata.name == name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Observatory extension not found: {name}")
    return _extension_payload(plugin)


def _cleanup_temp_dir(dir_path: Path) -> None:
    """Remove temporary directory and contents after response is sent."""
    shutil.rmtree(dir_path, ignore_errors=True)


@router.get("/extensions/{name}/assets/{asset_path:path}")
def get_extension_asset(name: str, asset_path: str, background_tasks: BackgroundTasks):
    """Serve an extension asset file, scheduling temp-dir cleanup after the response.

    Raises HTTPException when the extension is not found (404), the asset
    path is invalid (400), or the asset is missing (404).

    """
    extensions = _load_extensions()
    plugin = next((p for p in extensions if p.metadata.name == name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Observatory extension not found: {name}")

    if not asset_path:
        raise HTTPException(status_code=404, detail="Asset path required")

    path = PurePosixPath(asset_path)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="Invalid asset path")

    asset = plugin.asset_root.joinpath(*path.parts)
    try:
        if not asset.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
    except (AttributeError, OSError):
        raise HTTPException(status_code=404, detail="Asset not found")

    # as_file may resolve to a path inside a zip or a temporary extract
    # location that disappears when the context exits. Copy to a temp file the
    # response can still read while streaming; the background task reclaims it.
    try:
        with as_file(asset) as resolved:
            temp_dir = Path(tempfile.mkdtemp())
            temp_file = temp_dir / resolved.name
            shutil.copy2(resolved, temp_file)

        background_tasks.add_task(_cleanup_temp_dir, temp_dir)
        return FileResponse(temp_file)
    except Exception as exc:
        logger.exception("Failed to serve extension asset")
        raise HTTPException(status_code=500, detail="Failed to serve asset") from exc
