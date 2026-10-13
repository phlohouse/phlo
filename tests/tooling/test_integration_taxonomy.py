"""An unmarked test under an integration directory must fail collection."""

import subprocess
import sys
from pathlib import Path


def test_integration_directory_requires_marker(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "conftest.py").write_text((root / "conftest.py").read_text())
    integration = tmp_path / "tests/integration"
    integration.mkdir(parents=True)
    test = integration / "test_contract.py"
    test.write_text("def test_missing_marker(): assert True\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(test)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode != 0
    assert "Integration paths require the integration marker" in result.stderr
    test.write_text("import pytest\n@pytest.mark.integration\ndef test_marked(): assert True\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(test), "-m", "integration"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_marked" in result.stdout
