"""Required service lanes must fail a genuine unavailable-service selection."""

import os
import runpy
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def test_missing_service_cannot_pass_required_lane(tmp_path):
    # Reserve a loopback port without listening, so the endpoint really refuses
    # connections and cannot accidentally address a developer's running service.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        report = tmp_path / "missing-service.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "scripts.ci_required",
                "-o",
                "addopts=",
                "-m",
                "integration",
                "-rs",
                "packages/phlo-trino/tests/test_integration_trino.py"
                "::TestTrinoIntegrationReal::test_simple_query",
                f"--junitxml={report}",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=dict(
                os.environ,
                TRINO_HOST="127.0.0.1",
                TRINO_PORT=str(unavailable.getsockname()[1]),
                MINIO_HOST="127.0.0.1",
                MINIO_API_PORT=str(unavailable.getsockname()[1]),
                MINIO_ROOT_USER="disposable",
                MINIO_ROOT_PASSWORD="disposable",
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Required suite must run tests without skips or xfails" in result.stdout
    skipped = ET.parse(report).find(".//testcase/skipped")
    assert skipped is not None
    assert "Trino not available" in skipped.attrib["message"]
    summarize = runpy.run_path("scripts/test_summary.py")["summarize"]
    summary = summarize(tmp_path)
    assert "| missing-service.xml | 0 | 1 | 0 |" in summary
    assert "Trino not available" in summary
