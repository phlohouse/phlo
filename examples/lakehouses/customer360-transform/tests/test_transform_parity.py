"""Translation and graph parity between the dbt project and native workspace.

These checks need the phlo-transform binary (`phlo transform install`) and a
dbt executable (`uv sync --group dev`). They are marked integration because
they execute the engine rather than stub its reports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from phlo_transform.binary import find_binary
from phlo_transform.settings import get_settings
from phlo_transform.translate import translate_dbt_project
from phlo_transform.workspace import load_workspace

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DBT_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "dbt"
NATIVE_ROOT = PROJECT_ROOT / "workflows" / "transforms" / "native"


@pytest.fixture(scope="module")
def settings():
    return get_settings(PROJECT_ROOT)


@pytest.fixture(scope="module")
def binary(settings):
    found = find_binary(PROJECT_ROOT, settings)
    if found is None:
        pytest.skip("phlo-transform binary not installed; run: phlo transform install")
    return found


@pytest.fixture(scope="module")
def dbt_executable():
    found = shutil.which("dbt")
    if found is None:
        pytest.skip("dbt executable not installed; run: uv sync --group dev")
    return found


def test_translation_is_clean(settings, binary) -> None:
    invocation = translate_dbt_project(PROJECT_ROOT, settings, check_only=True)

    assert invocation.returncode == 0, invocation.stderr
    assert "Model conversion coverage: 100.0%" in invocation.stdout


def test_committed_native_workspace_matches_a_fresh_translation(
    settings, binary, tmp_path: Path
) -> None:
    from scripts.dbt_sources import apply_source_assets, source_asset_map

    generated = tmp_path / "native"
    invocation = translate_dbt_project(
        PROJECT_ROOT,
        settings,
        out=generated,
        overwrite=True,
        verify=True,
    )

    assert invocation.returncode == 0, invocation.stderr
    assert "check passed" in invocation.stdout
    apply_source_assets(generated / "phlo.toml", source_asset_map(DBT_ROOT))
    for relative in [
        *sorted(path.relative_to(generated) for path in (generated / "transforms").glob("*.sql")),
        *sorted(path.relative_to(generated) for path in (generated / "tests").glob("**/*.sql")),
        Path("phlo.toml"),
    ]:
        assert (generated / relative).read_text() == (NATIVE_ROOT / relative).read_text(), (
            f"stale native workspace file: {relative}; "
            "run: uv run python scripts/generate_native_workspace.py"
        )


def test_native_asset_graph_matches_the_dbt_manifest(
    settings, binary, dbt_executable, tmp_path: Path
) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        (DBT_ROOT / "profiles" / "profiles.yml").read_text(), encoding="utf-8"
    )
    completed = subprocess.run(  # noqa: S603 - dbt resolved above
        [
            dbt_executable,
            "parse",
            "--project-dir",
            str(DBT_ROOT),
            "--profiles-dir",
            str(profiles),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads((DBT_ROOT / "target" / "manifest.json").read_text(encoding="utf-8"))

    dbt_models = {
        node["name"]: {
            dependency.split(".")[-1]
            for dependency in node.get("depends_on", {}).get("nodes", [])
            if dependency.startswith("model.")
        }
        for node in manifest["nodes"].values()
        if node.get("resource_type") == "model"
    }
    workspace = load_workspace(PROJECT_ROOT, settings)
    assert workspace is not None
    native_models = {
        model.leaf_name: {dependency.split(".")[-1] for dependency in model.depends_on}
        for model in workspace.models
    }

    assert native_models == dbt_models
    assert len(native_models) == 10
    assert {test.targets[0].split(".")[-1] for test in workspace.tests if test.targets} == {
        "consent_current",
        "customer_dimension",
        "identity_resolution",
        "stg_consent_events",
        "stg_marketing_contacts",
        "stg_support_tickets",
    }


def test_phlo_asset_graph_is_identical_under_either_provider(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    binary,
    dbt_executable,
) -> None:
    """The provider layer, not just the SQL, exposes the same graph."""

    from phlo_dbt.assets import build_dbt_asset_specs
    from phlo_transform.assets import build_transform_asset_specs

    profiles = DBT_ROOT / "profiles"
    completed = subprocess.run(  # noqa: S603 - dbt resolved above
        [
            dbt_executable,
            "parse",
            "--project-dir",
            str(DBT_ROOT),
            "--profiles-dir",
            str(profiles),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    dbt_specs = {spec.key: set(spec.deps) for spec in build_dbt_asset_specs()}
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "phlo-transform")
    native_specs = {
        spec.key: set(spec.deps)
        for spec in build_transform_asset_specs(PROJECT_ROOT)
        if "transform_model" in spec.metadata
    }

    assert native_specs == dbt_specs
    assert set(native_specs) == {
        path.stem for path in (PROJECT_ROOT / "workflows").glob("*/models/*.sql")
    }
