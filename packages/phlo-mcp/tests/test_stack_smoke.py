"""Pytest wrapper for the live phlo-mcp stack smoke script.

Locates the bundled smoke stack spec and asserts durable-asset seeding,
project-root argument handling, and non-conflicting default ports that still
respect user overrides.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SMOKE_STACK_PATH = Path(__file__).with_name("smoke_stack.py")
_SMOKE_STACK_SPEC = importlib.util.spec_from_file_location(
    "phlo_mcp_smoke_stack", _SMOKE_STACK_PATH
)
assert _SMOKE_STACK_SPEC is not None
assert _SMOKE_STACK_SPEC.loader is not None
smoke_stack = importlib.util.module_from_spec(_SMOKE_STACK_SPEC)
_SMOKE_STACK_SPEC.loader.exec_module(smoke_stack)

pytestmark = pytest.mark.integration


def test_smoke_stack_seeds_durable_asset(tmp_path: Path) -> None:
    smoke_stack._seed_smoke_asset(tmp_path)

    asset_path = tmp_path / "workflows" / smoke_stack._SMOKE_ASSET_FILENAME
    assert asset_path.exists()
    asset_source = asset_path.read_text(encoding="utf-8")
    assert "AssetSpec(" in asset_source
    assert f'key="{smoke_stack._SMOKE_ASSET_KEY}"' in asset_source
    assert "import dagster" not in asset_source


def test_smoke_stack_project_root_argument() -> None:
    args = smoke_stack._parse_args(["--start-stack", "--project-root", "/tmp/phlo-mcp-smoke"])

    assert args.start_stack is True
    assert args.project_root == "/tmp/phlo-mcp-smoke"


def test_smoke_stack_env_avoids_default_port_conflicts(monkeypatch) -> None:
    monkeypatch.delenv("MINIO_API_PORT", raising=False)
    monkeypatch.delenv("MINIO_CONSOLE_PORT", raising=False)
    monkeypatch.delenv("CLICKSTACK_NATIVE_PORT", raising=False)

    env = smoke_stack._smoke_stack_env()

    assert env["MINIO_API_PORT"] == "19000"
    assert env["MINIO_CONSOLE_PORT"] == "19001"
    assert env["CLICKSTACK_NATIVE_PORT"] == "19002"


def test_smoke_stack_env_respects_user_port_overrides(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_API_PORT", "29000")
    monkeypatch.setenv("CLICKSTACK_NATIVE_PORT", "29002")

    env = smoke_stack._smoke_stack_env()

    assert env["MINIO_API_PORT"] == "29000"
    assert env["CLICKSTACK_NATIVE_PORT"] == "29002"


def test_live_stack_smoke_script() -> None:
    if os.environ.get("PHLO_MCP_STACK_SMOKE", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("set PHLO_MCP_STACK_SMOKE=1 to run the live stack smoke test")

    script = Path(__file__).with_name("smoke_stack.py")
    result = subprocess.run(
        [sys.executable, str(script)],
        text=True,
        capture_output=True,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
