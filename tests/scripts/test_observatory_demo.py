"""The disposable demo must reject unsafe invocations before starting services."""

import subprocess
import sys
from pathlib import Path


def test_demo_requires_explicit_write_opt_in(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/observatory_demo.py"
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--native-binary",
            str(tmp_path / "absent"),
            "--native-sha256",
            "0" * 64,
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--run-disposable is required" in result.stderr
    assert not report.exists()


def test_demo_rejects_unverified_binary_before_runtime_creation(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/observatory_demo.py"
    binary = tmp_path / "engine"
    binary.write_bytes(b"not the verified native engine")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run-disposable",
            "--native-binary",
            str(binary),
            "--native-sha256",
            "0" * 64,
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Native binary is missing or its SHA256 differs" in result.stderr
    assert not report.exists()
