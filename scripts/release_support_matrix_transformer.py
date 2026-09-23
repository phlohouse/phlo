#!/usr/bin/env python3
"""Run support-matrix generation as a ReleaseX transformer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from generate_reference_docs import support_docs, write_outputs

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    plan = json.load(sys.stdin)
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("unsupported ReleaseX workspace plan")

    rendered: dict[Path, str] = {}
    data, markdown = support_docs(ROOT)
    rendered[ROOT / "docs/reference/support-matrix.md"] = markdown
    rendered[ROOT / "docs/reference/generated/support-matrix.json"] = (
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    changed = write_outputs(rendered, check=False)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "changed_files": sorted(str(path.relative_to(ROOT)) for path in changed),
            }
        )
    )


if __name__ == "__main__":
    main()
