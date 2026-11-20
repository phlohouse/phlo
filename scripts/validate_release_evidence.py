#!/usr/bin/env python3
"""Validate one canonical Phlo release-candidate evidence bundle.

Exits 0 only when the bundle is canonical, sanitized, checksummed, complete
for every required runtime demonstration, and — when a candidate BOM is
supplied — bound to that BOM's candidate identity and artifact digests.
Several ``--bundle`` arguments are the parts of one golden path that ran on
separate runners; they are merged first, and ``--output`` keeps the result.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_candidate_bom
import release_evidence


def main(argv: list[str] | None = None) -> int:
    """Run the release evidence validator CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, action="append", required=True)
    parser.add_argument("--candidate-bom", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="Write the merged bundle.")
    args = parser.parse_args(argv)

    try:
        parts = [release_evidence.load_bundle(path) for path in args.bundle]
        bundle = parts[0] if len(parts) == 1 else release_evidence.merge_bundles(parts)
        bom = None
        if args.candidate_bom is not None:
            bom = release_candidate_bom.load_bom(args.candidate_bom)
        if args.output is not None:
            release_evidence.write_bundle(bundle, args.output)
        release_evidence.validate_bundle(bundle, bom)
    except (
        release_evidence.EvidenceError,
        release_candidate_bom.BomError,
    ) as exc:
        print(f"release evidence validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"evidence bundle {', '.join(map(str, args.bundle))} is valid: candidate "
        f"{bundle['candidate']['canonical_candidate_digest']} "
        f"conclusion={bundle['conclusion']} "
        f"({len(bundle['demonstrations'])} demonstrations)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
