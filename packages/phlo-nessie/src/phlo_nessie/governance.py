"""Nessie governance backend for rendered authorization rules.

Nessie authorization rules are static Quarkus configuration, so this
backend renders the managed rule set into ``.phlo/nessie/authz.properties``
— the file the generated stack mounts at ``/deployments/phlo-authz/`` and
loads through ``quarkus.config.locations`` when
``NESSIE_AUTHZ_ENABLED=true``. ``list_policies`` reads the rendered file
back so the core ``NessieCompiler`` can diff desired vs. current rules;
``check_access`` evaluates managed expressions locally against a role/op/
ref/path tuple.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from phlo.capabilities.interfaces import AccessPolicy
from phlo.logging import get_logger

logger = get_logger(__name__)

_MANAGED_PREFIX = "phlo_"
_RULE_KEY_PREFIX = "nessie.server.authorization.rules."
_DEFAULT_REF = "main"

_OP_RE = re.compile(r"op\s+in\s*\(([^)]*)\)")
_ROLE_RE = re.compile(r"role\s*==\s*'([^']+)'")
_REF_RE = re.compile(r"ref\s*=~\s*'([^']+)'")
_PATH_RE = re.compile(r"path\s*=~\s*'([^']+)'")


def default_rules_path() -> Path:
    """Return the managed rules-file path for the current project."""
    return Path.cwd() / ".phlo" / "nessie" / "authz.properties"


def _parse_rules_file(content: str) -> dict[str, str]:
    """Parse a properties file into {rule_name: expression}."""
    rules: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.startswith(_RULE_KEY_PREFIX):
            continue
        rules[key[len(_RULE_KEY_PREFIX) :]] = value.strip()
    return rules


def _evaluate_rule(expression: str, *, role: str, op: str, ref: str, path: str) -> bool:
    """Evaluate one managed rule expression against an access tuple.

    Supports the clauses this backend renders: ``op in (...)``,
    ``role=='x'``, ``ref=~'re'``, and ``path=~'re'``. Unknown clauses fail
    closed (the rule does not match) rather than silently allowing.
    """
    op_match = _OP_RE.search(expression)
    if op_match is None:
        return False
    ops = {part.strip().strip("'") for part in op_match.group(1).split(",")}
    if op not in ops:
        return False
    role_match = _ROLE_RE.search(expression)
    if role_match is None or role_match.group(1) != role:
        return False
    ref_match = _REF_RE.search(expression)
    if ref_match is not None and not re.fullmatch(ref_match.group(1), ref):
        return False
    path_match = _PATH_RE.search(expression)
    if path_match is not None and not re.fullmatch(path_match.group(1), path):
        return False
    return True


class NessieGovernanceBackend:
    """GovernanceBackend implementation rendering Nessie authz rules."""

    def __init__(self, rules_path: Path | None = None) -> None:
        """Initialize with an optional rules-file path (tests inject tmp)."""
        self._rules_path = rules_path

    @property
    def rules_path(self) -> Path:
        """Return the resolved rules-file path."""
        return self._rules_path or default_rules_path()

    def probe(self) -> bool:
        """Return whether the rendered rules file can be read."""
        try:
            self._read_rules()
        except OSError:
            return False
        return True

    def _read_rules(self) -> dict[str, str]:
        path = self.rules_path
        if not path.exists():
            return {}
        return _parse_rules_file(path.read_text())

    def _write_rules(self, rules: dict[str, str]) -> None:
        path = self.rules_path
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Rendered by phlo governance; edits are overwritten on sync.",
            "# Requires NESSIE_AUTHZ_ENABLED=true and a Nessie restart to apply.",
        ]
        for name in sorted(rules):
            lines.append(f"{_RULE_KEY_PREFIX}{name}={rules[name]}")
        path.write_text("\n".join(lines) + "\n")

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        """List managed rules from the rendered file as {policy_id, rule}."""
        try:
            rules = self._read_rules()
        except OSError:
            logger.warning("nessie_governance_read_failed", exc_info=True)
            return []
        return [
            {"policy_id": name, "rule": expression}
            for name, expression in sorted(rules.items())
            if name.startswith(_MANAGED_PREFIX)
        ]

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        """Render one managed rule into the rules file."""
        name = policy.policy_id
        if not name or not name.startswith(_MANAGED_PREFIX):
            raise ValueError(f"Managed rule names must start with {_MANAGED_PREFIX!r}")
        rules = self._read_rules()
        rules[name] = policy.table_pattern
        self._write_rules(rules)
        logger.info("nessie_governance_apply_policy", rule=name)

    def revoke_policy(self, *, policy_id: str) -> None:
        """Remove one managed rule from the rendered file."""
        rules = self._read_rules()
        if policy_id in rules:
            del rules[policy_id]
            self._write_rules(rules)
        logger.info("nessie_governance_revoke_policy", rule=policy_id)

    def check_access(self, *, principal: str, table_name: str, action: str) -> bool:
        """Evaluate managed rules for principal/op on the resource path.

        ``action`` is the Nessie op name (e.g. ``READ_ENTITY_VALUE``) or a
        canonical ``domain.verb`` which is mapped onto the op set.
        ``table_name`` carries the ``ref:path`` tuple as ``ref:path`` when a
        ref applies, else the path alone.
        """
        op = _ACTION_TO_OP.get(action, action)
        ref, _, path = table_name.partition(":")
        if not path:
            path, ref = ref, _DEFAULT_REF
        for rule in self.list_policies():
            if _evaluate_rule(rule["rule"], role=principal, op=op, ref=ref, path=path):
                return True
        return False


_ACTION_TO_OP: dict[str, str] = {
    "catalog.read": "READ_ENTITY_VALUE",
    "dataset.read": "READ_ENTITY_VALUE",
    "dataset.query": "READ_ENTITY_VALUE",
    "dataset.write": "UPDATE_ENTITY_VALUE",
    "dataset.publish": "UPDATE_REFERENCE",
    "catalog.manage": "UPDATE_REFERENCE",
}
