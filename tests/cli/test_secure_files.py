"""Tests for the atomic, permission-restrictive sensitive-file writer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from phlo.cli.infrastructure.secure_files import (
    SensitiveFilePermissionError,
    SensitiveWriteError,
    UnsupportedPlatformError,
    write_sensitive_file,
)


def test_creates_file_with_0600_mode(tmp_path: Path) -> None:
    target = tmp_path / ".env.local"
    write_sensitive_file(target, "SECRET=value\n")
    assert target.read_text() == "SECRET=value\n"
    assert target.stat().st_mode & 0o7777 == 0o600


def test_replaces_permissive_file_atomically(tmp_path: Path) -> None:
    target = tmp_path / ".env.local"
    target.write_text("OLD=value\n")
    target.chmod(0o644)
    write_sensitive_file(target, "NEW=value\n")
    assert target.read_text() == "NEW=value\n"
    assert target.stat().st_mode & 0o7777 == 0o600


def test_no_temp_files_left_behind_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env.local"
    target.write_text("ORIGINAL=value\n")

    def _boom(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("phlo.cli.infrastructure.secure_files.os.replace", _boom)
    with pytest.raises(SensitiveWriteError):
        write_sensitive_file(target, "NEW=value\n")

    assert target.read_text() == "ORIGINAL=value\n"
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_fchmod_failure_is_fatal_and_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env.local"

    def _boom(_fd: int, _mode: int) -> None:
        raise OSError("simulated chmod failure")

    monkeypatch.setattr("phlo.cli.infrastructure.secure_files.os.fchmod", _boom)
    with pytest.raises(SensitiveWriteError):
        write_sensitive_file(target, "SECRET=value\n")
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_permissive_mode_detected_and_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env.local"

    real_fchmod = os.fchmod

    def _force_permissive_tmp(_fd: int, _mode: int) -> None:
        # Force a wider mode than requested so the verification step detects it.
        real_fchmod(_fd, 0o644)

    monkeypatch.setattr("phlo.cli.infrastructure.secure_files.os.fchmod", _force_permissive_tmp)
    with pytest.raises(SensitiveFilePermissionError):
        write_sensitive_file(target, "SECRET=value\n")
    assert not target.exists()


def test_unsupported_platform_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phlo.cli.infrastructure.secure_files.os.name", "nt")
    with pytest.raises(UnsupportedPlatformError):
        write_sensitive_file(tmp_path / ".env.local", "SECRET=value\n")
    assert not (tmp_path / ".env.local").exists()
