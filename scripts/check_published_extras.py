#!/usr/bin/env python3
"""Fail when a release-set distribution declares an extra that PyPI cannot satisfy.

Inside the workspace, ``[tool.uv.sources]`` redirects phlo-family requirements
to local paths, so a requirement on a package that was never published still
installs. Users installing from PyPI get no such redirect. This check reads the
built wheel metadata of every release-set package and resolves the bare
distribution plus each declared extra with workspace sources disabled. The
only non-PyPI input is the release set's own wheels, which this release is
about to publish.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_MANIFEST_PATH = ROOT / "registry" / "support" / "v1.json"
DEFAULT_INDEX = "https://pypi.org/simple"


@dataclass(frozen=True)
class Wheel:
    """A built wheel and the metadata fields this check needs."""

    path: Path
    name: str
    version: str
    extras: tuple[str, ...]


@dataclass(frozen=True)
class Failure:
    """A requirement that did not resolve and the resolver's explanation."""

    requirement: str
    detail: str


Resolver = Callable[[str, Path], str | None]


def normalize(name: str) -> str:
    """Normalize a distribution name per PEP 503."""
    return name.lower().replace("_", "-").replace(".", "-")


def release_set(manifest: Path = SUPPORT_MANIFEST_PATH) -> set[str]:
    """Names of the distributions this release publishes."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    names = {normalize(entry["name"]) for entry in data["release_set"]["packages"]}
    if not names:
        raise ValueError(f"{manifest} release_set declares no packages")
    return names


def read_wheel(path: Path) -> Wheel:
    """Read name, version and extras from a wheel's METADATA."""
    with zipfile.ZipFile(path) as archive:
        metadata_name = next(
            (
                member
                for member in archive.namelist()
                if member.count("/") == 1 and member.endswith(".dist-info/METADATA")
            ),
            None,
        )
        if metadata_name is None:
            raise ValueError(f"{path.name} has no .dist-info/METADATA")
        metadata = HeaderParser().parsestr(archive.read(metadata_name).decode("utf-8"))
    return Wheel(
        path=path,
        name=metadata["Name"],
        version=metadata["Version"],
        extras=tuple(sorted(set(metadata.get_all("Provides-Extra") or []))),
    )


def release_wheels(distributions: Path, names: set[str]) -> list[Wheel]:
    """Release-set wheels in the build output; fail if any release package is missing."""
    wheels = [read_wheel(path) for path in sorted(distributions.glob("*.whl"))]
    selected = [wheel for wheel in wheels if normalize(wheel.name) in names]
    missing = names - {normalize(wheel.name) for wheel in selected}
    if missing:
        raise ValueError(f"no built wheel for release-set packages: {', '.join(sorted(missing))}")
    return selected


def requirements(wheels: Iterable[Wheel]) -> list[str]:
    """One pinned requirement for each wheel, bare and with each of its extras."""
    result = []
    for wheel in wheels:
        result.append(f"{wheel.name}=={wheel.version}")
        result.extend(f"{wheel.name}[{extra}]=={wheel.version}" for extra in wheel.extras)
    return result


def uv_resolver(index_url: str, python_version: str | None) -> Resolver:
    """Resolve one requirement with uv against index_url plus local release wheels."""

    def resolve(requirement: str, find_links: Path) -> str | None:
        command = [
            "uv",
            "pip",
            "compile",
            "--no-config",
            "--no-sources",
            "--no-cache",
            "--default-index",
            index_url,
            "--find-links",
            str(find_links),
            "-",
        ]
        if python_version:
            command[3:3] = ["--python-version", python_version]
        # Run outside the checkout so no project or workspace configuration applies.
        result = subprocess.run(
            command,
            input=requirement + "\n",
            capture_output=True,
            text=True,
            cwd=find_links,
            check=False,
        )
        if result.returncode == 0:
            return None
        return (result.stderr or result.stdout).strip()

    return resolve


def check(distributions: Path, names: set[str], resolve: Resolver) -> list[Failure]:
    """Resolve every release-set distribution and extra; return the failures."""
    wheels = release_wheels(distributions, names)
    with tempfile.TemporaryDirectory(prefix="phlo-release-wheels-") as directory:
        find_links = Path(directory)
        # Only release-set wheels may stand in for PyPI; other workspace builds may not.
        for wheel in wheels:
            shutil.copy2(wheel.path, find_links / wheel.path.name)
        failures = []
        for requirement in requirements(wheels):
            detail = resolve(requirement, find_links)
            if detail is not None:
                failures.append(Failure(requirement, detail))
    return failures


def main(argv: list[str] | None = None) -> int:
    """Run the check and report every unresolvable requirement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distributions", type=Path, required=True, help="Directory of built wheels."
    )
    parser.add_argument("--manifest", type=Path, default=SUPPORT_MANIFEST_PATH)
    parser.add_argument("--index-url", default=DEFAULT_INDEX)
    parser.add_argument("--python-version", help="Target Python version for resolution.")
    args = parser.parse_args(argv)

    names = release_set(args.manifest)
    failures = check(args.distributions, names, uv_resolver(args.index_url, args.python_version))
    if failures:
        for failure in failures:
            print(f"::error::{failure.requirement} does not resolve from {args.index_url}")
            print(failure.detail)
        print(
            f"{len(failures)} published requirement(s) cannot be installed from {args.index_url}.",
            file=sys.stderr,
        )
        return 1
    print(f"Every release-set distribution and extra resolves from {args.index_url}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
