#!/usr/bin/env python3
"""Hand authenticated daily scanner findings to the existing issue-based agent intake."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import release_provenance as github


def requests(report: dict, run_url: str) -> dict[str, str]:
    """Deduplicate each advisory/version across all affected consumer locks."""
    if report.get("assessment") != "complete":
        return {
            "security(deps): dependency assessment unavailable": "Repository maintainers own restoring the independent scanner and manually "
            "triaging dependency risk until intake is available. No clean verdict exists.\n\n"
            f"Independent assessment: {run_url}"
        }
    grouped: dict[str, list[str]] = {}
    for finding in report["introduced"] + report["existing"]:
        for advisory in finding["advisories"]:
            title = f"security(deps): {advisory} in {finding['ecosystem']}/{finding['name']} {finding['version']}"
            grouped.setdefault(title, []).append(finding["lockfile"])
    return {
        title: (
            "Phlo agent owns advisory triage, compatibility analysis and coordination with "
            "Renovate. Maintainers own escalation if automation is unavailable. "
            "An agent cannot approve an exception or replace the scanner verdict.\n\n"
            f"Independent assessment: {run_url}\n\nAffected consumer locks:\n"
            + "\n".join(f"- `{path}`" for path in sorted(set(paths)))
        )
        for title, paths in grouped.items()
    }


def publish(report: dict, run: dict) -> None:
    """Reuse an open security issue instead of competing with existing remediation."""
    issues = github.pages(f"repos/{github.REPOSITORY}/issues?state=open&labels=security", "")
    for title, body in requests(report, run["html_url"]).items():
        existing = next(
            (
                issue
                for issue in issues
                if issue.get("title") == title and "pull_request" not in issue
            ),
            None,
        )
        if existing:
            paths = set(re.findall(r"^- `([^`]+)`$", body, re.M))
            old_paths = set(re.findall(r"^- `([^`]+)`$", existing.get("body") or "", re.M))
            if paths - old_paths:
                body = (existing.get("body") or "") + "\n\nAdditional affected consumer locks:\n"
                body += "\n".join(f"- `{path}`" for path in sorted(paths - old_paths))
                subprocess.run(
                    [
                        "gh",
                        "issue",
                        "edit",
                        str(existing["number"]),
                        "--repo",
                        github.REPOSITORY,
                        "--body",
                        body,
                    ],
                    check=True,
                )
            continue
        subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                github.REPOSITORY,
                "--title",
                title,
                "--body",
                body,
                "--label",
                "security",
                "--label",
                "dependencies",
            ],
            check=True,
        )


def handoff(run_id: int, attempt: int, sha: str) -> None:
    """Bind the notification to its exact producer attempt before reading data."""
    run = github.api(f"repos/{github.REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    workflow = github.api(f"repos/{github.REPOSITORY}/actions/workflows/security.yml")
    repository = github.api(f"repos/{github.REPOSITORY}")
    if (
        run.get("event") != "schedule"
        or run.get("head_branch") != "main"
        or repository.get("default_branch") != "main"
        or run.get("id") != run_id
        or run.get("run_attempt") != attempt
        or attempt < 1
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or run.get("head_sha") != sha
        or run.get("repository", {}).get("full_name") != github.REPOSITORY
        or run.get("head_repository", {}).get("full_name") != github.REPOSITORY
        or run.get("workflow_id") != workflow["id"]
        or workflow.get("path") != ".github/workflows/security.yml"
        or run.get("path", "").split("@")[0] != workflow["path"]
        or run.get("status") != "completed"
    ):
        raise ValueError("Only the allowlisted completed daily scan may open remediation issues")
    with tempfile.TemporaryDirectory() as directory:
        try:
            artifact = github.download_artifact(
                run, f"dependency-assessment-{run_id}-{attempt}", Path(directory)
            )
            report = json.loads((artifact / "dependency-assessment.json").read_text())
        except (OSError, ValueError, subprocess.CalledProcessError):
            report = {"assessment": "unavailable"}
    if isinstance(report, dict) and "head" in report and report["head"] != sha:
        raise ValueError("Assessment names a different inspected SHA")
    if not valid_report(report, sha):
        report = {"assessment": "unavailable"}
    publish(report, run)


def valid_report(report: object, sha: str) -> bool:
    """Validate the complete dependency_delta queue report, not a clean-looking fragment."""
    if not isinstance(report, dict) or report.get("assessment") != "complete":
        return False
    if report.get("head") != sha or report.get("base") != sha or report.get("mode") != "queue":
        return False
    if not isinstance(report.get("inventory"), dict):
        return False
    for category in ("introduced", "existing", "unaccepted"):
        if not isinstance(report.get(category), list):
            return False
        for finding in report[category]:
            if not isinstance(finding, dict):
                return False
            if any(
                not isinstance(finding.get(key), str) or not finding[key].strip()
                for key in ("lockfile", "ecosystem", "name", "version")
            ):
                return False
            advisories = finding.get("advisories")
            if not isinstance(advisories, list) or not advisories:
                return False
            if any(not isinstance(item, str) or not item.strip() for item in advisories):
                return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    handoff(args.run_id, args.run_attempt, args.sha)


if __name__ == "__main__":
    main()
