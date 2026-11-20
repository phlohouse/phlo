"""Installing plugin packages must never move the installed Phlo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from phlo.plugins import project_install
from phlo.plugins.project_install import PluginInstallError, install_packages, pin_to_release

RELEASE = {
    "packages": [{"name": "phlo"}, {"name": "phlo-trino"}, {"name": "phlo-grafana"}],
    "release_set": {
        "packages": [
            {"name": "phlo", "version": "0.19.0"},
            {"name": "phlo-trino", "version": "0.18.0"},
        ]
    },
}


@pytest.fixture(autouse=True)
def _release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phlo.capabilities.support_status.load_support_manifest", lambda: RELEASE)
    monkeypatch.setattr(project_install, "installed_phlo_version", lambda: "0.19.0")
    monkeypatch.setattr("phlo.plugins.discovery.refresh", lambda: None)


def test_first_party_packages_are_pinned_to_the_release() -> None:
    assert pin_to_release(["phlo-trino", "Phlo_Trino[extra]>=0.1", "acme-plugin>=2"], "0.19.0") == [
        "phlo-trino==0.18.0",
        "Phlo_Trino[extra]==0.18.0",
        "acme-plugin>=2",
    ]


def test_a_first_party_package_left_out_of_the_release_is_refused() -> None:
    with pytest.raises(PluginInstallError, match="phlo-grafana is not published with phlo 0.19.0"):
        pin_to_release(["phlo-grafana"], "0.19.0")


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "lake"\ndependencies = []\n')
    (tmp_path / "phlo.yaml").write_text("name: lake\n")
    return tmp_path


def _fake_uv(monkeypatch: pytest.MonkeyPatch, locked_phlo: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command, cwd=None, **kwargs):
        calls.append(list(command))
        if command[1] == "add":
            Path(cwd, "pyproject.toml").write_text("changed by uv add\n")
            Path(cwd, "uv.lock").write_text(
                f'[[package]]\nname = "phlo"\nversion = "{locked_phlo}"\n'
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(project_install.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setattr(project_install.subprocess, "run", run)
    return calls


def test_a_plugin_is_added_to_the_project_and_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    calls = _fake_uv(monkeypatch, locked_phlo="0.19.0")

    assert install_packages(["phlo-trino"], cwd=root / ".") == "project-env"
    assert calls == [
        ["/bin/uv", "add", "--no-sync", "phlo-trino==0.18.0"],
        ["/bin/uv", "sync", "--inexact"],
    ]


def test_a_plugin_that_would_change_phlo_is_refused_and_the_project_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    before = (root / "pyproject.toml").read_text()
    calls = _fake_uv(monkeypatch, locked_phlo="0.17.0")

    with pytest.raises(PluginInstallError, match="would change the installed phlo"):
        install_packages(["acme-plugin"], cwd=root)

    assert (root / "pyproject.toml").read_text() == before
    assert not (root / "uv.lock").exists()
    assert [call[1] for call in calls] == ["add"]


def test_outside_a_project_the_install_requires_the_installed_phlo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_uv(monkeypatch, locked_phlo="unused")

    assert install_packages(["phlo-trino"], cwd=tmp_path) == "interpreter"
    assert calls == [
        [
            "/bin/uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "phlo-trino==0.18.0",
            "phlo==0.19.0",
        ]
    ]
