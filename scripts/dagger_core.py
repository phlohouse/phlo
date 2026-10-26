#!/usr/bin/env python3
"""Run one existing core CI shard with Dagger, without a project SDK dependency."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.0.0-beta.21"
ROOT = Path(__file__).resolve().parents[1]


def query(document: str, **variables):
    """Use the CLI's local session API; never forward host credentials to tests."""
    auth = base64.b64encode(f"{os.environ['DAGGER_SESSION_TOKEN']}:".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{os.environ['DAGGER_SESSION_PORT']}/query",
        data=json.dumps({"query": document, "variables": variables}).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(error.read().decode()) from error
    if result.get("errors"):
        raise SystemExit(json.dumps(result["errors"], indent=2))
    return result["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, choices=range(3), required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "test-results/dagger-core")
    args = parser.parse_args()
    if "DAGGER_SESSION_PORT" not in os.environ:
        version = subprocess.check_output(["dagger", "version"], text=True)
        if f"v{VERSION}\n" not in version:
            parser.error(f"Install Dagger {VERSION}; found {version.splitlines()[0]}")
        started = time.monotonic()
        result = subprocess.run(
            [
                "dagger",
                "--progress",
                "plain",
                "api",
                "with-session",
                sys.executable,
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            cwd=ROOT,
            check=False,
        )
        print(f"Dagger end-to-end elapsed: {time.monotonic() - started:.2f}s", flush=True)
        return result.returncode

    # Only tracked working-tree files enter the engine. Ignore local environments,
    # credentials, Git metadata and generated results, even when they exist locally.
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    files = [path for path in tracked if path and (ROOT / path).is_file()]
    snapshot = query(
        """query($path: String!, $files: [String!]!, $manifests: [String!]!) {
          host {
            source: directory(path: $path, include: $files) { id }
            manifests: directory(path: $path, include: $manifests) { id }
          }
        }""",
        path=str(ROOT),
        files=files,
        manifests=[
            path
            for path in files
            if path in {"pyproject.toml", "uv.lock"}
            or Path(path).match("packages/*/pyproject.toml")
        ],
    )["host"]
    source = snapshot["source"]["id"]
    # External dependency installation depends on manifests, not Python/test edits.
    deps = query(
        """query($source: ID!) {
          container { from(address: "mirror.gcr.io/library/python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70") {
            withExec(disableDaggerInDagger: true, args: ["sh", "-ec", "apt-get update && apt-get install -y --no-install-recommends git make jq gcc libpq-dev && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv==0.12.1"]) {
              withWorkdir(path: "/workspace") {
                withDirectory(path: "/workspace", source: $source) {
                  withExec(disableDaggerInDagger: true, args: ["uv", "sync", "--dev", "--locked", "--no-install-workspace"]) { id }
                }
              }
            }
          } }
        }""",
        source=snapshot["manifests"]["id"],
    )["container"]["from"]["withExec"]["withWorkdir"]["withDirectory"]["withExec"]["id"]
    workspace = query(
        """query($deps: ID!, $source: ID!) {
          node(id: $deps) { ... on Container {
            withDirectory(path: "/workspace", source: $source) {
              withExec(disableDaggerInDagger: true, args: ["sh", "-ec", "git init -q && git add . && GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z git -c user.name=Pilot -c user.email=pilot@example.invalid commit -qm snapshot && uv sync --dev --locked"]) { id }
            }
          } }
        }""",
        deps=deps,
        source=source,
    )["node"]["withDirectory"]["withExec"]["id"]
    output = args.output.resolve() / f"shard-{args.shard}"
    output.mkdir(parents=True, exist_ok=True)
    result = query(
        """query($workspace: ID!, $command: [String!]!, $output: String!) {
          node(id: $workspace) { ... on Container {
            withExec(args: $command, expect: ANY, disableDaggerInDagger: true) {
              exitCode
              stdout
              directory(path: "/workspace/test-results") { export(path: $output) }
            }
          } }
        }""",
        workspace=workspace,
        command=["make", "test-core-shard", f"CORE_TEST_SHARD_INDEX={args.shard}"],
        output=str(output),
    )["node"]["withExec"]
    print(result["stdout"], flush=True)
    return result["exitCode"]


if __name__ == "__main__":
    sys.exit(main())
