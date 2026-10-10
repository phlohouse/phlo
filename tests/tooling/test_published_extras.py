"""Tests for the release check that every published extra resolves from PyPI."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_published_extras.py"
SPEC = importlib.util.spec_from_file_location("check_published_extras", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
check_published_extras = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_published_extras
SPEC.loader.exec_module(check_published_extras)


def _wheel(directory: Path, name: str, version: str, extras: dict[str, list[str]]) -> Path:
    stem = f"{name.replace('-', '_')}-{version}"
    path = directory / f"{stem}-py3-none-any.whl"
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    for extra, requirements in extras.items():
        lines.append(f"Provides-Extra: {extra}")
        lines.extend(f"Requires-Dist: {req}; extra == '{extra}'" for req in requirements)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{stem}.dist-info/METADATA", "\n".join(lines) + "\n")
    return path


def _manifest(path: Path, names: list[str]) -> Path:
    path.write_text(
        json.dumps({"release_set": {"packages": [{"name": name} for name in names]}}),
        encoding="utf-8",
    )
    return path


def test_resolves_each_release_wheel_bare_and_with_every_extra(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, "phlo", "1.0.0", {"defaults": ["phlo-trino>=1"], "observe": ["x"]})
    _wheel(dist, "phlo-trino", "1.0.0", {})
    seen: list[tuple[str, set[str]]] = []

    def resolve(requirement: str, find_links: Path) -> str | None:
        seen.append((requirement, {path.name for path in find_links.iterdir()}))
        return None

    failures = check_published_extras.check(dist, {"phlo", "phlo-trino"}, resolve)

    assert failures == []
    assert [requirement for requirement, _ in seen] == [
        "phlo==1.0.0",
        "phlo[defaults]==1.0.0",
        "phlo[observe]==1.0.0",
        "phlo-trino==1.0.0",
    ]
    assert seen[0][1] == {"phlo-1.0.0-py3-none-any.whl", "phlo_trino-1.0.0-py3-none-any.whl"}


def test_unreleased_workspace_wheels_cannot_stand_in_for_pypi(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, "phlo", "1.0.0", {"blueprints": ["phlo-retail-files==0.1.0"]})
    _wheel(dist, "phlo-retail-files", "0.1.0", {})
    offered: set[str] = set()

    def resolve(requirement: str, find_links: Path) -> str | None:
        offered.update(path.name for path in find_links.iterdir())
        return None

    check_published_extras.check(dist, {"phlo"}, resolve)

    assert offered == {"phlo-1.0.0-py3-none-any.whl"}


def test_reports_every_unresolvable_extra(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, "phlo", "1.0.0", {"blueprints": ["missing"], "bogus": ["phlo==99"], "ok": []})

    def resolve(requirement: str, find_links: Path) -> str | None:
        return None if "[ok]" in requirement or "[" not in requirement else "not found"

    failures = check_published_extras.check(dist, {"phlo"}, resolve)

    assert [failure.requirement for failure in failures] == [
        "phlo[blueprints]==1.0.0",
        "phlo[bogus]==1.0.0",
    ]


def test_main_fails_closed_on_a_bogus_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, "phlo", "1.0.0", {"bogus": ["phlo-does-not-exist==1"]})
    manifest = _manifest(tmp_path / "v1.json", ["phlo"])
    monkeypatch.setattr(
        check_published_extras,
        "uv_resolver",
        lambda index, python: (
            lambda requirement, links: "not found" if "[bogus]" in requirement else None
        ),
    )

    code = check_published_extras.main(["--distributions", str(dist), "--manifest", str(manifest)])

    assert code == 1
    assert "phlo[bogus]==1.0.0 does not resolve" in capsys.readouterr().out


def test_missing_release_wheel_fails_closed(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, "phlo", "1.0.0", {})

    with pytest.raises(ValueError, match="phlo-trino"):
        check_published_extras.check(dist, {"phlo", "phlo-trino"}, lambda *_: None)


def test_release_stage_runs_the_check_on_the_built_distributions() -> None:
    stage = yaml.safe_load((REPO_ROOT / ".github/workflows/release-stage.yml").read_text())
    steps = stage["jobs"]["distributions"]["steps"]
    build = next(i for i, step in enumerate(steps) if "uv build" in step.get("run", ""))
    check = next(
        i for i, step in enumerate(steps) if "check_published_extras.py" in step.get("run", "")
    )
    upload = next(i for i, step in enumerate(steps) if "upload-artifact" in step.get("uses", ""))
    assert build < check < upload
    assert '--distributions "$RUNNER_TEMP/distributions"' in steps[check]["run"]
