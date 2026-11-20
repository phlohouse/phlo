"""Compare locked PyPI/npm inventories using shared OSV package-version results.

Never install or execute either revision. Existing package versions share one
query result across base and head, so advisory churn cannot make them a delta.
All newly introduced vulnerable versions block, regardless of severity.

Release mode assesses only the lockfiles whose packages ship in the release,
and blocks fixable critical or high findings there that no active waiver in
``security/dependency-risks.json`` covers (ADR 0063).
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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

Package = tuple[str, str, str]
LOCKFILE_ALIASES = {
    "apps/phlo-github-writer/package-lock.json": ("apps/phlo-agent/package-lock.json",),
}
# Lockfiles whose resolved packages ship in release wheels and images. Example
# projects and the separately deployed GitHub writer do not ship in a release.
SHIPPED_LOCKFILES = frozenset(
    {
        "uv.lock",
        "packages/phlo-observatory/src/phlo_observatory/package-lock.json",
    }
)
# Longest waiver a maintainer may grant, in days from first_seen.
WAIVER_DAYS = {"critical": 7, "high": 30, "moderate": 90, "low": 90}
EXPLOITED_WAIVER_DAYS = 7
RELEASE_BLOCKING = frozenset({"critical", "high", "unknown"})


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
            elif not ({"editable", "virtual", "directory", "git"} & source.keys()):
                # Git/workspace sources carry no PyPI name+version OSV can
                # assess — excluded like workspace links.
                raise ValueError(f"Unsupported non-registry dependency in {path}")
    else:
        lock = json.loads(content)
        if lock.get("lockfileVersion") not in (2, 3):
            raise ValueError(f"Unsupported npm lockfile version in {path}")
        workspaces = lock["packages"].get("", {}).get("workspaces", [])
        for location, package in lock["packages"].items():
            if not location or package.get("link") or location in workspaces:
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
    return packages


def inventory(ref: str) -> dict[str, set[Package]]:
    """Read each product separately so moving a vulnerability isn't grandfathered."""
    if not re.fullmatch(r"[a-fA-F0-9]{40}", ref):
        raise ValueError("Expected an exact 40-character commit SHA")
    result = {}
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", ref], capture_output=True, check=True
    )
    for path in tree.stdout.decode().split("\0"):
        if Path(path).name not in {"uv.lock", "package-lock.json", "npm-shrinkwrap.json"}:
            continue
        content = subprocess.run(
            ["git", "show", f"{ref}:{path}"], capture_output=True, check=True
        ).stdout
        canonical = next(
            (name for name, aliases in LOCKFILE_ALIASES.items() if path in aliases), path
        )
        if canonical in result:
            raise ValueError(f"Duplicate historical lock alias: {canonical}")
        result[canonical] = parse_lock(path, content)
    if not result:
        raise ValueError(f"No tracked lockfiles at {ref}")
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
            if not isinstance(record, dict):
                raise ValueError("Malformed OSV result")
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


def advisory_detail(advisory: str) -> dict:
    """Read one OSV advisory, failing closed on any error."""
    with urllib.request.urlopen(
        "https://api.osv.dev/v1/vulns/" + urllib.parse.quote(advisory, safe=""), timeout=60
    ) as response:
        detail = json.load(response)
    if not isinstance(detail, dict):
        raise ValueError(f"Malformed OSV advisory {advisory}")
    return detail


def advisory_severity(detail: dict) -> str:
    """Return critical, high, moderate, low or unknown from OSV database data."""
    severity = str(detail.get("database_specific", {}).get("severity", "unknown")).lower()
    if severity == "medium":
        severity = "moderate"
    return severity if severity in WAIVER_DAYS else "unknown"


def rated_severity(data: dict, detail: Callable[[str], dict]) -> str:
    """Rate an advisory, reading a GHSA alias when the record has no severity.

    PYSEC records usually carry no severity; their GHSA alias does.
    """
    severity = advisory_severity(data)
    for alias in data.get("aliases", []) or []:
        if severity != "unknown":
            break
        if str(alias).startswith("GHSA-"):
            severity = advisory_severity(detail(alias))
    return severity


def advisory_fixable(detail: dict, ecosystem: str, name: str) -> bool:
    """True when OSV records a fixed version for this package."""
    for affected in detail.get("affected", []) or []:
        package = affected.get("package", {})
        if package.get("ecosystem") != ecosystem or package.get("name", "").lower() != name.lower():
            continue
        for ranges in affected.get("ranges", []) or []:
            if any("fixed" in event for event in ranges.get("events", []) or []):
                return True
    return False


def assess_existing(
    findings: list[dict],
    ledger: Path,
    now: datetime | None = None,
    detail: Callable[[str], dict] | None = None,
) -> list[dict]:
    """Accept only exact, time-bounded waivers merged to main by a maintainer.

    The ledger is read from the trusted main checkout, so merging the waiver is
    its approval. The maintainer may approve their own waiver (ADR 0063).
    """
    now = now or datetime.now(UTC)
    detail = detail or advisory_detail
    records = json.loads(ledger.read_text())
    if not isinstance(records, list):
        raise ValueError("Risk ledger must be a list")
    accepted = {}
    windows = WAIVER_DAYS
    needed = {
        tuple(finding[field] for field in ("lockfile", "ecosystem", "name", "version"))
        + (advisory,)
        for finding in findings
        for advisory in finding["advisories"]
    }
    for record in records:
        required = {
            "lockfile",
            "ecosystem",
            "name",
            "version",
            "advisory",
            "severity",
            "exploited",
            "owner",
            "rationale",
            "first_seen",
            "expires",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError("Malformed risk record")
        if any(
            not isinstance(record[key], str) or not record[key].strip()
            for key in required - {"exploited"}
        ):
            raise ValueError("Risk fields must be nonempty strings")
        if type(record["exploited"]) is not bool or record["severity"] not in windows:
            raise ValueError("Unknown severity or exploitation status")
        if re.search(r"agent|\[bot\]", record["owner"], re.I):
            raise ValueError("A waiver must be owned by a human maintainer")
        first = datetime.fromisoformat(record["first_seen"])
        expires = datetime.fromisoformat(record["expires"])
        days = EXPLOITED_WAIVER_DAYS if record["exploited"] else windows[record["severity"]]
        if (
            first.tzinfo is None
            or expires.tzinfo is None
            or not first < expires <= first + timedelta(days=days)
        ):
            raise ValueError("Expired or excessive risk window")
        key = tuple(
            record[field] for field in ("lockfile", "ecosystem", "name", "version", "advisory")
        )
        if key in accepted:
            raise ValueError("Duplicate risk record")
        if key in needed and not first <= now < expires:
            raise ValueError("Expired risk window")
        accepted[key] = record
    unaccepted = []
    for finding in findings:
        for advisory in finding["advisories"]:
            key = tuple(
                finding[field] for field in ("lockfile", "ecosystem", "name", "version")
            ) + (advisory,)
            record = accepted.get(key)
            if record is not None:
                severity = rated_severity(detail(advisory), detail)
                if severity != record["severity"]:
                    raise ValueError(f"Unconfirmed severity for {advisory}")
            else:
                unaccepted.append({**finding, "advisories": [advisory]})
    return unaccepted


def release_blocking(
    findings: list[dict], detail: Callable[[str], dict] | None = None
) -> list[dict]:
    """Keep fixable critical, high or unrated advisories in shipped lockfiles."""
    detail = detail or advisory_detail
    blocking = []
    for finding in findings:
        if finding["lockfile"] not in SHIPPED_LOCKFILES:
            continue
        for advisory in finding["advisories"]:
            data = detail(advisory)
            severity = rated_severity(data, detail)
            if severity in RELEASE_BLOCKING and advisory_fixable(
                data, finding["ecosystem"], finding["name"]
            ):
                blocking.append({**finding, "advisories": [advisory], "severity": severity})
    return blocking


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("delta", "queue", "release"), default="delta")
    parser.add_argument("--ledger", type=Path, default=Path("security/dependency-risks.json"))
    args = parser.parse_args()
    report = {}
    try:
        base, head = inventory(args.base), inventory(args.head)
        packages = sorted(set().union(*base.values(), *head.values()))
        results = query_osv(packages)
        report = compare(base, head, results)
        report.update(
            base=args.base,
            head=args.head,
            assessment="complete",
            mode=args.mode,
            inventory={path: sorted(packages) for path, packages in head.items()},
        )
        status = 1 if report["introduced"] else 0
        if args.mode == "release":
            shipped = release_blocking(report["introduced"] + report["existing"])
            report["release_blocking"] = assess_existing(shipped, args.ledger)
            status = int(bool(report["release_blocking"]))
        elif args.mode == "queue":
            report["unaccepted"] = assess_existing(report["existing"], args.ledger)
            status = int(bool(report["introduced"] or report["unaccepted"]))
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        report.update(assessment="unavailable", error=str(error))
        status = 2
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
