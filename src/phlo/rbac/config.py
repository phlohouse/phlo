"""Configuration loader for canonical RBAC files.

RBACConfigLoader reads roles.yaml and policies.yaml from
``<base>/authorization/`` (default .phlo in the cwd), validates them through
the CanonicalRBAC model, and exposes compute_version_hash() for sync drift
detection. The hash covers raw parsed YAML, so two files projecting to the
same canonical model can still hash differently.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from phlo.rbac.models import CanonicalRBAC, PoliciesConfig, RolesConfig


class RBACConfigLoader:
    """Loads and validates canonical RBAC configuration files."""

    def __init__(self, base_path: Path | None = None):
        """Initialize the loader rooted at base_path (default .phlo in the cwd).

        Config files live under ``<base>/authorization/``.
        """
        self._base_path = base_path or Path.cwd() / ".phlo"

    @property
    def base_path(self) -> Path:
        """Return the base path for RBAC config files."""
        return self._base_path

    def load_roles(self, path: Path | None = None) -> RolesConfig:
        """Read roles.yaml (default base_path/authorization/roles.yaml) into RolesConfig.

        Raises: FileNotFoundError when the file is absent; ValueError when
        its contents fail validation.
        """
        if path is None:
            path = self._base_path / "authorization" / "roles.yaml"

        if not path.exists():
            raise FileNotFoundError(f"Roles config not found: {path}")

        with path.open() as f:
            data = yaml.safe_load(f) or {}

        try:
            return RolesConfig.from_dict(data)
        except Exception as e:
            raise ValueError(f"Invalid roles config: {e}") from e

    def load_policies(self, path: Path | None = None) -> PoliciesConfig:
        """Read policies.yaml (default base_path/authorization/policies.yaml) into PoliciesConfig.

        Raises: FileNotFoundError when the file is absent; ValueError when
        its contents fail validation.
        """
        if path is None:
            path = self._base_path / "authorization" / "policies.yaml"

        if not path.exists():
            raise FileNotFoundError(f"Policies config not found: {path}")

        with path.open() as f:
            data = yaml.safe_load(f) or {}

        try:
            return PoliciesConfig.from_dict(data)
        except Exception as e:
            raise ValueError(f"Invalid policies config: {e}") from e

    def load(self) -> CanonicalRBAC:
        """Load roles.yaml and policies.yaml into a validated CanonicalRBAC.

        Raises: FileNotFoundError when either file is absent; ValueError when
        parsing or validation fails.
        """
        roles = self.load_roles()
        policies = self.load_policies()

        rbac = CanonicalRBAC.from_configs(roles, policies)

        errors = rbac.validate()
        if errors:
            raise ValueError(f"Invalid RBAC configuration: {errors}")

        return rbac

    def validate(self) -> tuple[bool, list[str]]:
        """Validate the configuration without raising, returning (is_valid, error_messages)."""
        try:
            rbac = self.load()
            errors = rbac.validate()
            return len(errors) == 0, errors
        except Exception as e:
            return False, [str(e)]

    def compute_version_hash(self) -> str:
        """Compute a 16-character SHA256 hash of the raw parsed RBAC YAML content."""
        roles_path = self._base_path / "authorization" / "roles.yaml"
        policies_path = self._base_path / "authorization" / "policies.yaml"

        content: dict[str, Any] = {}
        if roles_path.exists():
            with roles_path.open() as f:
                content["roles"] = yaml.safe_load(f) or {}
        if policies_path.exists():
            with policies_path.open() as f:
                content["policies"] = yaml.safe_load(f) or {}

        # Hashes the raw parsed YAML, not the canonical projection built by
        # CanonicalRBAC.from_configs: two files yielding the same canonical model
        # can therefore produce different hashes here.
        json_content = json.dumps(content, sort_keys=True)
        return hashlib.sha256(json_content.encode()).hexdigest()[:16]
