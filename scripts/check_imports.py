"""Canonical enforcing entrypoint for import contracts and the complete AST graph."""

import subprocess
import sys

from check_import_cycles import ROOT, main

if __name__ == "__main__":
    result = subprocess.run(["lint-imports", "--no-cache"], cwd=ROOT, check=False)
    graph_result = main()
    sys.exit(result.returncode or graph_result)
