#!/usr/bin/env python3
"""Print the shared ReleaseX ``--next-version`` for a lockstep release.

ADR 0053 decision 4: from 1.0.0 every blessed_core package shares one version.
ReleaseX versions each package from its own commits, so the release workflow
passes this value as ``--next-version``, which ReleaseX applies to every selected
package. Below 1.0.0, and until the support manifest claims production
readiness, the value is empty and packages keep independent versions.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Callable
from typing import Any

import validate_support_manifest as support


def lockstep_version(
    phlo_version: str,
    production_ready: bool,
    plan: Callable[[], list[dict[str, Any]]],
) -> str:
    """Return the set-wide version, or an empty string for independent versioning."""
    released = support._version_is_at_least_1_0(phlo_version)
    if not (released or production_ready):
        return ""
    phlo = next(package for package in plan() if package["name"] == "phlo")
    if not phlo["selected"] or not phlo["next_version"]:
        return ""
    # The root package owns every path, so its bump is the largest in the set.
    return phlo["next_version"] if released else "1.0.0"


def _relx_plan() -> list[dict[str, Any]]:
    output = subprocess.run(
        ["relx", "release", "plan", "--json"], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(output)["packages"]


def main() -> None:
    """Write ``version=<shared version>`` for ``$GITHUB_OUTPUT``."""
    with (support.ROOT / "pyproject.toml").open("rb") as handle:
        phlo_version = tomllib.load(handle)["project"]["version"]
    manifest = json.loads(support.MANIFEST_PATH.read_text(encoding="utf-8"))
    production_ready = manifest["current_release"]["production_ready"]
    print(f"version={lockstep_version(phlo_version, production_ready, _relx_plan)}")


if __name__ == "__main__":
    main()
