"""Golden tests for stable CLI error output contracts.

Each contract locks both the exit code and the normalized stdout against a
golden file, so any user-visible change to an error message must update a
golden deliberately.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "goldens" / "cli_error_contracts"


def _normalize_output(output: str, replacements: Mapping[str, str] | None = None) -> str:
    normalized = output.replace("\r\n", "\n")
    if replacements:
        for source, replacement in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            normalized = normalized.replace(source, replacement)
    return normalized


def _render_contract(result: Result, output: str) -> str:
    normalized_output = output if output.endswith("\n") else f"{output}\n"
    return f"exit_code={result.exit_code}\noutput<<EOF\n{normalized_output}EOF\n"


def _assert_matches_golden(
    golden_name: str, result: Result, replacements: Mapping[str, str] | None = None
) -> None:
    expected_path = GOLDEN_DIR / f"{golden_name}.txt"
    expected = expected_path.read_text()
    actual = _render_contract(result, _normalize_output(result.output, replacements))
    assert actual == expected


def test_services_start_invalid_profile_error_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lock invalid profile output and exit behavior."""
    from phlo.cli.commands.services import common as common_module
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n")

    class FakeDiscovery:
        def get_available_profiles(self) -> set[str]:
            return {"api", "observability"}

    def _unexpected_call(*_args, **_kwargs) -> None:
        raise AssertionError("Docker path should not execute for invalid profile contract")

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(common_module, "ServiceDiscovery", FakeDiscovery)
    monkeypatch.setattr(start_module, "run_command", _unexpected_call)
    monkeypatch.setattr(start_module, "require_container_backend", _unexpected_call)

    result = CliRunner().invoke(start_module.start_cmd, ["--profile", "not-a-profile"])

    _assert_matches_golden("services_start_invalid_profile", result)


def test_services_list_config_parse_failure_error_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lock config parse failure output and exit behavior."""
    from phlo.cli.commands.services import list as list_module

    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("services: [\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])

    _assert_matches_golden(
        "services_list_config_parse_failure",
        result,
        replacements={str(config_path): "<PROJECT_ROOT>/phlo.yaml"},
    )


def test_services_list_discovery_failure_error_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lock discovery failure output and exit behavior."""
    from phlo.cli.commands.services import list as list_module

    class FailingDiscovery:
        def discover(self):
            raise RuntimeError("discovery blew up")

    monkeypatch.setattr(list_module, "ServiceDiscovery", FailingDiscovery)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])

    _assert_matches_golden("services_list_discovery_failure", result)


def test_normalize_output_prefers_longest_replacements_first() -> None:
    """Apply overlapping replacements longest-first to avoid prefix clobbering."""
    normalized = _normalize_output(
        "/tmp/foo/bar/baz is at /tmp/foo/bar",
        replacements={
            "/tmp/foo/bar/baz": "<LONG>",
            "/tmp/foo/bar": "<SHORT>",
        },
    )

    assert normalized == "<LONG> is at <SHORT>"
