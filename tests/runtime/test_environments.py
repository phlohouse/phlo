"""Project-owned deployed target identity and default selection contracts."""

import json

import pytest
import yaml

from phlo.config.project import get_default_environment, get_environment_targets


def test_configured_environment_order_default_and_complete_runtime_override(tmp_path, monkeypatch):
    targets = {
        "sandbox": {"dagster_location": "sandbox_code", "nessie_ref": "development"},
        "warehouse": {"dagster_location": "warehouse_code", "nessie_ref": "main"},
    }
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump(
            {"environments": targets, "default_environment": "warehouse"}, sort_keys=False
        )
    )
    monkeypatch.delenv("PHLO_V1_ENVIRONMENTS", raising=False)
    assert list(get_environment_targets(tmp_path)) == ["sandbox", "warehouse"]
    assert get_default_environment(tmp_path) == "warehouse"
    monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", json.dumps({"warehouse": targets["warehouse"]}))
    assert list(get_environment_targets(tmp_path)) == ["warehouse"]
    monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", json.dumps({"sandbox": targets["sandbox"]}))
    with pytest.raises(ValueError, match="default_environment"):
        get_environment_targets(tmp_path)


@pytest.mark.parametrize(
    "targets",
    [
        {},
        {"bad|env": {"dagster_location": "code", "nessie_ref": "main"}},
        {"x": {"dagster_location": "code", "nessie_ref": "../main"}},
        {
            "x": {"dagster_location": "same", "nessie_ref": "main"},
            "y": {"dagster_location": "same", "nessie_ref": "other"},
        },
        {
            "x": {"dagster_location": "code", "nessie_ref": "same"},
            "y": {"dagster_location": "other", "nessie_ref": "same"},
        },
    ],
)
def test_invalid_environment_identity_fails_closed(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", json.dumps(targets))
    with pytest.raises(ValueError):
        get_environment_targets(tmp_path)
