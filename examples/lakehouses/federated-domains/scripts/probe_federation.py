"""Boundary probe for multi-project dbt federation.

Enumerates every dbt project the framework discovers under this example and
prints exactly which single project the runtime would activate. The probe is
read-only: it never builds or materializes anything, it only exercises
``phlo_dbt.discovery.find_dbt_projects`` and ``get_dbt_project_dir`` against
the three domain projects.

Usage::

    uv run python scripts/probe_federation.py            # human-readable report
    uv run python scripts/probe_federation.py --json     # machine-readable
    uv run python scripts/probe_federation.py --check    # verify FEDERATION_FINDINGS.md

Exit code is always 0 unless ``--check`` fails: a probe records a boundary,
it does not gate on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINDINGS_PATH = ROOT / "FEDERATION_FINDINGS.md"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def probe(root: Path = ROOT) -> dict[str, object]:
    """Run discovery against ``root`` without mutating persistent state."""
    from phlo_dbt.discovery import find_dbt_projects, get_dbt_project_dir

    previous_env = os.environ.pop("DBT_PROJECT_DIR", None)
    previous_cwd = Path.cwd()
    try:
        os.chdir(root)
        discovered = find_dbt_projects()
        default_active = get_dbt_project_dir()

        # Demonstrate explicit activation: the documented escape hatch that
        # still permits only ONE active project at a time.
        explicit_candidates: list[dict[str, object]] = []
        for project in discovered:
            os.environ["DBT_PROJECT_DIR"] = str(project)
            activated = get_dbt_project_dir()
            explicit_candidates.append(
                {
                    "dbt_project_dir_env": _relative(project),
                    "activated": _relative(activated),
                    "project_name": _project_name(project),
                }
            )
            os.environ.pop("DBT_PROJECT_DIR", None)
    finally:
        if previous_env is not None:
            os.environ["DBT_PROJECT_DIR"] = previous_env
        os.chdir(previous_cwd)

    return {
        "root": str(root),
        "discovered": [
            {"path": _relative(project), "project_name": _project_name(project)}
            for project in discovered
        ],
        "default_active": _relative(default_active),
        "default_active_is_first_discovered": bool(discovered and default_active == discovered[0]),
        "discovery_order_note": (
            "find_dbt_projects returns rglob hits in lexicographic path order; "
            "get_dbt_project_dir takes the FIRST element. There is no "
            "shallowest-path or priority rule: equal-depth candidates are "
            "broken alphabetically."
        ),
        "explicit_activation": explicit_candidates,
    }


def _project_name(project_dir: Path) -> str | None:
    import yaml

    config_path = project_dir / "dbt_project.yml"
    if not config_path.exists():
        return None
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    name = config.get("name") if isinstance(config, dict) else None
    return str(name) if name else None


def render(report: dict[str, object]) -> str:
    """Render the human-readable probe report."""
    lines: list[str] = []
    lines.append("phlo dbt federation probe")
    lines.append(f"root: {report['root']}")
    lines.append("")
    discovered = report["discovered"]
    assert isinstance(discovered, list)
    lines.append(f"Discovered dbt projects ({len(discovered)}):")
    for entry in discovered:
        assert isinstance(entry, dict)
        lines.append(f"  - {entry['path']} (project: {entry['project_name']})")
    lines.append("")
    lines.append(f"Default activation (no DBT_PROJECT_DIR): {report['default_active']} ACTIVE")
    for entry in discovered:
        assert isinstance(entry, dict)
        marker = "ACTIVE" if entry["path"] == report["default_active"] else "INACTIVE"
        lines.append(f"  - {entry['path']} -> {marker} under single-project activation")
    lines.append("")
    lines.append(f"Discovery order note: {report['discovery_order_note']}")
    lines.append("")
    explicit = report["explicit_activation"]
    assert isinstance(explicit, list)
    lines.append("Explicit activation via DBT_PROJECT_DIR (one at a time only):")
    for entry in explicit:
        assert isinstance(entry, dict)
        lines.append(
            f"  - DBT_PROJECT_DIR={entry['dbt_project_dir_env']} -> {entry['activated']} ACTIVE"
        )
    lines.append("")
    inactive_count = len(discovered) - 1
    lines.append(
        f"Verdict: single-active-project runtime; {inactive_count} of {len(discovered)} "
        "discovered projects remain valid-but-inert artifacts. Product work required "
        "for safe federation is recorded in FEDERATION_FINDINGS.md."
    )
    return "\n".join(lines)


def check_findings(report: dict[str, object]) -> list[str]:
    """Return problems found when verifying FEDERATION_FINDINGS.md."""
    problems: list[str] = []
    if not FINDINGS_PATH.exists():
        problems.append(f"missing findings file: {FINDINGS_PATH}")
        return problems
    text = FINDINGS_PATH.read_text(encoding="utf-8")
    lowered = text.lower()
    discovered = report["discovered"]
    assert isinstance(discovered, list)
    for entry in discovered:
        if entry["path"] not in text:
            problems.append(f"findings file does not mention discovered project {entry['path']}")
    for topic in ("multi-manifest", "namespaced", "lineage", "wap"):
        if topic not in lowered:
            problems.append(f"findings file missing product-work topic: {topic}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify FEDERATION_FINDINGS.md covers every discovered project",
    )
    args = parser.parse_args()

    report = probe()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))

    if args.check:
        problems = check_findings(report)
        if problems:
            for problem in problems:
                print(f"CHECK FAILED: {problem}", file=sys.stderr)
            return 1
        print("CHECK OK: findings file covers every discovered project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
