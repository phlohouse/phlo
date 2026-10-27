#!/usr/bin/env python3
"""Hand authenticated daily scanner findings to the existing issue-based agent intake."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from time import monotonic, sleep

import release_provenance as github

PRODUCER = "security / dependency assessment"
SECURITY_WORKFLOW = ".github/workflows/security.yml"
# GitHub can list a run before it lists the job that run just finished, so poll.
PRODUCER_WAIT_SECONDS = 120
PRODUCER_POLL_SECONDS = 5


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
    """Reuse a matching advisory PR or security issue before creating agent intake."""
    issues = github.pages(f"repos/{github.REPOSITORY}/issues?state=open&labels=security", "")
    pulls = github.pages(f"repos/{github.REPOSITORY}/pulls?state=open", "")
    for title, body in requests(report, run["html_url"]).items():
        matching = _matching_pull(title, pulls)
        if matching:
            comments = github.pages(
                f"repos/{github.REPOSITORY}/issues/{matching['number']}/comments", ""
            )
            recorded = "\n".join(
                comment.get("body") or ""
                for comment in comments
                if title in (comment.get("body") or "")
            )
            if not recorded or _consumer_paths(body) - _consumer_paths(recorded):
                subprocess.run(
                    [
                        "gh",
                        "pr",
                        "comment",
                        str(matching["number"]),
                        "--repo",
                        github.REPOSITORY,
                        "--body",
                        f"{title}\n\n{body}",
                    ],
                    check=True,
                )
            continue
        existing = next(
            (
                issue
                for issue in issues
                if issue.get("title") == title and "pull_request" not in issue
            ),
            None,
        )
        if existing:
            paths = _consumer_paths(body)
            old_paths = _consumer_paths(existing.get("body") or "")
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


def _consumer_paths(body: str) -> set[str]:
    return set(re.findall(r"^- `([^`]+)`$", body, re.M))


def _matching_pull(title: str, pulls: list[dict]) -> dict | None:
    """An ordinary version bump is not proof that a PR addresses this advisory."""
    finding = re.fullmatch(r"security\(deps\): (\S+) in ([^/\s]+)/(\S+) (\S+)", title)
    if not finding:
        return None
    advisory, ecosystem, package, _version = finding.groups()
    # Include ecosystem-qualified names, but do not match another scoped package.
    package_pattern = rf"(?<![\w@./-])(?:{re.escape(ecosystem)}/)?{re.escape(package)}(?![\w./-])"
    advisory_pattern = rf"(?<![\w-]){re.escape(advisory)}(?![\w-])"
    for pull in pulls:
        text = f"{pull.get('title', '')}\n{pull.get('body') or ''}"
        if re.search(advisory_pattern, text) and re.search(package_pattern, text):
            return pull
    return None


def handoff(run_id: int, attempt: int, sha: str) -> None:
    """Bind the notification to its exact producer attempt before reading data."""
    run = github.api(f"repos/{github.REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    workflow = github.api(f"repos/{github.REPOSITORY}/actions/workflows/security.yml")
    repository = github.api(f"repos/{github.REPOSITORY}")
    rejected = _allowlist_failures(run, workflow, repository, run_id, attempt, sha)
    if rejected:
        raise _refusal("; ".join(rejected))
    _await_producer(run_id, attempt, sha)
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


def _refusal(reason: str) -> ValueError:
    """Refuse intake, naming the predicate and the values it observed."""
    return ValueError(
        "Only an allowlisted daily scan with one completed dependency producer may "
        f"open remediation issues: {reason}"
    )


def _allowlist_failures(
    run: dict, workflow: dict, repository: dict, run_id: int, attempt: int, sha: str
) -> list[str]:
    """Name every allowlist predicate that failed and the value that failed it."""
    facts = [
        ("event", run.get("event"), "schedule"),
        ("head_branch", run.get("head_branch"), "main"),
        ("default_branch", repository.get("default_branch"), "main"),
        ("run_id", run.get("id"), run_id),
        ("run_attempt", run.get("run_attempt"), attempt),
        ("head_sha", run.get("head_sha"), sha),
        ("repository.full_name", run.get("repository", {}).get("full_name"), github.REPOSITORY),
        (
            "head_repository.full_name",
            run.get("head_repository", {}).get("full_name"),
            github.REPOSITORY,
        ),
        ("workflow_id", run.get("workflow_id"), workflow.get("id")),
        ("workflow.path", workflow.get("path"), SECURITY_WORKFLOW),
        ("run.path", run.get("path", "").split("@")[0], SECURITY_WORKFLOW),
    ]
    failures = [
        f"{name}={observed!r} (expected {expected!r})"
        for name, observed, expected in facts
        if observed != expected
    ]
    if attempt < 1:
        failures.append(f"run_attempt={attempt!r} (expected at least 1)")
    if run.get("status") not in {"in_progress", "completed"}:
        failures.append(f"status={run.get('status')!r} (expected 'in_progress' or 'completed')")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        failures.append(f"head_sha={sha!r} (expected a full lowercase commit SHA)")
    return failures


def _await_producer(run_id: int, attempt: int, sha: str) -> None:
    """Wait out list lag, but judge a completed job's immutable identity immediately."""
    deadline = monotonic() + PRODUCER_WAIT_SECONDS
    while True:
        jobs = github.pages(
            f"repos/{github.REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"
        )
        producers = [job for job in jobs if job.get("name") == PRODUCER]
        completed = [job for job in producers if job.get("status") == "completed"]
        if completed:
            if len(completed) > 1:
                raise _refusal(
                    f"{len(completed)} completed producers: {_producer_states(completed)}"
                )
            producer = completed[0]
            if (
                producer.get("run_id") != run_id
                or producer.get("run_attempt") != attempt
                or producer.get("head_sha") != sha
            ):
                raise _refusal(_producer_states(completed))
            return
        if monotonic() >= deadline:
            raise _refusal(
                f"no producer completed within {PRODUCER_WAIT_SECONDS}s: {_producer_states(producers)}"
            )
        sleep(PRODUCER_POLL_SECONDS)


def _producer_states(producers: list[dict]) -> str:
    """Report the producer states observed, so a refusal is diagnosable."""
    if not producers:
        return f"no job named {PRODUCER!r}"
    return ", ".join(
        f"status={job.get('status')!r} run_id={job.get('run_id')!r} "
        f"run_attempt={job.get('run_attempt')!r} head_sha={job.get('head_sha')!r}"
        for job in producers
    )


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
