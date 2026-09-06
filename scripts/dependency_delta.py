"""Compare locked PyPI/npm inventories using shared OSV package-version results.

Never install or execute either revision. Existing package versions share one
query result across base and head, so advisory churn cannot make them a delta.
All newly introduced vulnerable versions block, regardless of severity.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

Package = tuple[str, str, str]
LOCKFILES = (
    "uv.lock",
    "packages/phlo-observatory/src/phlo_observatory/package-lock.json",
    "apps/phlo-agent/package-lock.json",
)


def parse_lock(path: str, content: bytes) -> set[Package]:
    """Read registry versions, excluding workspace links and the npm root."""
    packages: set[Package] = set()
    if path.endswith("uv.lock"):
        for package in tomllib.loads(content.decode())["package"]:
            source = package["source"]
            if "registry" in source:
                if source["registry"].rstrip("/") != "https://pypi.org/simple":
                    raise ValueError(f"Unsupported Python registry in {path}")
                name = re.sub(r"[-_.]+", "-", package["name"]).lower()
                packages.add(("PyPI", name, package["version"]))
            elif not ({"editable", "virtual", "directory"} & source.keys()):
                raise ValueError(f"Unsupported non-registry dependency in {path}")
    else:
        lock = json.loads(content)
        if lock.get("lockfileVersion") not in (2, 3):
            raise ValueError(f"Unsupported npm lockfile version in {path}")
        for location, package in lock["packages"].items():
            if not location or package.get("link"):
                continue
            if "node_modules/" not in location:
                raise ValueError(f"Unsupported package location in {path}: {location}")
            name = package.get("name") or location.rsplit("node_modules/", 1)[1]
            version = package["version"]
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].+)?", version):
                raise ValueError(f"Unsupported npm version in {path}: {name}")
            # Bundled packages inherit their registry parent's provenance.
            origin, origin_location = package, location
            while not origin.get("resolved") and origin.get("inBundle"):
                if "/node_modules/" not in origin_location:
                    raise ValueError(f"Missing npm bundle parent in {path}")
                origin_location = origin_location.rsplit("/node_modules/", 1)[0]
                origin = lock["packages"][origin_location]
            url = urllib.parse.urlsplit(origin.get("resolved", ""))
            if url.scheme != "https" or url.netloc != "registry.npmjs.org":
                raise ValueError(f"Unsupported npm source in {path}: {name}")
            packages.add(("npm", name, version))
    if not packages:
        raise ValueError(f"No registry dependencies found in {path}")
    return packages


def inventory(ref: str) -> dict[str, set[Package]]:
    """Read each product separately so moving a vulnerability isn't grandfathered."""
    if not re.fullmatch(r"[a-fA-F0-9]{40}", ref):
        raise ValueError("Expected an exact 40-character commit SHA")
    result = {}
    for path in LOCKFILES:
        content = subprocess.run(
            ["git", "show", f"{ref}:{path}"], check=True, capture_output=True
        ).stdout
        result[path] = parse_lock(path, content)
    return result


def query_osv(packages: list[Package]) -> dict[Package, list[str]]:
    """Query every unique version once; abort on incomplete or paginated data."""
    results = {}
    for start in range(0, len(packages), 500):
        batch = packages[start : start + 500]
        payload = {
            "queries": [
                {"package": {"ecosystem": ecosystem, "name": name}, "version": version}
                for ecosystem, name, version in batch
            ]
        }
        request = urllib.request.Request(
            "https://api.osv.dev/v1/querybatch",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    records = json.load(response)["results"]
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        if len(records) != len(batch):
            raise ValueError("Incomplete OSV batch response")
        for package, record in zip(batch, records, strict=True):
            if record.get("next_page_token") or record.get("error"):
                raise ValueError("OSV returned incomplete vulnerability data")
            results[package] = sorted({entry["id"] for entry in record.get("vulns", [])})
    return results


def compare(base: dict, head: dict, results: dict) -> dict:
    """Classify findings per lockfile; changed vulnerable versions are new risk."""
    report = {"introduced": [], "existing": []}
    for path, packages in head.items():
        for package in sorted(packages):
            if not results[package]:
                continue
            category = "existing" if package in base.get(path, set()) else "introduced"
            report[category].append(
                {
                    "lockfile": path,
                    "ecosystem": package[0],
                    "name": package[1],
                    "version": package[2],
                    "advisories": results[package],
                }
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        base, head = inventory(args.base), inventory(args.head)
        packages = sorted(set().union(*base.values(), *head.values()))
        results = query_osv(packages)
        report = compare(base, head, results)
        report.update(base=args.base, head=args.head, assessment="complete")
        status = 1 if report["introduced"] else 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        report = {"assessment": "unavailable", "error": str(error)}
        status = 2
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
