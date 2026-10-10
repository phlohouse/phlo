"""Unit tests for the package-owned harness utilities.

Guards the packaging contract: the installed ``phlo-testing`` package must
never load the repo-only ``scripts/run_golden_path.py``; its harness helpers
live in ``phlo_testing.harness_utils``.
"""

from __future__ import annotations

from pathlib import Path

from phlo_testing import harness_core, harness_utils
from phlo_testing.harness_utils import read_env_file, run_phlo


def test_run_phlo_uses_python_module_entrypoint(tmp_path: Path, monkeypatch) -> None:
    """run_phlo should invoke the phlo CLI through ``python -m phlo.cli.main``."""
    captured: dict[str, object] = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr(harness_core, "run_command", fake_run_command)
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


def test_read_env_file_strips_quotes_and_tolerates_missing_file(tmp_path: Path) -> None:
    """read_env_file should strip surrounding quotes and return {} for an absent file."""
    env_path = tmp_path / ".env"
    env_path.write_text("SINGLE='one'\nDOUBLE=\"two\"\n", encoding="utf-8")

    assert read_env_file(env_path) == {"SINGLE": "one", "DOUBLE": "two"}
    assert read_env_file(tmp_path / "missing.env") == {}


def test_apply_env_updates_uses_shared_layout_destinations(tmp_path):
    from phlo.config.layout import SHARED_LAYOUT_MARKER

    (tmp_path / ".gitignore").write_text(SHARED_LAYOUT_MARKER)
    harness_utils.apply_env_updates(tmp_path, {"POSTGRES_PORT": "15432"})
    assert read_env_file(tmp_path / "overrides/.env") == {"POSTGRES_PORT": "15432"}
    assert read_env_file(tmp_path / "secrets/.env") == {"POSTGRES_PORT": "15432"}
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".env.local").exists()


def test_golden_path_scripts_reuse_harness_core() -> None:
    """The release scripts must reuse harness_core rather than keep a clone."""
    import importlib.util
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "golden_path_common_under_test", repo_root / "scripts" / "golden_path_common.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    for name in ("run_command", "wait_for_http", "force_remove_directory"):
        defined_in = Path(getattr(module, name).__code__.co_filename).resolve()
        assert defined_in == Path(harness_core.__file__).resolve()
