"""Install plugin packages into the running Phlo project without moving Phlo.

A generated project is a uv project, so a plugin belongs in its
``pyproject.toml`` and lock, where ``uv sync`` keeps it. The installed Phlo
version is held fixed: a plugin whose published release needs another Phlo
version is refused rather than silently changing the Phlo the project runs.

A first-party package is pinned to the version released with this Phlo. One
that is not part of the release is refused, because the resolver would
otherwise settle on an old release written for a different Phlo.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from phlo.logging import get_logger

logger = get_logger(__name__)


class PluginInstallError(RuntimeError):
    """A plugin could not be installed without changing the installed Phlo."""


def installed_phlo_version() -> str:
    return importlib.metadata.version("phlo")


def _release_versions() -> tuple[set[str], dict[str, str]]:
    """Return first-party package names and the versions released with this Phlo."""
    from phlo.capabilities.support_status import load_support_manifest

    manifest = load_support_manifest()
    first_party: set[str] = {canonicalize_name(entry["name"]) for entry in manifest["packages"]}
    released: dict[str, str] = {
        canonicalize_name(entry["name"]): entry["version"]
        for entry in manifest["release_set"]["packages"]
    }
    return first_party, released


def pin_to_release(packages: list[str], version: str) -> list[str]:
    """Pin first-party packages to this release; refuse ones it does not ship.

    Third-party requirements pass through unchanged.
    """
    first_party, released = _release_versions()
    pinned: list[str] = []
    for spec in packages:
        try:
            requirement = Requirement(spec)
        except InvalidRequirement:
            pinned.append(spec)
            continue
        name = canonicalize_name(requirement.name)
        if name not in first_party:
            pinned.append(spec)
            continue
        if name not in released:
            raise PluginInstallError(
                f"{requirement.name} is not published with phlo {version}, so it cannot be "
                "installed into this project. Nothing was changed."
            )
        extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
        pinned.append(f"{requirement.name}{extras}=={released[name]}")
    return pinned


def _locked_version(lock: Path, name: str) -> str | None:
    try:
        document = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for package in document.get("package", []):
        if package.get("name") == name:
            return package.get("version")
    return None


def _project_root(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / "pyproject.toml").is_file() and (directory / "phlo.yaml").is_file():
            return directory
    return None


def _runs_in(root: Path) -> bool:
    """Return whether this interpreter is the project's own environment."""
    try:
        return Path(sys.prefix).resolve() == (root / ".venv").resolve()
    except OSError:
        return False


def _refusal(packages: list[str], version: str) -> PluginInstallError:
    return PluginInstallError(
        f"{', '.join(packages)} has no published release for phlo {version}; "
        "installing it would change the installed phlo. Nothing was changed."
    )


def _add_to_project(root: Path, packages: list[str], version: str, uv: str) -> None:
    pyproject, lock = root / "pyproject.toml", root / "uv.lock"
    saved = {path: path.read_bytes() for path in (pyproject, lock) if path.is_file()}
    # Resolve and lock first; only sync once the lock keeps this phlo.
    result = subprocess.run(
        [uv, "add", "--no-sync", *packages], cwd=root, capture_output=True, text=True
    )
    locked = _locked_version(lock, "phlo") if result.returncode == 0 else None
    if result.returncode != 0 or locked != version:
        for path, content in saved.items():
            path.write_bytes(content)
        if not saved.get(lock) and lock.exists() and result.returncode == 0:
            lock.unlink()
        if result.returncode != 0:
            raise PluginInstallError(
                f"uv could not add {', '.join(packages)}: {result.stderr.strip()[-500:]}"
            )
        raise _refusal(packages, version)
    subprocess.run([uv, "sync", "--inexact"], cwd=root, check=True)


def _install_into_interpreter(packages: list[str], version: str, uv: str | None) -> None:
    # Requiring the installed version keeps it; an editable install satisfies it.
    pin = f"phlo=={version}"
    command = (
        [uv, "pip", "install", "--python", sys.executable, *packages, pin]
        if uv
        else [sys.executable, "-m", "pip", "install", *packages, pin]
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        if "No solution found" in result.stderr or "ResolutionImpossible" in result.stderr:
            raise _refusal(packages, version)
        raise PluginInstallError(result.stderr.strip()[-500:] or "package install failed")


def install_packages(packages: list[str], *, cwd: Path | None = None) -> str:
    """Install ``packages`` for this project and return where they went.

    Returns ``"project"`` when they were added to the project's pyproject and
    lock and this process runs in that environment, ``"project-env"`` when they
    were added to a project whose environment is not this interpreter, and
    ``"interpreter"`` outside a uv project.
    """
    version = installed_phlo_version()
    packages = pin_to_release(packages, version)
    uv = shutil.which("uv")
    root = _project_root((cwd or Path.cwd()).resolve())
    if root is not None and uv is not None:
        _add_to_project(root, packages, version, uv)
        where = "project" if _runs_in(root) else "project-env"
    else:
        _install_into_interpreter(packages, version, uv)
        where = "interpreter"
    importlib.invalidate_caches()
    from phlo.plugins.discovery import refresh

    refresh()
    logger.info("plugin_packages_installed", packages=packages, target=where)
    return where
