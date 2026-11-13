"""MinIO governance backend for managed IAM policy documents.

Wraps ``mc admin policy`` inside the generated MinIO container. Roles map
to MinIO groups; ``list_policies`` reports each managed document with the
group it is actually attached to so drift covers attachment, not just
the document.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

from phlo.capabilities.interfaces import AccessPolicy
from phlo.logging import get_logger

logger = get_logger(__name__)

_MANAGED_PREFIX = "phlo_"
_ALIAS = "local"
_ALIAS_SETUP = (
    'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
    '"$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; '
)


def _default_mc_runner(script: str) -> str:
    """Run a shell script inside the generated MinIO container via compose exec."""
    from phlo.cli.commands.services.utils import ensure_compose_project
    from phlo.cli.infrastructure.compose import compose_base_cmd
    from phlo.cli.infrastructure.utils import get_project_name

    phlo_dir = ensure_compose_project()
    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=get_project_name())
    cmd.extend(["exec", "-T", "minio", "/bin/sh", "-c", _ALIAS_SETUP + script])
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"mc command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def _parse_json_lines(output: str) -> list[dict[str, Any]]:
    """Parse mc --json output (one JSON object per line)."""
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict) or parsed.get("status") == "error":
            raise ValueError("Invalid mc policy response")
        rows.append(parsed)
    return rows


def _extract_document(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the parsed policy document from an mc policy info row.

    Current mc emits the document under ``policyInfo.Policy``; older builds
    used top-level ``policyDocument``/``policy``. All are tried, and a
    ``policy`` string carrying the policy *name* is ignored.
    """
    candidates: list[Any] = [row.get("policyDocument"), row.get("policy")]
    info = row.get("policyInfo")
    if isinstance(info, dict):
        candidates.extend([info.get("Policy"), info.get("policy")])
    for raw in candidates:
        if isinstance(raw, dict) and "Statement" in raw:
            return raw
        if isinstance(raw, str) and raw.startswith("{"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "Statement" in parsed:
                return parsed
    return None


def _group_policy_names(rows: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in rows:
        result = row["result"]
        mappings = (result.get("groupPolicyMappings") or []) + (result.get("policyMappings") or [])
        for mapping in mappings:
            policies = mapping.get("policies")
            if policies is None and "policy" in mapping:
                policies = [mapping["policy"]]
            if not isinstance(policies, list) or any(
                not isinstance(name, str) for name in policies
            ):
                raise ValueError("Invalid group policy mapping")
            names.update(policies)
    return names


def _iam_patterns(value: Any) -> list[str]:
    patterns = [value] if isinstance(value, str) else value
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("IAM actions and resources must be nonempty strings or lists")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or "${" in pattern:
            raise ValueError("Unsupported IAM pattern")
    return patterns


def _iam_matches(pattern: str, value: str) -> bool:
    # IAM supports * and ?, not shell bracket character classes.
    expression = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(expression, value, flags=re.DOTALL) is not None


class MinioGovernanceBackend:
    """GovernanceBackend implementation using mc admin policy commands."""

    def __init__(self, runner: Callable[[str], str] | None = None) -> None:
        """Initialize with an optional script runner (tests inject a fake)."""
        self._runner = runner or _default_mc_runner

    def probe(self) -> bool:
        """Return whether the MinIO container answers an admin read."""
        try:
            self._runner("mc admin info local --json")
        except Exception:
            return False
        return True

    def _attached_groups(self, name: str) -> list[str]:
        """Return the MinIO groups the named policy is attached to."""
        entities = self._runner(
            f"mc admin policy entities {_ALIAS} --policy {shlex.quote(name)} --json"
        )
        groups: set[str] = set()
        for row in _parse_json_lines(entities):
            result = row.get("result") or {}
            for key in ("groupPolicyMappings", "policyMappings"):
                for mapping in result.get(key) or []:
                    group = mapping.get("group")
                    if isinstance(group, str) and group:
                        groups.add(group)
                    # Current mc emits group names as a list under "groups".
                    for name in mapping.get("groups") or []:
                        if isinstance(name, str) and name:
                            groups.add(name)
        return sorted(groups)

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        """List managed policy documents with their real group attachment.

        ``role`` is the single attached group, ``""`` when detached, or a
        comma-joined list when attached to several groups — only the exact
        desired attachment counts as converged state.
        """
        listing = self._runner(f"mc admin policy list {_ALIAS} --json")
        names = [
            str(row.get("policy", ""))
            for row in _parse_json_lines(listing)
            if str(row.get("policy", "")).startswith(_MANAGED_PREFIX)
        ]
        rows: list[dict[str, Any]] = []
        for name in names:
            info = self._runner(f"mc admin policy info {_ALIAS} {shlex.quote(name)} --json")
            document: dict[str, Any] = {}
            for row in _parse_json_lines(info):
                parsed = _extract_document(row)
                if parsed is not None:
                    document = parsed
                    break
            groups = self._attached_groups(name)
            rows.append(
                {
                    "policy_name": name,
                    "document": json.dumps(document, sort_keys=True),
                    "role": ",".join(groups),
                }
            )
        return rows

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        """Validate, replace the named document, then reconcile its attachments.

        Native mc create overwrites an existing document without deleting it.
        Definite creation failure therefore does not detach the old policy.
        Attachment failures propagate; list_policies exposes the partial state
        and applying again reconciles it. No cross-command atomicity is claimed.
        """
        name = policy.policy_id
        if not name or not name.startswith(_MANAGED_PREFIX):
            raise ValueError(f"Managed policy names must start with {_MANAGED_PREFIX!r}")
        document = json.loads(policy.table_pattern)
        if not isinstance(document, dict) or not isinstance(
            document.get("Statement"), (list, dict)
        ):
            raise ValueError("Policy must be a JSON object with IAM statements")
        groups = self._attached_groups(name)
        encoded = base64.b64encode(json.dumps(document, sort_keys=True).encode("utf-8")).decode(
            "ascii"
        )
        self._runner(
            "path=$(mktemp) && trap 'rm -f \"$path\"' EXIT && "
            f'printf %s {shlex.quote(encoded)} | base64 -d > "$path" && '
            f'mc admin policy create {_ALIAS} {shlex.quote(name)} "$path"'
        )
        for group in groups:
            if group != policy.principal:
                self._runner(
                    f"mc admin policy detach {_ALIAS} {shlex.quote(name)} --group {shlex.quote(group)}"
                )
        if policy.principal and policy.principal not in groups:
            self._runner(
                f"mc admin policy attach {_ALIAS} {shlex.quote(name)} "
                f"--group {shlex.quote(policy.principal)}"
            )
        logger.info("minio_governance_apply_policy", policy=name, group=policy.principal)

    def revoke_policy(self, *, policy_id: str) -> None:
        """Detach a managed policy from its attached groups and remove it."""
        for group in self._attached_groups(policy_id):
            try:
                self._runner(
                    f"mc admin policy detach {_ALIAS} {shlex.quote(policy_id)} "
                    f"--group {shlex.quote(group)}"
                )
            except Exception:
                logger.warning(
                    "minio_governance_policy_detach_failed",
                    policy=policy_id,
                    group=group,
                    exc_info=True,
                )
        self._runner(f"mc admin policy remove {_ALIAS} {shlex.quote(policy_id)}")
        logger.info("minio_governance_revoke_policy", policy=policy_id)

    def check_access(self, *, principal: str, table_name: str, action: str) -> bool:
        """Conservatively simulate the group's policies, with explicit Deny precedence.

        Only unconditional Action/Resource statements with literal or * and ?
        patterns are supported. Unsupported IAM constructs, unreadable policies,
        or malformed evidence return False, never an affirmative access claim.
        This is not an attestation of a live MinIO request or user policies.
        """
        target_arn = table_name if table_name.startswith("arn:") else f"arn:aws:s3:::{table_name}"
        allowed = False
        try:
            entities = self._runner(
                f"mc admin policy entities {_ALIAS} --group {shlex.quote(principal)} --json"
            )
            names = _group_policy_names(_parse_json_lines(entities))
            for name in sorted(names):
                info = self._runner(f"mc admin policy info {_ALIAS} {shlex.quote(name)} --json")
                rows = _parse_json_lines(info)
                if not rows:
                    raise ValueError("Missing policy document")
                for row in rows:
                    document = _extract_document(row)
                    if document is None:
                        raise ValueError("Missing policy document")
                    effect = self._document_effect(document, target_arn, action)
                    if effect == "Deny":
                        return False
                    allowed = allowed or effect == "Allow"
        except Exception:
            logger.warning("minio_governance_access_unverified", group=principal, exc_info=True)
            return False
        return allowed

    @staticmethod
    def _document_effect(document: dict[str, Any], arn: str, action: str) -> str | None:
        """Evaluate the supported IAM subset without discarding deny evidence."""
        statements = document.get("Statement")
        if isinstance(statements, dict):
            statements = [statements]
        if not isinstance(statements, list):
            raise ValueError("Invalid IAM statements")
        allowed = False
        for statement in statements:
            if not isinstance(statement, dict) or set(statement) - {
                "Sid",
                "Effect",
                "Action",
                "Resource",
            }:
                raise ValueError("Unsupported IAM statement")
            effect = statement.get("Effect")
            if effect not in {"Allow", "Deny"}:
                raise ValueError("Invalid IAM effect")
            actions = _iam_patterns(statement.get("Action"))
            resources = _iam_patterns(statement.get("Resource"))
            if not any(_iam_matches(pattern.lower(), action.lower()) for pattern in actions):
                continue
            if any(_iam_matches(pattern, arn) for pattern in resources):
                if effect == "Deny":
                    return "Deny"
                allowed = True
        return "Allow" if allowed else None
