"""Change only local synthetic failure flags; never launches a workflow."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["baseline", "schema-failures"])
    args = parser.parse_args()
    enabled = args.mode == "schema-failures"
    path = ROOT / "data/scenario.json"
    if not path.exists():
        parser.error("Generate source fixtures first.")
    path.write_text(
        json.dumps({"bioreactor_schema_drift": enabled, "plate_schema_drift": enabled}, indent=2)
        + "\n"
    )
    print(f"Saved {args.mode}; no workflow was launched.")


if __name__ == "__main__":
    main()
