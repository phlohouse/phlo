"""Governance compilers for the remaining blessed backends.

Postgres, MinIO, and Nessie each get a ``GovernanceCompiler``. Canonical
pairs a compiler does not own skip to the surface layer; unknown pairs
raise so a policy naming no enforcing backend fails loudly.
"""

from __future__ import annotations

import json
import re
from typing import Any

from phlo.capabilities.interfaces import AccessPolicy
from phlo.rbac.compiler import (
    CompilerContext,
    GovernanceCompiler,
    TrinoCompiler,
    _validate_sql_identifier,
    _validate_sql_privilege,
    _validate_sql_resource_pattern,
)
from phlo.rbac.models import (
    BackendArtifact,
    CanonicalRBAC,
    PolicyChange,
    PolicyRule,
    SyncPlan,
    VerifyResult,
)

_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(value: str) -> str:
    """Reduce a resource or action name to a backend-safe artifact slug."""
    return _SAFE_SLUG_RE.sub("_", value).strip("_") or "all"


def _plan_content_aware(
    compiler: GovernanceCompiler,
    rbac: CanonicalRBAC,
    context: CompilerContext,
) -> SyncPlan:
    """Plan by name and content: a same-name artifact whose rendered
    content differs is replaced (delete + create), not skipped."""
    desired_by_name = {a.name: a for a in compiler.compile(rbac, context)}
    current_by_name = {a.name: a for a in compiler.read_current_state(context)}
    changes: list[PolicyChange] = []
    for name, artifact in desired_by_name.items():
        observed = current_by_name.get(name)
        if observed is not None and not compiler._artifact_equivalent(artifact, observed):
            changes.append(
                PolicyChange(
                    change_type="delete",
                    backend=compiler.backend_name,
                    artifact=observed,
                )
            )
            observed = None
        if observed is None:
            changes.append(
                PolicyChange(
                    change_type="create",
                    backend=compiler.backend_name,
                    artifact=artifact,
                    revert_id=compiler._generate_revert_id(),
                )
            )
    for name, artifact in current_by_name.items():
        if name not in desired_by_name:
            changes.append(
                PolicyChange(
                    change_type="delete",
                    backend=compiler.backend_name,
                    artifact=artifact,
                )
            )
    return SyncPlan(
        version_hash=rbac.version_hash or "",
        backend=compiler.backend_name,
        changes=tuple(changes),
    )


def _verify_content_aware(
    compiler: GovernanceCompiler,
    rbac: CanonicalRBAC,
    context: CompilerContext,
) -> VerifyResult:
    """Verify by name and content: same-name artifacts whose rendered
    content differs are reported as ``mismatched``."""
    desired_by_name = {a.name: a for a in compiler.compile(rbac, context)}
    current_by_name = {a.name: a for a in compiler.read_current_state(context)}
    missing = [a for n, a in desired_by_name.items() if n not in current_by_name]
    extra = [a for n, a in current_by_name.items() if n not in desired_by_name]
    mismatched = [
        a
        for n, a in desired_by_name.items()
        if (o := current_by_name.get(n)) is not None and not compiler._artifact_equivalent(a, o)
    ]
    return VerifyResult(
        backend=compiler.backend_name,
        in_sync=not missing and not extra and not mismatched,
        missing=tuple(missing),
        extra=tuple(extra),
        mismatched=tuple(mismatched),
    )


class PostgresCompiler(GovernanceCompiler):
    """Compiler for PostgreSQL role grants.

    Maps canonical dataset actions onto table, schema, and predefined-role
    grants: ``schema.table`` grants on one table, ``schema.*`` grants USAGE
    plus ALL TABLES plus default privileges, and ``*`` grants the PG14+
    predefined ``pg_read_all_data``/``pg_write_all_data`` roles.
    """

    POSTGRES_POLICY_PAIRS = frozenset(
        {
            ("dataset.read", "dataset"),
            ("dataset.query", "dataset"),
            ("dataset.write", "dataset"),
        }
    )

    ACTION_MAPPING: dict[str, tuple[str, ...]] = {
        "dataset.read": ("SELECT",),
        "dataset.query": ("SELECT",),
        "dataset.write": ("INSERT", "UPDATE", "DELETE"),
    }

    _READ_ALL_ROLE = "pg_read_all_data"
    _WRITE_ALL_ROLE = "pg_write_all_data"

    @property
    def backend_name(self) -> str:
        """Return the postgres backend identifier."""
        return "postgres"

    def supports_action(self, action: str) -> bool:
        """Return True when the action has a postgres grant mapping."""
        return action in self.ACTION_MAPPING

    def policy_applicability(self, action: str, resource_type: str) -> str:
        """Return whether a policy is postgres-backed, surface-only, or invalid."""
        pair = (action, resource_type)
        if pair in self.POSTGRES_POLICY_PAIRS:
            return "postgres"
        if pair in KNOWN_POLICY_PAIRS:
            return "surface"
        return "unsupported"

    def compile(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Compile canonical RBAC into postgres grant artifacts."""
        artifacts: list[BackendArtifact] = []
        for policy in rbac.policies.policies:
            artifacts.extend(self._compile_policy(policy))
        return artifacts

    def _compile_policy(self, policy: PolicyRule) -> list[BackendArtifact]:
        applicability = self.policy_applicability(policy.action, policy.resource_type)
        if applicability == "surface":
            return []
        if applicability == "unsupported":
            raise ValueError(
                f"postgres cannot compile policy {policy.policy_id!r} for "
                f"{policy.action}/{policy.resource_type}"
            )
        self._ensure_supported_policy_effect(policy)

        privileges = self.ACTION_MAPPING[policy.action]
        resource = policy.resource_id_pattern
        artifacts: list[BackendArtifact] = []
        for role_name in policy.principal_roles:
            _validate_sql_identifier(role_name, "role_name")
            if resource == "*":
                artifacts.append(self._all_data_artifact(role_name, policy))
                continue
            _validate_sql_resource_pattern(resource, "resource_id")
            if resource.endswith(".*"):
                artifacts.extend(
                    self._schema_artifacts(role_name, resource[:-2], privileges, policy)
                )
            else:
                for privilege in privileges:
                    _validate_sql_privilege(privilege)
                    artifacts.append(
                        self._artifact(
                            name=f"{role_name}__{resource}__{privilege}",
                            statement=(f"GRANT {privilege} ON TABLE {resource} TO {role_name}"),
                            role=role_name,
                            privilege=privilege,
                            resource=resource,
                            policy_id=policy.policy_id,
                            scope="table",
                        )
                    )
        return artifacts

    def _all_data_artifact(self, role_name: str, policy: PolicyRule) -> BackendArtifact:
        predefined = (
            self._WRITE_ALL_ROLE if policy.action == "dataset.write" else self._READ_ALL_ROLE
        )
        return self._artifact(
            name=f"{role_name}__*__{predefined}",
            statement=f"GRANT {predefined} TO {role_name}",
            role=role_name,
            privilege=predefined,
            resource="*",
            policy_id=policy.policy_id,
            scope="role_membership",
        )

    def _schema_artifacts(
        self,
        role_name: str,
        schema: str,
        privileges: tuple[str, ...],
        policy: PolicyRule,
    ) -> list[BackendArtifact]:
        _validate_sql_identifier(schema, "schema")
        artifacts = [
            self._artifact(
                name=f"{role_name}__{schema}.*__USAGE",
                statement=f"GRANT USAGE ON SCHEMA {schema} TO {role_name}",
                role=role_name,
                privilege="USAGE",
                resource=f"{schema}.*",
                policy_id=policy.policy_id,
                scope="schema_usage",
            )
        ]
        for privilege in privileges:
            _validate_sql_privilege(privilege)
            artifacts.append(
                self._artifact(
                    name=f"{role_name}__{schema}.*__{privilege}",
                    statement=(
                        f"GRANT {privilege} ON ALL TABLES IN SCHEMA {schema} TO {role_name}"
                    ),
                    role=role_name,
                    privilege=privilege,
                    resource=f"{schema}.*",
                    policy_id=policy.policy_id,
                    scope="all_tables",
                )
            )
            artifacts.append(
                self._artifact(
                    name=f"{role_name}__{schema}.*__{privilege}__DEFAULT",
                    statement=(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                        f"GRANT {privilege} ON TABLES TO {role_name}"
                    ),
                    role=role_name,
                    privilege=privilege,
                    resource=f"{schema}.*",
                    policy_id=policy.policy_id,
                    scope="default_privileges",
                )
            )
        return artifacts

    def _artifact(
        self,
        *,
        name: str,
        statement: str,
        role: str,
        privilege: str,
        resource: str,
        policy_id: str,
        scope: str,
    ) -> BackendArtifact:
        return BackendArtifact(
            backend=self.backend_name,
            artifact_type="grant",
            name=name,
            statement=statement,
            managed=True,
            metadata={
                "role": role,
                "privilege": privilege,
                "resource": resource,
                "policy_id": policy_id,
                "scope": scope,
            },
        )

    def read_current_state(
        self,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Read managed grants, schema usage, default privileges, and
        predefined-role memberships from postgres via the backend's
        ``list_policies`` rows. A failed listing propagates so readiness
        reports an observation failure instead of an empty state."""
        if self._backend is None:
            return []

        artifacts: list[BackendArtifact] = []
        for row in self._backend.list_policies():
            grantee = str(row.get("grantee", ""))
            if not grantee or not self._matches_managed(grantee, context):
                continue
            artifacts.append(self._row_artifact(row, grantee))
        return artifacts

    _ROW_SCOPE = {
        "table_grant": "table",
        "schema_usage": "schema_usage",
        "all_tables": "all_tables",
        "default_privileges": "default_privileges",
        "role_membership": "role_membership",
    }

    def _row_artifact(self, row: Any, grantee: str) -> BackendArtifact:
        kind = str(row.get("kind", "table_grant"))
        schema = str(row.get("schema", ""))
        table = str(row.get("table", ""))
        privilege = str(row.get("privilege", ""))
        if kind == "role_membership":
            name = f"{grantee}__*__{privilege}"
            resource = "*"
        elif kind in {"schema_usage", "all_tables", "default_privileges"}:
            suffix = "__DEFAULT" if kind == "default_privileges" else ""
            name = f"{grantee}__{schema}.*__{privilege}{suffix}"
            resource = f"{schema}.*"
        else:
            name = f"{grantee}__{schema}.{table}__{privilege}"
            resource = f"{schema}.{table}" if schema else table
        return BackendArtifact(
            backend=self.backend_name,
            artifact_type="grant",
            name=name,
            statement="",
            managed=True,
            metadata={
                "role": grantee,
                "privilege": privilege,
                "resource": resource,
                "scope": self._ROW_SCOPE.get(kind, "table"),
                "complete": row.get("complete", True),
                "inferred": kind == "all_tables",
            },
        )

    _PREDEFINED_ROLE_PRIVILEGES: dict[str, frozenset[str]] = {
        "pg_read_all_data": frozenset({"SELECT", "USAGE"}),
        "pg_write_all_data": frozenset({"INSERT", "UPDATE", "DELETE", "TRUNCATE"}),
    }

    def verify(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> VerifyResult:
        """Verify with coverage semantics: a concrete desired grant is
        satisfied by a broader observed grant (``schema.*`` complete
        coverage, or a predefined ``pg_*_all_data`` membership), and an
        observed grant not covered by any desired one is drift."""
        desired = self.compile(rbac, context)
        current = [
            artifact
            for artifact in self.read_current_state(context)
            if artifact.metadata.get("complete", True) is not False
        ]

        def covers(have: BackendArtifact, want: BackendArtifact) -> bool:
            meta_h, meta_w = have.metadata, want.metadata
            if meta_h.get("role") != meta_w.get("role"):
                return False
            priv_h, priv_w = str(meta_h.get("privilege")), str(meta_w.get("privilege"))
            covering = self._PREDEFINED_ROLE_PRIVILEGES.get(priv_h, frozenset())
            if priv_h != priv_w and priv_w not in covering:
                return False
            res_h, res_w = str(meta_h.get("resource")), str(meta_w.get("resource"))
            if res_h == res_w and meta_h.get("scope") != meta_w.get("scope"):
                return False
            if res_h == "*":
                return True
            if res_h == res_w:
                return True
            return bool(res_h.endswith(".*") and res_w.startswith(res_h[:-1]))

        missing = [want for want in desired if not any(covers(have, want) for have in current)]
        extra = [
            have
            for have in current
            if not have.metadata.get("inferred") and not any(covers(want, have) for want in desired)
        ]
        return VerifyResult(
            backend=self.backend_name,
            in_sync=not missing and not extra,
            missing=tuple(missing),
            extra=tuple(extra),
            mismatched=(),
        )

    def _apply_generic_policy_change(self, change: Any) -> None:
        """Apply a grant through the backend's AccessPolicy channel."""
        if self._backend is None:
            raise RuntimeError("No postgres governance backend registered")
        artifact = change.artifact
        meta = artifact.metadata
        if change.change_type == "create":
            self._backend.apply_policy(
                policy=AccessPolicy(
                    policy_id=artifact.name,
                    principal=str(meta.get("role", "")),
                    table_pattern=str(meta.get("resource", "")),
                    action=str(meta.get("privilege", "SELECT")),
                    effect="ALLOW",
                    columns=None,
                    row_filter=None,
                    data_masking=None,
                )
            )
        elif change.change_type == "delete":
            privilege = str(meta.get("privilege", ""))
            resource = str(meta.get("resource", ""))
            role = str(meta.get("role", ""))
            scope = str(meta.get("scope", "table"))
            self._backend.revoke_policy(policy_id=f"{scope}:{privilege}:{resource}:{role}")


class MinioCompiler(GovernanceCompiler):
    """Compiler for MinIO managed IAM policy documents.

    Each (role, action, resource-pattern) triple compiles to one named
    policy document listing S3 actions on the mapped object prefixes; the
    backend attaches the document to the MinIO group named for the role.
    The action is part of the artifact name so read and write grants on
    the same resource do not collide.
    """

    MINIO_POLICY_PAIRS = frozenset(
        {
            ("object.read", "object"),
            ("object.write", "object"),
            ("dataset.read", "dataset"),
            ("dataset.query", "dataset"),
            ("dataset.write", "dataset"),
        }
    )

    READ_ACTIONS = ("s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket")
    WRITE_ACTIONS = READ_ACTIONS + (
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
    )

    ACTION_MAPPING: dict[str, tuple[str, ...]] = {
        "object.read": READ_ACTIONS,
        "dataset.read": READ_ACTIONS,
        "dataset.query": READ_ACTIONS,
        "object.write": WRITE_ACTIONS,
        "dataset.write": WRITE_ACTIONS,
    }

    @property
    def backend_name(self) -> str:
        """Return the minio backend identifier."""
        return "minio"

    def supports_action(self, action: str) -> bool:
        """Return True when the action has a MinIO policy mapping."""
        return action in self.ACTION_MAPPING

    def policy_applicability(self, action: str, resource_type: str) -> str:
        """Return whether a policy is minio-backed, surface-only, or invalid."""
        pair = (action, resource_type)
        if pair in self.MINIO_POLICY_PAIRS:
            return "minio"
        if pair in KNOWN_POLICY_PAIRS:
            return "surface"
        return "unsupported"

    def _resource_prefix(self, policy: PolicyRule, context: CompilerContext) -> tuple[str, str]:
        """Map a canonical resource pattern to a (bucket, prefix) pair."""
        resource = policy.resource_id_pattern
        if policy.resource_type == "dataset":
            bucket = str(context.extra.get("warehouse_bucket") or "lake")
            return bucket, resource.replace(".", "/").replace("*", "")
        if "/" in resource:
            bucket, _, prefix = resource.partition("/")
            return bucket, prefix.rstrip("*")
        return resource, ""

    def compile(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Compile canonical RBAC into MinIO policy documents."""
        artifacts: list[BackendArtifact] = []
        for policy in rbac.policies.policies:
            applicability = self.policy_applicability(policy.action, policy.resource_type)
            if applicability == "surface":
                continue
            if applicability == "unsupported":
                raise ValueError(
                    f"minio cannot compile policy {policy.policy_id!r} for "
                    f"{policy.action}/{policy.resource_type}"
                )
            self._ensure_supported_policy_effect(policy)

            actions = self.ACTION_MAPPING[policy.action]
            bucket, prefix = self._resource_prefix(policy, context)
            _validate_sql_identifier(bucket, "bucket")
            document = self._policy_document(bucket, prefix, actions)
            for role_name in policy.principal_roles:
                _validate_sql_identifier(role_name, "role_name")
                name = (
                    f"phlo_{role_name}__{_slug(policy.resource_id_pattern)}__{_slug(policy.action)}"
                )
                artifacts.append(
                    BackendArtifact(
                        backend=self.backend_name,
                        artifact_type="policy_doc",
                        name=name,
                        statement=json.dumps(document, sort_keys=True),
                        managed=True,
                        metadata={
                            "role": role_name,
                            "policy_name": name,
                            "resource": policy.resource_id_pattern,
                            "bucket": bucket,
                            "prefix": prefix,
                            "actions": list(actions),
                            "policy_id": policy.policy_id,
                        },
                    )
                )
        return artifacts

    def _policy_document(
        self,
        bucket: str,
        prefix: str,
        actions: tuple[str, ...],
    ) -> dict[str, Any]:
        object_arn = f"arn:aws:s3:::{bucket}/{prefix}*" if prefix else f"arn:aws:s3:::{bucket}/*"
        bucket_arn = f"arn:aws:s3:::{bucket}"
        statements: list[dict[str, Any]] = []
        if "s3:ListBucket" in actions:
            list_stmt: dict[str, Any] = {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [bucket_arn],
            }
            if prefix:
                list_stmt["Condition"] = {"StringLike": {"s3:prefix": [f"{prefix}*"]}}
            statements.append(list_stmt)
        object_actions = [a for a in actions if a != "s3:ListBucket"]
        if object_actions:
            statements.append(
                {
                    "Effect": "Allow",
                    "Action": sorted(object_actions),
                    "Resource": [object_arn],
                }
            )
        return {"Version": "2012-10-17", "Statement": statements}

    def read_current_state(
        self,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Read managed MinIO policy documents via the backend's
        ``list_policies`` rows. A failed listing propagates so readiness
        reports an observation failure instead of an empty state."""
        if self._backend is None:
            return []
        artifacts: list[BackendArtifact] = []
        for row in self._backend.list_policies():
            name = str(row.get("policy_name", ""))
            if not name or not self._matches_managed(name, context):
                continue
            artifacts.append(
                BackendArtifact(
                    backend=self.backend_name,
                    artifact_type="policy_doc",
                    name=name,
                    statement=str(row.get("document", "")),
                    managed=True,
                    metadata={
                        "policy_name": name,
                        "role": str(row.get("role", "")),
                    },
                )
            )
        return artifacts

    def _artifact_equivalent(
        self,
        desired: BackendArtifact,
        current: BackendArtifact,
    ) -> bool:
        """A MinIO document is equivalent only when its content matches
        and it is attached to the group named for the canonical role —
        a detached or wrong-group document is drift, not state."""
        return desired.statement == current.statement and str(
            desired.metadata.get("role", "")
        ) == str(current.metadata.get("role", ""))

    def plan(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> SyncPlan:
        return _plan_content_aware(self, rbac, context)

    def verify(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> VerifyResult:
        return _verify_content_aware(self, rbac, context)

    def _apply_generic_policy_change(self, change: Any) -> None:
        """Apply or detach a managed policy document via the backend."""
        if self._backend is None:
            raise RuntimeError("No minio governance backend registered")
        artifact = change.artifact
        if change.change_type == "create":
            self._backend.apply_policy(
                policy=AccessPolicy(
                    policy_id=artifact.name,
                    principal=str(artifact.metadata.get("role", "")),
                    table_pattern=artifact.statement,
                    action="policy_doc",
                    effect="ALLOW",
                    columns=None,
                    row_filter=None,
                    data_masking=None,
                )
            )
        elif change.change_type == "delete":
            self._backend.revoke_policy(policy_id=artifact.name)


class NessieCompiler(GovernanceCompiler):
    """Compiler for Nessie authorization rules.

    Nessie authorization is static Quarkus configuration, so artifacts are
    ``nessie.server.authorization.rules.<name>=<expression>`` lines; the
    governance backend owns rendering them into the project's managed rules
    file that the generated stack mounts for Nessie to read at startup.
    """

    NESSIE_POLICY_PAIRS = frozenset(
        {
            ("catalog.read", "catalog"),
            ("catalog.manage", "catalog"),
            ("dataset.read", "dataset"),
            ("dataset.query", "dataset"),
            ("dataset.write", "dataset"),
            ("dataset.publish", "dataset"),
        }
    )

    ACTION_OPS: dict[str, tuple[str, ...]] = {
        "catalog.read": ("VIEW_REFERENCE", "READ_ENTITY_VALUE", "READ_LOG"),
        "catalog.manage": (
            "VIEW_REFERENCE",
            "CREATE_REFERENCE",
            "UPDATE_REFERENCE",
            "DELETE_REFERENCE",
            "ASSIGN_REFERENCE",
            "READ_ENTITY_VALUE",
            "READ_LOG",
        ),
        "dataset.read": ("VIEW_REFERENCE", "READ_ENTITY_VALUE"),
        "dataset.query": ("VIEW_REFERENCE", "READ_ENTITY_VALUE"),
        "dataset.write": ("UPDATE_ENTITY_VALUE",),
        "dataset.publish": ("UPDATE_REFERENCE",),
    }

    @property
    def backend_name(self) -> str:
        """Return the nessie backend identifier."""
        return "nessie"

    def supports_action(self, action: str) -> bool:
        """Return True when the action has a Nessie rule mapping."""
        return action in self.ACTION_OPS

    def policy_applicability(self, action: str, resource_type: str) -> str:
        """Return whether a policy is nessie-backed, surface-only, or invalid."""
        pair = (action, resource_type)
        if pair in self.NESSIE_POLICY_PAIRS:
            return "nessie"
        if pair in KNOWN_POLICY_PAIRS:
            return "surface"
        return "unsupported"

    def compile(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Compile canonical RBAC into Nessie rule artifacts."""
        artifacts: list[BackendArtifact] = []
        for policy in rbac.policies.policies:
            applicability = self.policy_applicability(policy.action, policy.resource_type)
            if applicability == "surface":
                continue
            if applicability == "unsupported":
                raise ValueError(
                    f"nessie cannot compile policy {policy.policy_id!r} for "
                    f"{policy.action}/{policy.resource_type}"
                )
            self._ensure_supported_policy_effect(policy)

            ops = self.ACTION_OPS[policy.action]
            for role_name in policy.principal_roles:
                _validate_sql_identifier(role_name, "role_name")
                expression = self._rule_expression(role_name, ops, policy)
                name = (
                    f"phlo_{role_name}__{_slug(policy.action)}__{_slug(policy.resource_id_pattern)}"
                )
                artifacts.append(
                    BackendArtifact(
                        backend=self.backend_name,
                        artifact_type="authz_rule",
                        name=name,
                        statement=f"nessie.server.authorization.rules.{name}={expression}",
                        managed=True,
                        metadata={
                            "role": role_name,
                            "rule_name": name,
                            "expression": expression,
                            "ops": list(ops),
                            "resource": policy.resource_id_pattern,
                            "policy_id": policy.policy_id,
                        },
                    )
                )
        return artifacts

    def _rule_expression(
        self,
        role_name: str,
        ops: tuple[str, ...],
        policy: PolicyRule,
    ) -> str:
        ops_clause = "op in (" + ",".join(f"'{op}'" for op in ops) + ")"
        clauses = [ops_clause, f"role=='{role_name}'"]
        if policy.resource_type == "dataset":
            resource = policy.resource_id_pattern
            _validate_sql_resource_pattern(resource, "resource_id")
            path = re.escape(resource).replace(r"\*", ".*")
            clauses.append(f"path=~'{path}'")
        return " && ".join(clauses)

    def read_current_state(
        self,
        context: CompilerContext,
    ) -> list[BackendArtifact]:
        """Read managed Nessie rules via the backend's ``list_policies``
        rows. A failed listing propagates so readiness reports an
        observation failure instead of an empty state."""
        if self._backend is None:
            return []
        artifacts: list[BackendArtifact] = []
        for row in self._backend.list_policies():
            name = str(row.get("policy_id", ""))
            if not name or not self._matches_managed(name, context):
                continue
            expression = str(row.get("rule", ""))
            artifacts.append(
                BackendArtifact(
                    backend=self.backend_name,
                    artifact_type="authz_rule",
                    name=name,
                    statement=f"nessie.server.authorization.rules.{name}={expression}",
                    managed=True,
                    metadata={"rule_name": name, "expression": expression},
                )
            )
        return artifacts

    def plan(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> SyncPlan:
        return _plan_content_aware(self, rbac, context)

    def verify(
        self,
        rbac: CanonicalRBAC,
        context: CompilerContext,
    ) -> VerifyResult:
        return _verify_content_aware(self, rbac, context)

    def _apply_generic_policy_change(self, change: Any) -> None:
        """Write or remove one managed rule via the backend."""
        if self._backend is None:
            raise RuntimeError("No nessie governance backend registered")
        artifact = change.artifact
        if change.change_type == "create":
            self._backend.apply_policy(
                policy=AccessPolicy(
                    policy_id=artifact.name,
                    principal=str(artifact.metadata.get("role", "")),
                    table_pattern=str(artifact.metadata.get("expression", "")),
                    action="authz_rule",
                    effect="ALLOW",
                    columns=None,
                    row_filter=None,
                    data_masking=None,
                )
            )
        elif change.change_type == "delete":
            self._backend.revoke_policy(policy_id=artifact.name)


KNOWN_POLICY_PAIRS = frozenset(
    set(TrinoCompiler.TRINO_POLICY_PAIRS)
    | set(TrinoCompiler.SURFACE_ONLY_POLICY_PAIRS)
    | set(PostgresCompiler.POSTGRES_POLICY_PAIRS)
    | set(MinioCompiler.MINIO_POLICY_PAIRS)
    | set(NessieCompiler.NESSIE_POLICY_PAIRS)
)
