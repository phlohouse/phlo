"""Adding the notebook kernel host turns notebooks on without hand-set settings."""

from __future__ import annotations

from pathlib import Path

import yaml

from phlo.cli.commands.services.utils import apply_notebook_defaults, grant_dev_notebooks
from tests.support import _service

KERNELS = _service("phlo-notebook-kernels")
API = _service("phlo-api")


def test_kernel_host_turns_notebooks_on_and_adds_the_api_extra() -> None:
    """Verify the switches start on and the extra joins any existing extras."""
    result = apply_notebook_defaults([API, KERNELS], {"PHLO_API_EXTRAS": "trino, iceberg"})
    assert result["PHLO_NOTEBOOK_KERNELS"] == "1"
    assert result["PHLO_NOTEBOOK_PACKAGES"] == "1"
    assert result["PHLO_NOTEBOOK_SCHEDULES"] == "1"
    assert result["PHLO_V1_QUERY_SINGLE_PROCESS"] == "1"
    assert result["PHLO_API_EXTRAS"] == "trino,iceberg,notebooks"


def test_project_settings_win_over_notebook_defaults() -> None:
    """Verify a value set in phlo.yaml env is kept, and the extra is not repeated."""
    result = apply_notebook_defaults(
        [KERNELS], {"PHLO_NOTEBOOK_PACKAGES": "0", "PHLO_API_EXTRAS": "notebooks"}
    )
    assert result["PHLO_NOTEBOOK_PACKAGES"] == "0"
    assert result["PHLO_API_EXTRAS"] == "notebooks"


def test_without_the_kernel_host_nothing_changes() -> None:
    """Verify notebooks stay off when the kernel host is not installed."""
    overrides = {"PHLO_API_EXTRAS": "trino"}
    assert apply_notebook_defaults([API], overrides) == overrides


def _write_policies(phlo_dir: Path, policy_ids: list[str]) -> Path:
    path = phlo_dir / "authorization" / "policies.yaml"
    path.parent.mkdir(parents=True)
    policies = [{"policy_id": policy_id, "action": "*.read"} for policy_id in policy_ids]
    path.write_text(yaml.safe_dump({"policies": policies}), encoding="utf-8")
    return path


def test_dev_identity_gets_notebook_rights_once(tmp_path: Path) -> None:
    """Verify the generated dev identity gains notebook actions per environment, once."""
    path = _write_policies(tmp_path, ["observatory-dev-discovery"])
    assert grant_dev_notebooks(tmp_path, [KERNELS], ["sandbox"]) is True
    policies = yaml.safe_load(path.read_text(encoding="utf-8"))["policies"]
    granted = {(p["action"], p["resource"]["id_pattern"]) for p in policies[1:]}
    assert ("run.execute", "env=sandbox") in granted
    assert ("object.write", "env=sandbox|*") in granted
    assert len(granted) == 10
    assert grant_dev_notebooks(tmp_path, [KERNELS], ["sandbox"]) is False


def test_other_authorization_is_never_widened(tmp_path: Path) -> None:
    """Verify a policy file not written by --observatory-dev is left alone."""
    path = _write_policies(tmp_path, ["team-readers"])
    before = path.read_text(encoding="utf-8")
    assert grant_dev_notebooks(tmp_path, [KERNELS], ["sandbox"]) is False
    assert path.read_text(encoding="utf-8") == before


def test_no_grant_without_the_kernel_host(tmp_path: Path) -> None:
    """Verify the dev identity stays read-only when notebooks are not added."""
    _write_policies(tmp_path, ["observatory-dev-discovery"])
    assert grant_dev_notebooks(tmp_path, [API], ["sandbox"]) is False


def test_regenerating_keeps_the_dev_source_mount(tmp_path: Path) -> None:
    """Verify `services add` in a dev-mode stack finds the mounted phlo source again."""
    from phlo.cli.commands.services.utils import _existing_dev_source

    host = tmp_path / "overrides" / "compose.host.yaml"
    host.parent.mkdir()
    assert _existing_dev_source(tmp_path) is None
    services = {"phlo-api": {"volumes": ["../../phlo/src/phlo/../..:/opt/phlo-dev:rw"]}}
    host.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    assert _existing_dev_source(tmp_path) == "../../phlo/src/phlo"
