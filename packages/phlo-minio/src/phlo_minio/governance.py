"""MinIO governance backend for managed IAM policy documents.

Wraps ``mc admin policy`` inside the generated MinIO container. Roles map
to MinIO groups; ``list_policies`` reports each managed document with the
group it is actually attached to so drift covers attachment, not just
the document.
"""

from __future__ import annotations

import base64
import json
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
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _extract_document(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the parsed policy document from an mc policy info row."""
    raw = row.get("policyDocument") or row.get("policy") or ""
    if isinstance(raw, dict) and raw.get("Statement"):
        return raw
    if isinstance(raw, str) and raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and parsed.get("Statement"):
            return parsed
    return None


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
        """Create the policy document and attach it to the role's group.

        A stale or detached document under the same name is detached and
        removed first so apply also repairs drift, not just creates.
        """
        name = policy.policy_id
        if not name or not name.startswith(_MANAGED_PREFIX):
            raise ValueError(f"Managed policy names must start with {_MANAGED_PREFIX!r}")
        for group in self._attached_groups(name):
            self._runner(
                f"mc admin policy detach {_ALIAS} {shlex.quote(name)} --group {shlex.quote(group)}"
            )
        self._runner(f"mc admin policy remove {_ALIAS} {shlex.quote(name)} >/dev/null 2>&1 || true")
        document = json.loads(policy.table_pattern)
        encoded = base64.b64encode(json.dumps(document, sort_keys=True).encode("utf-8")).decode(
            "ascii"
        )
        path = f"/tmp/{name}.json"
        self._runner(
            f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)} && "
            f"mc admin policy create {_ALIAS} {shlex.quote(name)} {shlex.quote(path)}"
        )
        if policy.principal:
            self._runner(
                f"mc admin policy attach {_ALIAS} {shlex.quote(name)} "
                f"--group {shlex.quote(policy.principal)}"
            )
        logger.info(
            "minio_governance_apply_policy",
            policy=name,
            group=policy.principal,
        )

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
        """Return whether the principal's group has a policy covering the
        object ARN prefix with the requested S3 action."""
        try:
            entities = self._runner(
                f"mc admin policy entities {_ALIAS} --group {shlex.quote(principal)} --json"
            )
        except Exception:
            logger.warning("minio_governance_entities_failed", group=principal, exc_info=True)
            return False
        policy_names: list[str] = []
        for row in _parse_json_lines(entities):
            result = row.get("result") or {}
            for mapping in result.get("groupPolicyMappings") or []:
                for name in mapping.get("policies") or []:
                    if isinstance(name, str):
                        policy_names.append(name)
        target_arn = table_name if table_name.startswith("arn:") else f"arn:aws:s3:::{table_name}"
        for name in policy_names:
            try:
                info = self._runner(f"mc admin policy info {_ALIAS} {shlex.quote(name)} --json")
            except Exception:
                continue
            for row in _parse_json_lines(info):
                document = _extract_document(row)
                if document is not None and self._document_allows(document, target_arn, action):
                    return True
        return False

    @staticmethod
    def _document_allows(document: dict[str, Any], arn: str, action: str) -> bool:
        """Return whether a parsed IAM-style document allows action on arn."""
        for statement in document.get("Statement") or []:
            if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
                continue
            actions = statement.get("Action") or []
            resources = statement.get("Resource") or []
            if isinstance(actions, str):
                actions = [actions]
            if isinstance(resources, str):
                resources = [resources]
            if not any(a == action or a == "s3:*" or a == "*" for a in actions):
                continue
            for resource in resources:
                if (
                    resource == "*"
                    or arn == resource
                    or (resource.endswith("*") and arn.startswith(resource[:-1]))
                ):
                    return True
        return False
