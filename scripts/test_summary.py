"""Publish JUnit execution and skip reasons without treating skips as successes."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(directory: Path) -> str:
    """Summarize each named lane and retain every skip/xfail reason."""
    rows = ["| Suite | Executed | Skipped/xfail | Failed/error |", "|---|---:|---:|---:|"]
    reasons = []
    for path in sorted(directory.rglob("*.xml")):
        cases = ET.parse(path).findall(".//testcase")
        skips = [case for case in cases if case.find("skipped") is not None]
        failures = sum(
            case.find("failure") is not None or case.find("error") is not None for case in cases
        )
        rows.append(f"| {path.name} | {len(cases) - len(skips)} | {len(skips)} | {failures} |")
        for case in skips:
            skipped = case.find("skipped")
            assert skipped is not None
            reason = skipped.attrib.get("message", "unknown reason").replace("\n", " ")
            reasons.append(f"- `{path.name}::{case.attrib.get('name', '?')}`: {reason}")
    return "\n".join([*rows, "", "### Skip and xfail reasons", *reasons]) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(summarize(parser.parse_args().directory), end="")
