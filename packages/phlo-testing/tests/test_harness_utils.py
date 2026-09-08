"""Unit tests for the package-owned harness utilities.

Guards the packaging contract: the installed ``phlo-testing`` package must
never load the repo-only ``scripts/run_golden_path.py``; its harness helpers
live in ``phlo_testing.harness_utils``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from phlo_testing import harness_utils
from phlo_testing.harness_utils import read_env_file, run_phlo
from phlo_testing.profile_harness import bootstrap_bundled_stack_harness  # noqa: F401


def test_profile_harness_does_not_reference_repo_script() -> None:
    """The profile harness must not load scripts/run_golden_path.py at runtime."""
    import phlo_testing.profile_harness as profile_harness

    harness_source = inspect.getsource(profile_harness)
    assert "_load_golden_path_module" not in harness_source
    assert "run_golden_path" not in harness_source
    assert "importlib.util" not in harness_source


def test_run_phlo_uses_python_module_entrypoint(tmp_path: Path, monkeypatch) -> None:
    """run_phlo should invoke the phlo CLI through ``python -m phlo.cli.main``."""
    captured: dict[str, object] = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr(harness_utils, "run_command", fake_run_command)
    result = run_phlo(["services", "start"], cwd=tmp_path, python_exe=None)

    assert result == "ok"
    assert captured["args"][1:4] == ["-m", "phlo.cli.main", "services"]


def test_read_env_file_parses_and_skips_comments(tmp_path: Path) -> None:
    """read_env_file should read KEY=VALUE lines and skip comments and blanks."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\n\nPOSTGRES_PORT=5432\nQUOTED=a=b\n",
        encoding="utf-8",
    )

    assert read_env_file(env_path) == {"POSTGRES_PORT": "5432", "QUOTED": "a=b"}


def test_apply_env_updates_uses_shared_layout_destinations(tmp_path):
    from phlo.config.layout import SHARED_LAYOUT_MARKER

    (tmp_path / ".gitignore").write_text(SHARED_LAYOUT_MARKER)
    harness_utils.apply_env_updates(tmp_path, {"POSTGRES_PORT": "15432"})
    assert read_env_file(tmp_path / "overrides/.env") == {"POSTGRES_PORT": "15432"}
    assert read_env_file(tmp_path / "secrets/.env") == {"POSTGRES_PORT": "15432"}
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".env.local").exists()
