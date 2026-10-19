"""Installed, read-only API preview prerequisite inspection."""

from __future__ import annotations

import argparse
import json

from phlo_api.observatory_api.v1_preview import preview_preflight


def main() -> None:
    """Print safe checks; exit nonzero for unavailable prerequisites."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", help="Configured environment ID; omit to check the full mapping.")
    args = parser.parse_args()
    checks = preview_preflight(args.env)
    print(json.dumps(checks, indent=2))
    if any(check["state"] == "unavailable" for check in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
