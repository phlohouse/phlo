"""Runtime contract checks for observatory service packaging.

The bundled container must run unprivileged, ship the docker CLI for status
discovery, and never mount the host Docker socket.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from importlib import resources
from typing import Any

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


def test_observatory_bundles_only_the_api_backed_ui() -> None:
    package = resources.files("phlo_observatory")
    manifest = json.loads(package.joinpath("package.json").read_text(encoding="utf-8"))
    assert "workspaces" not in manifest
    assert manifest["scripts"]["start"] == "node .output/server/index.mjs"
    for path in (
        "vite.config.ts",
        "src/lib/data/api/client.ts",
        "src/routes/healthz.ts",
        "scripts/verify-api-client.mjs",
    ):
        assert package.joinpath(path).is_file(), path
    for path in ("select-ui.sh", "replacement/src", "src/observatory", "src/server"):
        assert not package.joinpath(path).is_file() and not package.joinpath(path).is_dir(), path
    service = _load_service_document()
    assert "OBSERVATORY_UI" not in service["env_vars"]
    assert service["dev"]["command"][:3] == ["npm", "run", "dev"]
    assert service["compose"]["environment"] == {
        "NODE_ENV": "production",
        "HOST": "0.0.0.0",
        "PORT": 3000,
        "PHLO_API_URL": "http://phlo-api:4000",
    }
    assert service["compose"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/healthz",
    ]
    instructions = _dockerfile_instructions()
    assert ("ENV", "PORT=3000") in instructions
    assert ("COPY", "--from=builder /app/.output /app/.output") in instructions
    assert ("CMD", '["node", ".output/server/index.mjs"]') in instructions
    assert not any("replacement" in args or "select-ui" in args for _, args in instructions)
