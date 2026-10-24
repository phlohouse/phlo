"""Binary discovery, invocation, and verified installation."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo_transform import binary as binary_module
from phlo_transform.binary import (
    binary_name,
    find_binary,
    install_binary,
    release_target,
    require_binary,
    run_cli,
)
from phlo_transform.settings import TransformSettings


def _release_archive(name: str = "phlo-transform", contents: bytes = b"#!/bin/sh\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(contents)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(contents))
    return buffer.getvalue()


def test_find_binary_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_TRANSFORM_BIN", raising=False)
    installed = tmp_path / ".phlo" / "bin" / binary_name()
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\n")

    assert find_binary(tmp_path, TransformSettings(_project_root=tmp_path)) == installed

    configured = tmp_path / "custom-bin"
    configured.write_text("#!/bin/sh\n")
    monkeypatch.setenv("PHLO_TRANSFORM_BIN", str(configured))
    assert find_binary(tmp_path, TransformSettings(_project_root=tmp_path)) == configured


def test_find_binary_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_TRANSFORM_BIN", raising=False)
    on_path = tmp_path / "path-bin"
    on_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(binary_module.shutil, "which", lambda name: str(on_path))

    assert find_binary(tmp_path, TransformSettings(_project_root=tmp_path)) == on_path


def test_require_binary_explains_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHLO_TRANSFORM_BIN", raising=False)
    monkeypatch.setattr(binary_module.shutil, "which", lambda name: None)

    with pytest.raises(PhloCapabilitySetupError, match="executable is missing") as error:
        require_binary(tmp_path, TransformSettings(_project_root=tmp_path))

    assert any(
        "phlo transform install" in suggestion for suggestion in error.value.suggestions or []
    )


def test_install_downloads_and_verifies_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _release_archive()
    digest = binary_module._sha256(archive)
    requested: list[str] = []

    def fake_download(url: str, timeout: float) -> bytes:
        requested.append(url)
        return archive if url.endswith(".tar.gz") else f"{digest}  phlo-transform.tar.gz\n".encode()

    monkeypatch.setattr(binary_module, "release_target", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(binary_module, "_download", fake_download)
    settings = TransformSettings(_project_root=tmp_path)

    installed = install_binary(tmp_path, settings, version="0.1.0")

    assert installed == tmp_path / ".phlo" / "bin" / binary_name()
    assert installed.read_bytes() == b"#!/bin/sh\n"
    assert requested[0].endswith("/v0.1.0/phlo-transform-x86_64-unknown-linux-gnu.tar.gz")


def test_install_rejects_checksum_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_module, "release_target", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(
        binary_module,
        "_download",
        lambda url, timeout: (
            b"deadbeef  archive\n" if url.endswith(".sha256") else _release_archive()
        ),
    )
    settings = TransformSettings(_project_root=tmp_path)

    with pytest.raises(PhloCapabilitySetupError, match="checksum mismatch"):
        install_binary(tmp_path, settings, version="0.1.0")


def test_existing_binary_is_preserved_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".phlo" / "bin" / binary_name()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")

    def fail(*args, **kwargs):
        raise AssertionError("download must not run")

    monkeypatch.setattr(binary_module, "_download", fail)
    settings = TransformSettings(_project_root=tmp_path)

    assert install_binary(tmp_path, settings) == target
    assert target.read_bytes() == b"existing"


def test_release_target_rejects_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_module.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(binary_module.platform, "machine", lambda: "pdp11")

    with pytest.raises(PhloCapabilitySetupError, match="no portable release"):
        release_target()


def test_run_cli_reports_missing_executable(tmp_path: Path) -> None:
    settings = TransformSettings(_project_root=tmp_path)
    missing = tmp_path / "missing-binary"

    with pytest.raises(PhloCapabilitySetupError, match="executable not found"):
        run_cli(missing, ["check"], settings=settings, root=tmp_path)


def test_private_credentials_reach_subprocess_but_not_logs_or_invocation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    captured = {}
    messages = []

    def invoke(command, **kwargs):
        captured.update(command=command, environment=kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(binary_module.subprocess, "run", invoke)
    monkeypatch.setattr(
        binary_module.logger, "debug", lambda *args, **kwargs: messages.append(kwargs)
    )
    result = run_cli(
        tmp_path / "engine",
        ["plan", "--trino-password", "argv-secret", "--nessie-token=token-secret"],
        settings=TransformSettings(),
        root=tmp_path,
        environment={"PHLO_TRINO_PASSWORD": "environment-secret"},
    )
    assert captured["environment"]["PHLO_TRINO_PASSWORD"] == "environment-secret"
    assert "argv-secret" in captured["command"]
    assert "argv-secret" not in str(messages) + str(result)
    assert "token-secret" not in str(messages) + str(result)
    assert "environment-secret" not in str(messages) + str(result)


def test_timeout_exception_and_cause_redact_command_credentials(tmp_path, monkeypatch):
    def invoke(command, **kwargs):
        raise binary_module.subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(binary_module.subprocess, "run", invoke)
    with pytest.raises(PhloCapabilitySetupError) as caught:
        run_cli(
            tmp_path / "engine",
            ["plan", "--trino-password=private-secret"],
            settings=TransformSettings(),
            root=tmp_path,
        )
    assert "private-secret" not in str(caught.value)
    assert "private-secret" not in str(caught.value.__cause__)
