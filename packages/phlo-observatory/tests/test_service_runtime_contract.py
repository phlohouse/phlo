"""Runtime contract checks for observatory service packaging.

The bundled container must run unprivileged, ship the docker CLI for status
discovery, and never mount the host Docker socket.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT_USER_SPECS = frozenset({"root", "0", "0:0", "root:root"})
_APK_ADD_DOCKER_CLI = re.compile(r"\bapk\s+add\b[^&|;]*\bdocker-cli(?=[=<\s&|;]|$)")


def _load_service_document() -> dict[str, Any]:
    raw = resources.files("phlo_observatory").joinpath("service.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    assert isinstance(document, dict), "service.yaml must be a mapping"
    return document


def _iter_mount_strings(document: dict[str, Any]) -> Iterator[str]:
    """Yield every mount/device spec declared in any containerized section."""
    for section_name in ("compose", "dev"):
        section = document.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in ("volumes", "devices"):
            entries = section.get(key)
            if isinstance(entries, list):
                yield from (entry for entry in entries if isinstance(entry, str))


def _dockerfile_instructions() -> list[tuple[str, str]]:
    """Return ``(VERB, arguments)`` pairs with line continuations joined."""
    raw = resources.files("phlo_observatory").joinpath("Dockerfile").read_text(encoding="utf-8")
    instructions: list[tuple[str, str]] = []
    pending = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}" if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        verb, _, args = pending.partition(" ")
        instructions.append((verb.upper(), args.strip()))
        pending = ""
    assert not pending, "Dockerfile ends inside a line continuation"
    return instructions


def test_observatory_service_does_not_mount_docker_socket() -> None:
    """Bundled defaults should not expose host Docker daemon control."""
    socket_mounts = [
        mount for mount in _iter_mount_strings(_load_service_document()) if "docker.sock" in mount
    ]
    assert socket_mounts == []


def test_observatory_service_declares_no_root_identity_or_privilege_grants() -> None:
    """Hardening knobs, when declared, must not restore root or capabilities."""
    compose = _load_service_document().get("compose") or {}

    user_spec = compose.get("user")
    if user_spec is not None:
        assert str(user_spec).strip().lower() not in _ROOT_USER_SPECS

    cap_drop = compose.get("cap_drop")
    if cap_drop is not None:
        dropped = {str(item).strip().upper() for item in cap_drop}
        assert "ALL" in dropped, "partial cap_drop leaves other capabilities enabled"

    for option in compose.get("security_opt") or []:
        assert "no-new-privileges:false" not in str(option).lower()


def test_observatory_dockerfile_installs_docker_cli() -> None:
    """Container image should include docker CLI for service status discovery."""
    run_commands = [args for verb, args in _dockerfile_instructions() if verb == "RUN"]
    assert any(_APK_ADD_DOCKER_CLI.search(command) for command in run_commands)


def test_observatory_bundles_replacement_without_removing_legacy() -> None:
    package = resources.files("phlo_observatory")
    manifest = json.loads(package.joinpath("package.json").read_text(encoding="utf-8"))
    assert manifest["workspaces"] == ["replacement"]
    for path in (
        "src/routes/index.tsx",
        "replacement/package.json",
        "replacement/vite.config.ts",
        "replacement/src/lib/data/api/client.ts",
        "replacement/src/routes/healthz.ts",
        "replacement/scripts/verify-api-client.mjs",
        "select-ui.sh",
    ):
        assert package.joinpath(path).is_file(), path
    service = _load_service_document()
    assert service["env_vars"]["OBSERVATORY_UI"]["default"] == "legacy"
    assert service["dev"]["command"][:3] == ["sh", "select-ui.sh", "npm"]
    instructions = _dockerfile_instructions()
    assert ("ENV", "PORT=3000") in instructions
    copies = [args for verb, args in instructions if verb == "COPY"]
    assert any("/app/.output" in args for args in copies)
    assert any("/app/replacement/.output" in args for args in copies)


@pytest.mark.parametrize(
    "mode, directory", [(None, ""), ("legacy", ""), ("replacement", "replacement")]
)
def test_ui_selector_uses_packaged_directory_from_any_cwd(tmp_path, mode, directory) -> None:
    package = Path(str(resources.files("phlo_observatory")))
    environment = {key: value for key, value in os.environ.items() if key != "OBSERVATORY_UI"}
    if mode is not None:
        environment["OBSERVATORY_UI"] = mode
    result = subprocess.run(
        ["sh", str(package / "select-ui.sh"), "pwd"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == package / directory


def test_ui_selector_rejects_unknown_mode_before_executing(tmp_path) -> None:
    script = resources.files("phlo_observatory").joinpath("select-ui.sh")
    marker = tmp_path / "must-not-exist"
    result = subprocess.run(
        ["sh", str(script), "touch", str(marker)],
        env={**os.environ, "OBSERVATORY_UI": "../../outside"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 64
    assert not marker.exists()
