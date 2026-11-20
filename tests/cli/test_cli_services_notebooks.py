"""Adding notebooks or the preview Trino turns them on without hand-set settings."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from phlo.cli.commands.services.utils import (
    apply_notebook_defaults,
    apply_preview_defaults,
    grant_dev_access,
)
from phlo.plugins.discovery.service_manifest import ServiceManifestResolver
from tests.support import _service

KERNELS = _service("phlo-notebook-kernels")
API = _service("phlo-api")
TRINO = _service("trino")
PREVIEW = _service("trino-preview", depends_on=["trino"])
PREVIEW.companion_of = ["trino", "phlo-api"]
ENVS = {"sandbox": {"nessie_ref": "main"}, "pre-prod": {"nessie_ref": "release_1"}}


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
    assert grant_dev_access(tmp_path, [KERNELS], ["sandbox"]) is True
    policies = yaml.safe_load(path.read_text(encoding="utf-8"))["policies"]
    granted = {(p["action"], p["resource"]["id_pattern"]) for p in policies[1:]}
    assert ("run.execute", "env=sandbox") in granted
    assert ("object.write", "env=sandbox|*") in granted
    assert len(granted) == 10
    assert grant_dev_access(tmp_path, [KERNELS], ["sandbox"]) is False


def test_other_authorization_is_never_widened(tmp_path: Path) -> None:
    """Verify a policy file not written by --observatory-dev is left alone."""
    path = _write_policies(tmp_path, ["team-readers"])
    before = path.read_text(encoding="utf-8")
    assert grant_dev_access(tmp_path, [KERNELS], ["sandbox"]) is False
    assert path.read_text(encoding="utf-8") == before


def test_no_grant_without_the_kernel_host(tmp_path: Path) -> None:
    """Verify the dev identity stays read-only when notebooks are not added."""
    _write_policies(tmp_path, ["observatory-dev-discovery"])
    assert grant_dev_access(tmp_path, [API], ["sandbox"]) is False


def test_regenerating_keeps_the_dev_source_mount(tmp_path: Path) -> None:
    """Verify `services add` in a dev-mode stack finds the mounted phlo source again."""
    from phlo.cli.commands.services.utils import _existing_dev_source

    host = tmp_path / "overrides" / "compose.host.yaml"
    host.parent.mkdir()
    assert _existing_dev_source(tmp_path) is None
    services = {"phlo-api": {"volumes": ["../../phlo/src/phlo/../..:/opt/phlo-dev:rw"]}}
    host.write_text(yaml.safe_dump({"services": services}), encoding="utf-8")
    assert _existing_dev_source(tmp_path) == "../../phlo/src/phlo"


def test_preview_trino_is_added_with_the_api_and_trino() -> None:
    """Verify the preview server joins a stack that has both, and only then."""
    available = [TRINO, API, PREVIEW]
    names = {
        s.name
        for s in ServiceManifestResolver.expand_dependencies(
            [TRINO, API], available_services=available
        )
    }
    assert "trino-preview" in names
    names = {
        s.name
        for s in ServiceManifestResolver.expand_dependencies([TRINO], available_services=available)
    }
    assert names == {"trino"}


def test_preview_defaults_point_the_api_at_the_generated_server() -> None:
    """Verify each environment gets its own catalog and password file, with no hand-set env."""
    result = apply_preview_defaults([API, TRINO, PREVIEW], {}, ENVS)
    assert result["PHLO_V1_PREVIEW_TRINO_URL"] == "https://trino-preview:8443"
    assert json.loads(result["PHLO_V1_PREVIEW_CATALOGS"]) == {
        "sandbox": {"catalog": "preview_sandbox", "nessie_ref": "main"},
        "pre-prod": {"catalog": "preview_pre_prod", "nessie_ref": "release_1"},
    }
    files = json.loads(result["PHLO_V1_PREVIEW_TRINO_PASSWORD_FILES"])
    assert files["pre-prod"] == "/var/lib/phlo-preview/api/password-pre-prod"
    assert result["PHLO_V1_PREVIEW_STATE_DIR"] == "/var/lib/phlo-preview"
    assert result["PHLO_V1_PREVIEW_TARGETS"] == "preview_sandbox main,preview_pre_prod release_1"
    for name in (
        "PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED",
        "PHLO_V1_QUERY_SINGLE_REPLICA",
        "PHLO_V1_QUERY_SINGLE_PROCESS",
    ):
        assert result[name] == "1"


def test_operator_preview_settings_are_left_alone() -> None:
    """Verify a project that configures its own preview keeps it."""
    own = {"PHLO_V1_PREVIEW_TRINO_URL": "https://preview.example:8443"}
    assert apply_preview_defaults([API, TRINO, PREVIEW], own, ENVS) == own
    stanza = {"sandbox": {"nessie_ref": "main", "preview": {"catalog": "mine"}}}
    assert apply_preview_defaults([API, TRINO, PREVIEW], {}, stanza) == {}


def test_no_preview_defaults_without_server_or_environments() -> None:
    """Verify nothing is pointed at a server that is not there or has nothing to serve."""
    assert apply_preview_defaults([API, TRINO], {}, ENVS) == {}
    assert apply_preview_defaults([API, TRINO, PREVIEW], {}, {}) == {}
    clash = {"a-b": {"nessie_ref": "main"}, "a_b": {"nessie_ref": "dev"}}
    assert apply_preview_defaults([API, TRINO, PREVIEW], {}, clash) == {}


def test_dev_identity_may_query_once_the_preview_is_added(tmp_path: Path) -> None:
    """Verify dev_user can run and save queries without notebooks, and nothing more."""
    path = _write_policies(tmp_path, ["observatory-dev-discovery"])
    assert grant_dev_access(tmp_path, [PREVIEW], ["sandbox"]) is True
    policies = yaml.safe_load(path.read_text(encoding="utf-8"))["policies"][1:]
    assert {p["action"] for p in policies} == {"dataset.query", "object.write"}
    assert {p["resource"]["id_pattern"] for p in policies} == {"env=sandbox", "env=sandbox|*"}
