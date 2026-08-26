"""List WAP-owned Nessie branches and flag those older than a threshold.

Usage (from this directory, platform stack running):

    uv run python scripts/inspect_branches.py [--older-than-minutes 60]

Every ``pipeline-run-*`` ref is printed with its age. Branches past the
threshold are candidates for the retention cleanup sensor (24-hour default);
the quality_failure and warning_only scenarios intentionally leave retained
branches behind, so this is the audit view an operator reconciles against
``.phlo/wap-reports/``.

Note: pyiceberg's REST catalog (phlo_iceberg.get_catalog) exposes no reference
enumeration API, so ref listing goes through phlo_nessie - the same client the
platform's own WAP sensors use.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from scripts.run_scenario import WAP_BRANCH_PREFIX


def inspect_branches(older_than_minutes: float, base_url: str | None = None) -> int:
    """Print WAP branches with ages; return the count of stale ones."""
    from phlo_nessie.resource import NessieResource  # noqa: PLC0415 - live-stack import

    nessie = NessieResource(base_url) if base_url else NessieResource()
    now = datetime.now(timezone.utc)
    branches = [info for info in nessie.list_branches() if info.name.startswith(WAP_BRANCH_PREFIX)]
    if not branches:
        print("no pipeline-run-* branches present")
        return 0

    stale = 0
    print(f"{'branch':<50} {'age_minutes':>12}  stale")
    for info in sorted(branches, key=lambda item: item.name):
        if info.created_at is None:
            print(f"{info.name:<50} {'unknown':>12}  -")
            continue
        age_minutes = (now - info.created_at).total_seconds() / 60.0
        is_stale = age_minutes >= older_than_minutes
        stale += int(is_stale)
        marker = "yes" if is_stale else ""
        print(f"{info.name:<50} {age_minutes:>12.1f}  {marker}")
    print(f"\n{len(branches)} branch(es), {stale} older than {older_than_minutes:g} min")
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than-minutes", type=float, default=60.0)
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args(argv)
    inspect_branches(args.older_than_minutes, args.base_url)
    # Inspection never fails the caller on stale refs; retention is advisory.
    return 0


if __name__ == "__main__":
    sys.exit(main())
