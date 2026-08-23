"""Tests for the RBAC config loader.

Covers loading roles and policies from authorization/*.yaml, validation
error reporting for missing files, deterministic content-derived version
hashes, and the .phlo default base path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from phlo.rbac.config import RBACConfigLoader

_ROLES_YAML = {"version": 1, "roles": {"admin": {"inherits": []}}}

_POLICIES_YAML = {
    "version": 1,
    "policies": [
        {
            "policy_id": "allow_admin",
            "effect": "allow",
            "principal": {"roles": ["admin"]},
            "action": "dataset.read",
            "resource": {"type": "dataset", "id_pattern": "*"},
        }
    ],
}


def _write_rbac(base: Path, *, roles: bool = True, policies: bool = True) -> Path:
    auth_dir = base / "authorization"
    auth_dir.mkdir(parents=True, exist_ok=True)
    if roles:
        (auth_dir / "roles.yaml").write_text(yaml.dump(_ROLES_YAML))
    if policies:
        (auth_dir / "policies.yaml").write_text(yaml.dump(_POLICIES_YAML))
    return base


class TestRBACConfigLoader:
    def test_load_roles_from_yaml(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        roles = loader.load_roles()
        assert "admin" in roles.roles

    def test_load_roles_file_not_found(self, tmp_path: Path) -> None:
        loader = RBACConfigLoader(base_path=tmp_path)
        with pytest.raises(FileNotFoundError):
            loader.load_roles()

    def test_load_policies_from_yaml(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        policies = loader.load_policies()
        assert len(policies.policies) == 1
        assert policies.policies[0].policy_id == "allow_admin"

    def test_load_policies_file_not_found(self, tmp_path: Path) -> None:
        loader = RBACConfigLoader(base_path=tmp_path)
        with pytest.raises(FileNotFoundError):
            loader.load_policies()

    def test_load_combines_roles_and_policies(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        rbac = loader.load()
        assert "admin" in rbac.roles.roles
        assert len(rbac.policies.policies) == 1

    def test_validate_valid_config(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        is_valid, errors = loader.validate()
        assert is_valid is True
        assert errors == []

    def test_validate_missing_files(self, tmp_path: Path) -> None:
        loader = RBACConfigLoader(base_path=tmp_path)
        is_valid, errors = loader.validate()
        assert is_valid is False
        assert len(errors) > 0

    def test_compute_version_hash_deterministic(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        assert loader.compute_version_hash() == loader.compute_version_hash()

    def test_compute_version_hash_changes_with_content(self, tmp_path: Path) -> None:
        _write_rbac(tmp_path)
        loader = RBACConfigLoader(base_path=tmp_path)
        hash1 = loader.compute_version_hash()

        alt = {"version": 1, "roles": {"viewer": {"inherits": []}}}
        (tmp_path / "authorization" / "roles.yaml").write_text(yaml.dump(alt))
        hash2 = loader.compute_version_hash()

        assert hash1 != hash2

    def test_base_path_defaults_to_cwd(self) -> None:
        loader = RBACConfigLoader()
        assert loader.base_path == Path.cwd() / ".phlo"
