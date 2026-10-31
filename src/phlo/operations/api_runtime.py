"""Reviewed repair of empty Docker-created API binds, never recursive chown."""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phlo.infrastructure.container_backend import project_container_host_identity

type DirectoryName = Literal["logs", "audit", "state", "observatory"]


class ApiRuntimeDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: DirectoryName
    device: int
    inode: int
    uid: int = Field(ge=0)
    gid: int = Field(ge=0)
    image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    original_uid: Literal[0] = 0
    original_gid: Literal[0] = 0
    original_mode: Literal[0o755] = 0o755


def _directory(root: Path, name: str) -> Path:
    path = root / ".phlo" / name
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("API runtime repair refuses symlinked paths")
    if path.exists() and not path.is_dir():
        raise ValueError("API runtime bind must be a directory")
    return path


def pending_directories(root: Path, service: dict) -> list[DirectoryName]:
    """Find unwritable default binds; personal mounts are not adopted."""
    if platform.system() != "Linux" or not service:
        return []
    identity = project_container_host_identity() or (os.getuid(), os.getgid())
    if service.get("user") != ":".join(map(str, identity)):
        return []
    sources = {}
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            if volume.endswith(":ro"):
                continue
            source, _, target = volume.partition(":")
            sources[target.split(":")[0]] = (root / ".phlo" / source).absolute()
        elif volume.get("type") == "bind" and not volume.get("read_only"):
            sources[volume["target"]] = Path(volume["source"])
    pending: list[DirectoryName] = []
    names: tuple[DirectoryName, ...] = ("logs", "audit", "state", "observatory")
    for name in names:
        source = sources.get(f"/app/.phlo/{name}")
        # Normalize lexical '..' without following a symlink before rejecting it.
        if source is None or Path(os.path.normpath(source)) != root / ".phlo" / name:
            continue
        path = _directory(root, name)
        if not path.exists():
            continue
        info = path.stat()
        permissions = stat.S_IMODE(info.st_mode)
        bits = permissions >> (
            6 if info.st_uid == os.getuid() else 3 if info.st_gid == os.getgid() else 0
        )
        if bits & 3 != 3:
            pending.append(name)
    return pending


def _docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode:
        raise ValueError(
            "API runtime ownership operation failed; private recovery metadata retained"
        )
    return result.stdout.strip()


def _stopped(root: Path) -> None:
    containers = _docker(
        "ps", "--filter", "label=com.docker.compose.service=phlo-api", "-q"
    ).split()
    if not containers:
        return
    for container in json.loads(_docker("inspect", *containers)):
        if any(
            Path(mount["Source"]).is_relative_to(root)
            for mount in container["Mounts"]
            if mount["Type"] == "bind"
        ):
            raise ValueError(
                "Stop the project's API before reviewing or repairing runtime ownership"
            )


def plan_api_runtime(root: Path, document: dict) -> tuple[ApiRuntimeDirectory, ...]:
    if platform.system() != "Linux":
        raise ValueError("Empty API bind ownership repair requires native Linux")
    service = document["services"].get("phlo-api", {})
    pending = pending_directories(root, service)
    if not pending:
        return ()
    candidates = []
    for name in pending:
        path = _directory(root, name)
        info = path.stat()
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o755) or any(
            path.iterdir()
        ):
            raise ValueError(
                "Only empty root-owned 0755 default API binds can be repaired; retain custom data for operator review"
            )
        candidates.append((name, info))
    _stopped(root)
    image = _docker("image", "inspect", "--format", "{{.Id}}", service["image"])
    return tuple(
        ApiRuntimeDirectory(
            name=name,
            device=info.st_dev,
            inode=info.st_ino,
            uid=os.getuid(),
            gid=os.getgid(),
            image=image,
        )
        for name, info in candidates
    )


def verify_api_runtime(
    root: Path, changes: tuple[ApiRuntimeDirectory, ...], *, applied: bool
) -> None:
    for change in changes:
        path = _directory(root, change.name)
        info = path.stat()
        expected = (change.uid, change.gid) if applied else (0, 0)
        if (
            (info.st_dev, info.st_ino) != (change.device, change.inode)
            or (info.st_uid, info.st_gid) != expected
            or stat.S_IMODE(info.st_mode) != 0o755
            or (not applied and any(path.iterdir()))
        ):
            raise ValueError(
                "API runtime directory changed after review; refusing ownership mutation"
            )


def repair_api_runtime(
    root: Path, changes: tuple[ApiRuntimeDirectory, ...], *, rollback: bool = False
) -> None:
    if not changes:
        return
    _stopped(root)
    verify_api_runtime(root, changes, applied=rollback)
    completed = []
    try:
        for change in changes:
            _repair_directory(root, change, rollback=rollback)
            completed.append(change)
        verify_api_runtime(root, changes, applied=not rollback)
    except Exception:
        if not rollback:
            for change in reversed(completed):
                verify_api_runtime(root, (change,), applied=True)
                _repair_directory(root, change, rollback=True)
        raise


def _repair_directory(root: Path, change: ApiRuntimeDirectory, *, rollback: bool) -> None:
    # Bind identity is checked again inside the pinned, networkless helper.
    # No chmod, recursion, copying, deletion or storage relocation occurs.
    script = (
        "import os,json,sys,stat; from pathlib import Path; "
        "p=Path('/repair'); c=json.loads(sys.argv[1]); s=p.stat(); "
        "assert (s.st_dev,s.st_ino)==(c['device'],c['inode']); "
        "assert stat.S_IMODE(s.st_mode)==0o755 and not any(p.iterdir()); "
        "assert (s.st_uid,s.st_gid)==tuple(c['before']); "
        "os.chown(p,*c['after'])"
    )
    before, after = (
        ((change.uid, change.gid), (0, 0)) if rollback else ((0, 0), (change.uid, change.gid))
    )
    _docker(
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=0:0",
        "--cap-drop=ALL",
        "--cap-add=CHOWN",
        "--security-opt=no-new-privileges",
        "--mount",
        f"type=bind,source={_directory(root, change.name)},target=/repair",
        "--entrypoint=python",
        change.image,
        "-c",
        script,
        json.dumps(
            {"device": change.device, "inode": change.inode, "before": before, "after": after}
        ),
    )
