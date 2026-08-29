#!/usr/bin/env python3
"""Generate the package-bundled plugin registry from canonical authoring data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REGISTRY = ROOT / "registry" / "plugins.json"
BUNDLED_REGISTRY = ROOT / "src" / "phlo" / "plugins" / "registry_data.json"


def generate() -> str:
    """Copy canonical registry content into the wheel-packaged location."""
    return CANONICAL_REGISTRY.read_text(encoding="utf-8")


def main() -> int:
    """Write generated registry data, or verify that it is current."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated data is stale")
    args = parser.parse_args()
    generated = generate()

    if args.check:
        if BUNDLED_REGISTRY.read_text(encoding="utf-8") != generated:
            print(f"{BUNDLED_REGISTRY.relative_to(ROOT)} is stale; run {Path(__file__).name}.")
            return 1
        return 0

    BUNDLED_REGISTRY.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
