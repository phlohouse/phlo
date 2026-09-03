"""Atomic, permission-restrictive writes for sensitive generated files.

Sensitive files (secret-bearing environment files such as ``.phlo/.env.local``)
must never appear with permissive permissions, even transiently. This module
provides one narrow helper: create a sibling temporary file, set the restrictive
mode before it becomes visible, write and flush, then atomically replace the
destination. A prior permissive mode is never preserved because the replacement
installs a brand-new inode with the restrictive mode.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

SENSITIVE_FILE_MODE = 0o600


class SensitiveWriteError(RuntimeError):
    """Base class for sensitive-file write failures."""


class UnsupportedPlatformError(SensitiveWriteError):
    """The current platform cannot guarantee the required restrictive mode."""


class SensitiveFilePermissionError(SensitiveWriteError):
    """The restrictive file mode could not be established or verified."""


def write_sensitive_file(path: Path | str, content: str) -> None:
    """Create or replace ``path`` with ``content`` at mode ``0600`` atomically.

    On POSIX, failure to establish or verify the restrictive mode is fatal. On
    unsupported platforms the write refuses rather than claiming security it
    cannot provide. The temporary file is removed on any failure.
    """
    if os.name != "posix":
        raise UnsupportedPlatformError(
            "sensitive-file writes with a guaranteed restrictive mode are not supported "
            f"on this platform ({os.name!r})"
        )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd: int | None = None
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        tmp_path = Path(tmp_name)
        os.fchmod(fd, SENSITIVE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None  # ownership transferred to the file object
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        actual_mode = tmp_path.stat().st_mode & 0o7777
        if actual_mode != SENSITIVE_FILE_MODE:
            raise SensitiveFilePermissionError(
                f"temporary sensitive file has mode {oct(actual_mode)}, expected {oct(SENSITIVE_FILE_MODE)}"
            )

        tmp_path.replace(target)
        tmp_path = None
    except OSError as exc:
        raise SensitiveWriteError(f"failed to write sensitive file {target}: {exc}") from exc
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
