"""Object-storage helpers shared by the Iceberg resource and table operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from phlo.capabilities.maintenance import MaintenancePreconditionError


def storage_path_key(path: str) -> str:
    """Normalize URI and PyArrow filesystem paths for reference comparison."""
    parsed = urlsplit(path)
    if parsed.scheme in {"s3", "s3a", "s3n"}:
        return f"{parsed.netloc}{parsed.path}".rstrip("/")
    return path.rstrip("/")


def list_storage_files(io: object, location: str) -> list[Any]:
    """List files through PyIceberg's configured PyArrow filesystem."""
    from pyarrow.fs import FileSelector, FileType

    parse_location = getattr(io, "parse_location", None)
    fs_by_scheme = getattr(io, "fs_by_scheme", None)
    if not callable(parse_location) or not callable(fs_by_scheme):
        raise MaintenancePreconditionError(
            "Configured Iceberg FileIO cannot provide a safe recursive object listing."
        )
    scheme, netloc, path = parse_location(location, getattr(io, "properties", {}))
    filesystem = fs_by_scheme(scheme, netloc)
    infos = filesystem.get_file_info(FileSelector(path, recursive=True, allow_not_found=False))
    return [info for info in infos if getattr(info, "type", None) is FileType.File]
