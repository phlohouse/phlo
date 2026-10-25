"""dbt translation command construction and CLI forwarding."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo_transform import translate as translate_module
from phlo_transform.binary import Invocation
from phlo_transform.settings import TransformSettings
from phlo_transform.translate import find_dbt_project, translate_dbt_project


@pytest.fixture
def dbt_project(tmp_path: Path) -> Path:
    project = tmp_path / "workflows" / "transforms" / "dbt"
    project.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")
    nested = tmp_path / "workflows" / "other" / "dbt_packages" / "pkg"
    nested.mkdir(parents=True)
    (nested / "dbt_project.yml").write_text("name: pkg\n", encoding="utf-8")
    return project


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], Path]]:
    calls: list[tuple[list[str], Path]] = []

    def fake_run_cli(binary, args, *, settings, root):
        calls.append((list(args), root))
        return Invocation(command=(str(binary), *args), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(
        translate_module, "require_binary", lambda *args: Path("/bin/phlo-transform")
    )
    monkeypatch.setattr(translate_module, "run_cli", fake_run_cli)
    return calls


def test_find_dbt_project_skips_installed_packages(tmp_path: Path, dbt_project: Path) -> None:
    assert find_dbt_project(tmp_path) == dbt_project


def test_translate_forwards_output_and_verification(
    tmp_path: Path, dbt_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)

    invocation = translate_dbt_project(
        tmp_path,
        TransformSettings(_project_root=tmp_path),
        out=Path("native"),
        overwrite=True,
        verify=True,
    )

    assert invocation.returncode == 0
    args, root = calls[0]
    assert root == dbt_project
    assert args == [
        "translate",
        "--from",
        "dbt",
        "--out",
        str(tmp_path / "native"),
        "--overwrite",
        "--verify",
    ]


def test_translate_check_only_passes_check_without_an_output_directory(
    tmp_path: Path, dbt_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)

    translate_dbt_project(
        tmp_path,
        TransformSettings(_project_root=tmp_path),
        check_only=True,
    )

    args, root = calls[0]
    assert root == dbt_project
    assert args == ["translate", "--from", "dbt", "--check"]


def test_translate_requires_a_dbt_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)

    with pytest.raises(PhloCapabilitySetupError, match="no dbt project found"):
        translate_dbt_project(tmp_path, TransformSettings(_project_root=tmp_path), check_only=True)


def test_translate_requires_output_when_writing(
    tmp_path: Path, dbt_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture(monkeypatch)

    with pytest.raises(ValueError, match="output directory"):
        translate_dbt_project(tmp_path, TransformSettings(_project_root=tmp_path))
