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
from datetime import UTC, datetime, timedelta
from pathlib import Path

Package = tuple[str, str, str]
LOCKFILE_ALIASES = {
    "apps/phlo-github-writer/package-lock.json": ("apps/phlo-agent/package-lock.json",),
}


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


def verify_risk_review(record: dict) -> None:
    """Require an actual maintainer approval of the exact recorded exception."""
    match = re.fullmatch(r"https://github.com/phlohouse/phlo/pull/(\d+)", record["review_url"])
    if match is None:
        raise ValueError("Risk review must name a repository pull request")
    endpoint = f"repos/phlohouse/phlo/pulls/{match[1]}"

    def api(path: str, *, paginate: bool = False):
        args = ["gh", "api", path]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        return json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)

    pull = api(endpoint)
    if not pull.get("merged_at") or pull["base"]["ref"] != "main":
        raise ValueError("Risk exception must have merged through protected main")
    head = pull["head"]["sha"]
    ledger = json.loads(
        subprocess.run(
            ["git", "show", f"{head}:security/dependency-risks.json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if record not in ledger:
        raise ValueError("Approved revision does not contain this exact risk record")
    permission = api(f"repos/phlohouse/phlo/collaborators/{record['reviewed_by']}/permission")
    if permission.get("permission") not in {"write", "maintain", "admin"}:
        raise ValueError("Risk approver is not a repository maintainer")
    reviews = [
        review for page in api(endpoint + "/reviews?per_page=100", paginate=True) for review in page
    ]
    reviewer = record["reviewed_by"]
    latest = [
        review
        for review in reviews
        if review.get("user", {}).get("login") == reviewer and review.get("state") != "COMMENTED"
    ]
    if not latest:
        raise ValueError("No authenticated human approval for the exception")
    review = max(latest, key=lambda item: item["id"])
    if (
        review.get("state") != "APPROVED"
        or review.get("commit_id") != head
        or review["user"].get("type") != "User"
    ):
        raise ValueError("Exception was not approved at its exact final revision")


def assess_existing(findings: list[dict], ledger: Path, now: datetime | None = None) -> list[dict]:
    """Accept only exact, human-reviewed, time-bounded existing risks."""
    now = now or datetime.now(UTC)
    records = json.loads(ledger.read_text())
    if not isinstance(records, list):
        raise ValueError("Risk ledger must be a list")
    accepted = {}
    windows = {"critical": 1, "high": 7, "moderate": 30, "low": 30}
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
            "reviewed_by",
            "review_url",
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
        if (
            record["reviewed_by"] == record["owner"]
            or re.search(r"agent|\[bot\]", record["reviewed_by"], re.I)
            or not record["review_url"].startswith("https://github.com/phlohouse/phlo/pull/")
        ):
            raise ValueError("Independent human review is required")
        first = datetime.fromisoformat(record["first_seen"])
        expires = datetime.fromisoformat(record["expires"])
        days = 1 if record["exploited"] else windows[record["severity"]]
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
                verify_risk_review(record)
                with urllib.request.urlopen(
                    "https://api.osv.dev/v1/vulns/" + urllib.parse.quote(advisory, safe=""),
                    timeout=60,
                ) as response:
                    detail = json.load(response)
                severity = detail.get("database_specific", {}).get("severity", "unknown").lower()
                if severity == "medium":
                    severity = "moderate"
                if severity not in windows or severity != record["severity"]:
                    raise ValueError(f"Unconfirmed severity for {advisory}")
            else:
                unaccepted.append({**finding, "advisories": [advisory]})
    return unaccepted


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
            status = int(bool(report["introduced"] or report["existing"]))
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
