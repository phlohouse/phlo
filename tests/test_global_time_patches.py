"""Keep test clock fakes from mutating the process-wide time module."""

import ast
from pathlib import Path

import pytest


def _global_time_patches(source: str) -> list[int]:
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "setattr" or len(node.args) < 2:
            continue
        target = node.args[0]
        if (
            (
                isinstance(target, ast.Attribute)
                and target.attr == "time"
                or isinstance(target, ast.Name)
                and target.id == "time"
            )
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in {"monotonic", "sleep", "time"}
        ) or (
            isinstance(target, ast.Constant)
            and isinstance(target.value, str)
            and target.value.rsplit(".", 2)[-2:]
            in (
                ["time", "monotonic"],
                ["time", "sleep"],
                ["time", "time"],
            )
        ):
            offenders.append(node.lineno)
    return offenders


@pytest.mark.parametrize(
    "source",
    [
        'monkeypatch.setattr(\nstart_module.time, "monotonic", fake)',
        'monkeypatch.setattr(\nhooks.time, "sleep", fake)',
        'monkeypatch.setattr(\nregistry_client.time, "time", fake)',
        'monkeypatch.setattr(\ntime, "sleep", fake)',
        'monkeypatch.setattr("phlo_dagster.cli_backfill.time.sleep", fake)',
        'monkeypatch.setattr("phlo_dagster.cli_materialize.time.monotonic", fake)',
    ],
)
def test_guard_detects_global_time_patches(source: str) -> None:
    assert _global_time_patches(source) == [1]


def test_clock_patches_do_not_target_global_time() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [*root.joinpath("tests").rglob("test_*.py")]
    paths.extend(root.glob("packages/*/tests/**/test_*.py"))
    offenders = [
        f"{path.relative_to(root)}:{line}"
        for path in paths
        for line in _global_time_patches(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
