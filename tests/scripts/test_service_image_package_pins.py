"""Consumer-built service images must not pin distro package versions.

Generated stacks build these Dockerfiles on the operator's machine from the
committed ``.phlo`` copy, weeks or months after the phlo release. Debian and
Alpine drop superseded versions from their archives at point releases, so an
exact version turns a released image into a build that can never succeed again
(the reported failure is ``E: Version '5.2.37-2+b9' for 'bash' was not found``,
exit code 100, on the Dagster image). PyPI pins are unaffected: PyPI keeps every
published version.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DOCKERFILES = sorted(REPO_ROOT.glob("packages/*/src/*/Dockerfile"))
_INSTALLERS = (
    re.compile(r"\bapt-get\s+install\b"),
    re.compile(r"\bapk\s+add\b"),
)
_PACKAGE_WITH_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*=")
_SHELL_SEPARATOR = re.compile(r"&&|\|\||;")


def _dockerfile_instructions(path: Path) -> list[str]:
    """Return instructions with line continuations joined."""
    instructions: list[str] = []
    pending = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}" if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append(pending)
        pending = ""
    assert not pending, f"{path} ends inside a line continuation"
    return instructions


def _install_segments(instruction: str) -> Iterator[str]:
    """Yield each distro-install argument list, bounded by the next shell separator."""
    for installer in _INSTALLERS:
        for match in installer.finditer(instruction):
            yield _SHELL_SEPARATOR.split(instruction[match.end() :], maxsplit=1)[0]


def test_service_image_dockerfiles_are_discovered() -> None:
    """A broken glob must fail rather than silently assert nothing."""
    assert SERVICE_DOCKERFILES, "no service Dockerfiles discovered"


@pytest.mark.parametrize(
    "dockerfile",
    SERVICE_DOCKERFILES,
    ids=[path.parents[2].name for path in SERVICE_DOCKERFILES],
)
def test_service_image_dockerfiles_do_not_pin_distro_packages(dockerfile: Path) -> None:
    pinned = [
        spec
        for instruction in _dockerfile_instructions(dockerfile)
        for segment in _install_segments(instruction)
        for spec in segment.split()
        if _PACKAGE_WITH_VERSION.match(spec)
    ]
    assert pinned == [], (
        f"{dockerfile.relative_to(REPO_ROOT)} pins distro packages: {', '.join(pinned)}. "
        "Generated stacks build this file months after the phlo release, when the "
        "distro archive no longer serves a superseded version; keep the package "
        "unpinned and satisfy hadolint with `# hadolint ignore=DL3008` (apt) or "
        "`DL3018` (apk)."
    )
