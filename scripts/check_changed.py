#!/usr/bin/env python3
"""Run selected native checks after make install, make setup-js, or make setup."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys

import select_ci


def commands(selection: dict[str, object]) -> list[list[str]]:
    """Use the selector's lanes without provisioning services or security scanners."""
    checks: list[list[str]] = []
    if selection["python"]:
        checks.append(["make", "lint-python", "format-python", "typecheck-python"])
        checks.extend(
            ["python3", f"scripts/{script}.py"]
            for script in (
                "validate_support_manifest",
                "check_version_drift",
                "check_ci_package_groups",
            )
        )
        if "all" in selection["reasons"]:
            checks.append(["uv", "run", "--locked", "pytest", "-m", "not integration"])
        else:
            for group in selection["groups"]:
                for package in group["packages"].split():
                    checks.append(
                        [
                            "uv",
                            "run",
                            "--locked",
                            "--package",
                            package,
                            "--with-editable",
                            "./packages/phlo-testing",
                            "--with",
                            "pytest",
                            "pytest",
                            "-m",
                            "not integration",
                            "--tb=short",
                            f"packages/{package}/tests",
                        ]
                    )
    if selection["frontend"]:
        checks.append(["make", "lint-ts", "format-ts", "typecheck-ts"])
        checks.extend(
            ["npm", "--prefix", "packages/phlo-observatory/src/phlo_observatory", "run", task]
            for task in ("test", "build")
        )
    if selection["writer"]:
        checks.extend(
            ["npm", "--prefix", "apps/phlo-github-writer", "run", task]
            for task in ("typecheck", "test", "build")
        )
    if selection["plugin"]:
        checks.append(
            ["node", "--experimental-strip-types", "--test", ".amp/plugins/phlo-github/lib.test.ts"]
        )
    if selection["docs"]:
        checks.append(["python3", "scripts/check_markdown_links.py"])
        checks.append(["python3", "scripts/check_adr_index.py"])
        checks.append(["make", "docs-build"])
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    selection = select_ci.select(select_ci.git_paths(args.base, working_tree=True))
    print(
        "Selected: "
        + ", ".join(
            lane
            for lane in ("python", "frontend", "writer", "plugin", "docs", "integration")
            if selection[lane]
        ),
        flush=True,
    )
    for lane, reason in selection["reasons"].items():
        print(f"  {lane}: {reason}", flush=True)
    print(
        "Remaining required CI contracts: container/security scans, dependency risk, "
        "workflow hardening, platform/merge-queue and release gates; "
        + ("selected integration services/tests. " if selection["integration"] else "")
        + "Local success is not CI acceptance. No Docker or scanners are provisioned.",
        flush=True,
    )
    for command in commands(selection):
        print("$ " + shlex.join(command), flush=True)
        if not args.dry_run:
            subprocess.run(command, cwd=select_ci.ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
