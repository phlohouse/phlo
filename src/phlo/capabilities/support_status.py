"""Offline support-manifest capability.

This module only reads package resources and installed distribution metadata; it
does not contact a registry or alter the environment.

Imported by the phlo CLI support command to render local support status.
"""

from __future__ import annotations

import json
import re
from importlib import metadata, resources
from typing import Any


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_support_manifest() -> dict[str, Any]:
    """Load the support manifest embedded in the installed ``phlo`` wheel."""
    text = resources.files("phlo.support_data").joinpath("v1.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    if manifest.get("manifest_schema") != "phlo.support.v1":
        raise ValueError("unsupported bundled support manifest")
    return manifest


def support_status() -> dict[str, Any]:
    """Evaluate locally installed Phlo distributions against the bundled manifest."""
    try:
        manifest = load_support_manifest()
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        return {
            "compatible": None,
            "production_ready": None,
            "manifest": {
                "source": "bundled",
                "trust": "unavailable",
                "staleness": {"status": "unknown", "reason": str(exc)},
            },
            "items": [],
            "gates": {},
        }

    distributions = {
        _normalize(distribution.metadata["Name"]): distribution.version
        for distribution in metadata.distributions()
        if "Name" in distribution.metadata
    }
    expected_packages = manifest["release_set"]["packages"]
    expected_names = {_normalize(item["name"]) for item in expected_packages}
    items = []
    for package in expected_packages:
        name = package["name"]
        expected = package["version"]
        installed = distributions.get(_normalize(name))
        status = (
            "compatible"
            if installed == expected
            else "missing"
            if installed is None
            else "mismatched"
        )
        items.append(
            {
                "kind": "package",
                "name": name,
                "expected": expected,
                "installed": installed,
                "status": status,
            }
        )
    for normalized, installed in sorted(distributions.items()):
        if normalized.startswith("phlo-") and normalized not in expected_names:
            items.append(
                {
                    "kind": "package",
                    "name": normalized,
                    "expected": None,
                    "installed": installed,
                    "status": "unexpected",
                }
            )

    compatible = all(item["status"] == "compatible" for item in items)
    release = manifest["current_release"]
    return {
        "compatible": compatible,
        "production_ready": release["production_ready"],
        "release": {"version": release["version"], "maturity": release["maturity"]},
        "manifest": {
            "source": "bundled",
            "trust": "trusted",
            "staleness": {
                "status": "unknown",
                "reason": "the bundled v1 manifest has no publication timestamp",
            },
        },
        "items": items,
        "gates": manifest["gates"]["status"],
    }
