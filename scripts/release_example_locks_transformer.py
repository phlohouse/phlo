#!/usr/bin/env python3
"""Refresh checked-in example locks after ReleaseX updates package versions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    plan = json.load(sys.stdin)
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("unsupported ReleaseX workspace plan")

    locks = (
        subprocess.check_output(["git", "ls-files", "-z", "examples/**/uv.lock"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    before = {path: (ROOT / path).read_bytes() for path in locks if path}
    # ReleaseX clears HOME and UV_*; keep resolver state outside its file snapshot.
    with tempfile.TemporaryDirectory(prefix="phlo-release-locks-") as temporary:
        env = {**os.environ, "HOME": temporary, "UV_CACHE_DIR": f"{temporary}/cache"}
        for path in before:
            subprocess.run(
                ["uv", "lock", "--directory", str((ROOT / path).parent)],
                cwd=ROOT,
                env=env,
                stdout=sys.stderr,
                check=True,
            )
    changed = sorted(
        path for path, original in before.items() if (ROOT / path).read_bytes() != original
    )
    print(json.dumps({"schema_version": 1, "changed_files": changed}))


if __name__ == "__main__":
    main()
