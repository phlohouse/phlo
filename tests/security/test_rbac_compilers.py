"""Tests for the postgres, minio, and nessie governance compilers.

Covers canonical-pair routing, artifact emission, verify semantics
(coverage, content drift, attachment), and each backend's
apply/revoke/list/check_access behavior through fakes.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from phlo.capabilities.interfaces import AccessPolicy, GovernanceBackend
from phlo.rbac.compiler import COMPILER_REGISTRY, CompilerContext, get_compiler
from phlo.rbac.compilers import MinioCompiler, NessieCompiler, PostgresCompiler
from phlo.rbac.models import CanonicalRBAC, PoliciesConfig, RolesConfig


def _rbac(policies: list[dict[str, Any]], roles: dict[str, Any] | None = None) -> CanonicalRBAC:
    roles_config = RolesConfig.from_dict(
        {"version": 1, "roles": roles or {"phlo_analyst": {"inherits": []}}}
    )
    policies_config = PoliciesConfig.from_dict({"version": 1, "policies": policies})
    return CanonicalRBAC.from_configs(roles_config, policies_config)


def _policy(
    policy_id: str,
    action: str,
    resource_type: str,
    id_pattern: str,
    effect: str = "allow",
    roles: tuple[str, ...] = ("phlo_analyst",),
) -> dict[str, Any]:
    return {
        "policy_id": policy_id,
        "effect": effect,
        "principal": {"roles": list(roles)},
        "action": action,
        "resource": {"type": resource_type, "id_pattern": id_pattern},
    }


def _ctx(backend: str) -> CompilerContext:
    return CompilerContext(environment="test", backend_name=backend)


class _FakePostgres:
    """PostgresResource fake capturing executed SQL and serving canned rows."""

    def __init__(self, rows: dict[str, list[tuple]] | None = None) -> None:
        self.executed: list[str] = []
        self._rows = rows or {}
        self._default_rows: list[tuple] = []

    def execute(self, sql_stmt: str, params: tuple | None = None) -> None:
        self.executed.append(sql_stmt)

    def query(self, sql_stmt: str, params: tuple | None = None) -> list[tuple]:
        for marker, rows in self._rows.items():
            if marker in sql_stmt:
                return list(rows)
        return list(self._default_rows)

    def query_one(self, sql_stmt: str, params: tuple | None = None) -> tuple | None:
        rows = self.query(sql_stmt, params)
        return rows[0] if rows else None


class _FakePostgresGovernanceBackend:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.applied: list[AccessPolicy] = []
        self.revoked: list[str] = []
        self._rows = rows or []

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        return list(self._rows)

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        self.applied.append(policy)

    def revoke_policy(self, *, policy_id: str) -> None:
        self.revoked.append(policy_id)


class TestPostgresCompiler:
    def test_registered(self) -> None:
        assert COMPILER_REGISTRY["postgres"] is PostgresCompiler
        compiler = get_compiler("postgres")
        assert isinstance(compiler, PostgresCompiler)

    def test_table_grant_artifact(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])
        artifacts = PostgresCompiler().compile(rbac, _ctx("postgres"))

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.statement == "GRANT SELECT ON TABLE analytics.orders TO phlo_analyst"
        assert artifact.metadata["scope"] == "table"

    def test_schema_wildcard_emits_usage_all_tables_and_defaults(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.write", "dataset", "analytics.*")])
        artifacts = PostgresCompiler().compile(rbac, _ctx("postgres"))
        scopes = sorted(a.metadata["scope"] for a in artifacts)

        # USAGE + 3 privileges x (all_tables + default_privileges)
        assert scopes == [
            "all_tables",
            "all_tables",
            "all_tables",
            "default_privileges",
            "default_privileges",
            "default_privileges",
            "schema_usage",
        ]
        statements = {a.statement for a in artifacts}
        assert "GRANT USAGE ON SCHEMA analytics TO phlo_analyst" in statements
        assert any("ON ALL TABLES IN SCHEMA analytics" in s for s in statements)
        assert any("ALTER DEFAULT PRIVILEGES IN SCHEMA analytics" in s for s in statements)

    def test_catch_all_uses_predefined_roles(self) -> None:
        rbac = _rbac(
            [
                _policy("p1", "dataset.read", "dataset", "*"),
                _policy("p2", "dataset.write", "dataset", "*"),
            ]
        )
        artifacts = PostgresCompiler().compile(rbac, _ctx("postgres"))
        statements = {a.statement for a in artifacts}

        assert "GRANT pg_read_all_data TO phlo_analyst" in statements
        assert "GRANT pg_write_all_data TO phlo_analyst" in statements

    def test_surface_only_pair_skipped_unknown_raises(self) -> None:
        rbac = _rbac(
            [_policy("p1", "admin.manage", "admin", "dagster")],
        )
        assert PostgresCompiler().compile(rbac, _ctx("postgres")) == []

        rbac_unknown = _rbac(
            [_policy("p2", "nonsense.action", "nonsense", "x")],
        )
        with pytest.raises(ValueError, match="postgres cannot compile policy"):
            PostgresCompiler().compile(rbac_unknown, _ctx("postgres"))

    def test_deny_policy_rejected(self) -> None:
        rbac = _rbac(
            [_policy("p1", "dataset.read", "dataset", "a.b", effect="deny")],
        )
        with pytest.raises(ValueError, match="does not support canonical 'deny'"):
            PostgresCompiler().compile(rbac, _ctx("postgres"))

    def test_verify_in_sync(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "table_grant",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "table": "orders",
                    "privilege": "SELECT",
                }
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert result.in_sync
        assert not result.missing and not result.extra

    def test_object_policies_are_surface_not_postgres(self) -> None:
        """``object.*`` resources are MinIO territory: postgres must not
        claim them (bucket paths cannot compile to schema.table grants)."""
        compiler = PostgresCompiler()
        assert compiler.policy_applicability("object.read", "object") == "surface"
        rbac = _rbac([_policy("p1", "object.read", "object", "lake/orders")])
        assert compiler.compile(rbac, _ctx("postgres")) == []

    def test_verify_detects_missing_and_extra(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "table_grant",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "table": "stale_table",
                    "privilege": "DELETE",
                }
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert not result.in_sync
        assert [a.name for a in result.missing] == ["phlo_analyst__analytics.orders__SELECT"]
        assert [a.name for a in result.extra] == ["phlo_analyst__analytics.stale_table__DELETE"]

    def test_verify_all_tables_coverage(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "schema_usage",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "USAGE",
                },
                {
                    "kind": "all_tables",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "SELECT",
                    "complete": True,
                },
                {
                    "kind": "default_privileges",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "SELECT",
                },
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.*")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert result.in_sync

    def test_verify_incomplete_all_tables_is_missing(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "schema_usage",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "USAGE",
                },
                {
                    "kind": "all_tables",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "SELECT",
                    "complete": False,
                },
                {
                    "kind": "default_privileges",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "privilege": "SELECT",
                },
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.*")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert not result.in_sync
        assert any(a.metadata.get("scope") == "all_tables" for a in result.missing)

    def test_verify_suppresses_inferred_coverage_from_extra(self) -> None:
        """``all_tables`` coverage rows are derived from concrete grants —
        they satisfy desired wildcards but must not appear as extras; the
        concrete grants underneath still flag as drift."""
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "table_grant",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "table": "orders",
                    "privilege": "SELECT",
                },
                {
                    "kind": "all_tables",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "table": "",
                    "privilege": "SELECT",
                    "complete": True,
                },
                {
                    "kind": "table_grant",
                    "grantee": "phlo_analyst",
                    "schema": "analytics",
                    "table": "orders",
                    "privilege": "DELETE",
                },
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert not result.in_sync
        assert not result.missing
        assert [a.name for a in result.extra] == ["phlo_analyst__analytics.orders__DELETE"]

    def test_verify_predefined_role_covers_table_grant(self) -> None:
        """A predefined-role membership satisfies a desired table grant but
        is itself flagged as extra — it grants more than the model declares."""
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "role_membership",
                    "grantee": "phlo_analyst",
                    "privilege": "pg_read_all_data",
                }
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert not result.in_sync
        assert not result.missing
        assert [a.name for a in result.extra] == ["phlo_analyst__*__pg_read_all_data"]

    def test_verify_predefined_role_in_sync_when_desired(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "role_membership",
                    "grantee": "phlo_analyst",
                    "privilege": "pg_read_all_data",
                }
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "*")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert result.in_sync

    def test_unmanaged_grantees_ignored(self) -> None:
        backend = _FakePostgresGovernanceBackend(
            rows=[
                {
                    "kind": "table_grant",
                    "grantee": "other_role",
                    "schema": "analytics",
                    "table": "orders",
                    "privilege": "SELECT",
                }
            ]
        )
        compiler = PostgresCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "analytics.orders")])

        result = compiler.verify(rbac, _ctx("postgres"))

        assert not result.in_sync
        assert len(result.missing) == 1
        assert len(result.extra) == 0


class TestPostgresGovernanceBackend:
    def test_apply_table_grant(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        pg = _FakePostgres()
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg))
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__analytics.orders__SELECT",
                principal="phlo_analyst",
                table_pattern="analytics.orders",
                action="SELECT",
                effect="ALLOW",
            )
        )

        assert pg.executed == ['GRANT SELECT ON TABLE "analytics"."orders" TO "phlo_analyst"']

    def test_apply_schema_wildcard_shapes(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        pg = _FakePostgres()
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg))
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__analytics.*__USAGE",
                principal="phlo_analyst",
                table_pattern="analytics.*",
                action="USAGE",
                effect="ALLOW",
            )
        )
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__analytics.*__SELECT",
                principal="phlo_analyst",
                table_pattern="analytics.*",
                action="SELECT",
                effect="ALLOW",
            )
        )
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__analytics.*__SELECT__DEFAULT",
                principal="phlo_analyst",
                table_pattern="analytics.*",
                action="SELECT",
                effect="ALLOW",
            )
        )

        assert pg.executed == [
            'GRANT USAGE ON SCHEMA "analytics" TO "phlo_analyst"',
            'GRANT SELECT ON ALL TABLES IN SCHEMA "analytics" TO "phlo_analyst"',
            'ALTER DEFAULT PRIVILEGES IN SCHEMA "analytics" '
            'GRANT SELECT ON TABLES TO "phlo_analyst"',
        ]

    def test_apply_predefined_role(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        pg = _FakePostgres()
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg))
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__*__pg_read_all_data",
                principal="phlo_analyst",
                table_pattern="*",
                action="pg_read_all_data",
                effect="ALLOW",
            )
        )

        assert pg.executed == ['GRANT pg_read_all_data TO "phlo_analyst"']

    def test_revoke_scopes(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        pg = _FakePostgres()
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg))
        backend.revoke_policy(policy_id="table_grant:SELECT:analytics.orders:phlo_analyst")
        backend.revoke_policy(policy_id="default_privileges:SELECT:analytics.*:phlo_analyst")
        backend.revoke_policy(policy_id="role_membership:pg_read_all_data:*:phlo_analyst")

        assert pg.executed == [
            'REVOKE SELECT ON TABLE "analytics"."orders" FROM "phlo_analyst"',
            'ALTER DEFAULT PRIVILEGES IN SCHEMA "analytics" '
            'REVOKE SELECT ON TABLES FROM "phlo_analyst"',
            'REVOKE pg_read_all_data FROM "phlo_analyst"',
        ]

    def test_revoke_rejects_malformed_id(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        backend = PostgresGovernanceBackend(postgres=cast(Any, _FakePostgres()))
        with pytest.raises(ValueError, match="SCOPE:PRIVILEGE:RESOURCE:ROLE"):
            backend.revoke_policy(policy_id="bogus")

    def test_apply_rejects_injection(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        backend = PostgresGovernanceBackend(postgres=cast(Any, _FakePostgres()))
        with pytest.raises(ValueError):
            backend.apply_policy(
                policy=AccessPolicy(
                    policy_id="x",
                    principal='evil"; DROP TABLE t; --',
                    table_pattern="analytics.orders",
                    action="SELECT",
                    effect="ALLOW",
                )
            )

    def test_check_access(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        pg = _FakePostgres(rows={"has_table_privilege": [(True,)]})
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg))
        assert backend.check_access(
            principal="phlo_analyst", table_name="analytics.orders", action="SELECT"
        )
        pg_empty = _FakePostgres()
        backend = PostgresGovernanceBackend(postgres=cast(Any, pg_empty))
        assert not backend.check_access(
            principal="phlo_analyst", table_name="analytics.orders", action="SELECT"
        )

    def test_probe(self) -> None:
        from phlo_postgres.governance import PostgresGovernanceBackend

        assert PostgresGovernanceBackend(
            postgres=cast(Any, _FakePostgres(rows={"SELECT 1": [(1,)]}))
        ).probe()
        assert not PostgresGovernanceBackend(postgres=cast(Any, _FakePostgres())).probe()


class _FakeMcRunner:
    """Script runner fake: records scripts, serves canned stdout per marker."""

    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.scripts: list[str] = []
        self._outputs = outputs or {}
        self.fail = False

    def __call__(self, script: str) -> str:
        self.scripts.append(script)
        if self.fail:
            raise RuntimeError("mc exploded")
        for marker, output in self._outputs.items():
            if marker in script:
                return output
        return ""


class _FakeMinioGovernanceBackend:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.applied: list[AccessPolicy] = []
        self.revoked: list[str] = []
        self._rows = rows or []

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        return list(self._rows)

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        self.applied.append(policy)

    def revoke_policy(self, *, policy_id: str) -> None:
        self.revoked.append(policy_id)


class TestMinioCompiler:
    def test_registered(self) -> None:
        assert COMPILER_REGISTRY["minio"] is MinioCompiler
        assert isinstance(get_compiler("minio"), MinioCompiler)

    def test_dataset_read_policy_document(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        artifacts = MinioCompiler().compile(rbac, _ctx("minio"))

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.artifact_type == "policy_doc"
        assert artifact.name == "phlo_phlo_analyst__lake_orders__dataset_read"
        document = json.loads(artifact.statement)
        assert document["Version"] == "2012-10-17"
        actions = {action for stmt in document["Statement"] for action in stmt["Action"]}
        assert {"s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"} <= actions
        assert "s3:PutObject" not in actions
        resources = {resource for stmt in document["Statement"] for resource in stmt["Resource"]}
        assert "arn:aws:s3:::lake/lake/orders*" in resources or any(
            r.startswith("arn:aws:s3:::lake/") for r in resources
        )

    def test_dataset_write_includes_put_and_delete(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.write", "dataset", "lake.orders")])
        artifacts = MinioCompiler().compile(rbac, _ctx("minio"))
        document = json.loads(artifacts[0].statement)
        actions = {action for stmt in document["Statement"] for action in stmt["Action"]}
        assert {"s3:PutObject", "s3:DeleteObject"} <= actions

    def test_unknown_pair_raises_surface_skipped(self) -> None:
        rbac = _rbac([_policy("p1", "service.read", "service", "x")])
        assert MinioCompiler().compile(rbac, _ctx("minio")) == []
        rbac_unknown = _rbac([_policy("p2", "nope.nope", "nope", "x")])
        with pytest.raises(ValueError, match="minio cannot compile policy"):
            MinioCompiler().compile(rbac_unknown, _ctx("minio"))

    def test_verify_round_trip(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        desired = MinioCompiler().compile(rbac, _ctx("minio"))
        backend = _FakeMinioGovernanceBackend(
            rows=[
                {
                    "policy_name": desired[0].name,
                    "document": desired[0].statement,
                    "role": "phlo_analyst",
                }
            ]
        )
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))

        result = compiler.verify(rbac, _ctx("minio"))

        assert result.in_sync

    def test_verify_detects_drift(self) -> None:
        backend = _FakeMinioGovernanceBackend(rows=[])
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])

        result = compiler.verify(rbac, _ctx("minio"))

        assert not result.in_sync
        assert len(result.missing) == 1

    def test_read_and_write_on_same_resource_do_not_collide(self) -> None:
        rbac = _rbac(
            [
                _policy("p1", "dataset.read", "dataset", "lake.orders"),
                _policy("p2", "dataset.write", "dataset", "lake.orders"),
            ]
        )
        artifacts = MinioCompiler().compile(rbac, _ctx("minio"))
        names = [a.name for a in artifacts]

        assert len(set(names)) == 2
        assert names == [
            "phlo_phlo_analyst__lake_orders__dataset_read",
            "phlo_phlo_analyst__lake_orders__dataset_write",
        ]

        backend = _FakeMinioGovernanceBackend(rows=[])
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))
        plan = compiler.plan(rbac, _ctx("minio"))

        creates = [c.artifact.name for c in plan.changes if c.change_type == "create"]
        assert creates == names

    def test_verify_flags_detached_document_as_mismatched(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        desired = MinioCompiler().compile(rbac, _ctx("minio"))
        backend = _FakeMinioGovernanceBackend(
            rows=[
                {
                    "policy_name": desired[0].name,
                    "document": desired[0].statement,
                    "role": "",
                }
            ]
        )
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))

        result = compiler.verify(rbac, _ctx("minio"))

        assert not result.in_sync
        assert [a.name for a in result.mismatched] == [desired[0].name]
        assert not result.missing and not result.extra

        plan = compiler.plan(rbac, _ctx("minio"))
        sequence = [(c.change_type, c.artifact.name) for c in plan.changes]
        assert sequence == [("delete", desired[0].name), ("create", desired[0].name)]

    def test_verify_flags_stale_document_content_as_mismatched(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        desired = MinioCompiler().compile(rbac, _ctx("minio"))
        stale = json.loads(desired[0].statement)
        stale["Statement"][1]["Action"].append("s3:PutObject")
        backend = _FakeMinioGovernanceBackend(
            rows=[
                {
                    "policy_name": desired[0].name,
                    "document": json.dumps(stale, sort_keys=True),
                    "role": "phlo_analyst",
                }
            ]
        )
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))

        result = compiler.verify(rbac, _ctx("minio"))

        assert not result.in_sync
        assert len(result.mismatched) == 1

    def test_verify_tolerates_minio_reordered_action_list(self) -> None:
        """MinIO does not preserve Action order; a reordered document is
        still converged, not drift."""
        rbac = _rbac([_policy("p1", "dataset.write", "dataset", "lake.orders")])
        desired = MinioCompiler().compile(rbac, _ctx("minio"))
        stored = json.loads(desired[0].statement)
        stored["Statement"][1]["Action"] = list(reversed(stored["Statement"][1]["Action"]))
        backend = _FakeMinioGovernanceBackend(
            rows=[
                {
                    "policy_name": desired[0].name,
                    "document": json.dumps(stored, sort_keys=True),
                    "role": "phlo_analyst",
                }
            ]
        )
        compiler = MinioCompiler(backend=cast(GovernanceBackend, backend))

        result = compiler.verify(rbac, _ctx("minio"))

        assert result.in_sync

    def test_listing_failure_propagates(self) -> None:
        class _FailingBackend(_FakeMinioGovernanceBackend):
            def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
                raise RuntimeError("mc unreachable")

        compiler = MinioCompiler(backend=cast(GovernanceBackend, _FailingBackend()))
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])

        with pytest.raises(RuntimeError, match="mc unreachable"):
            compiler.read_current_state(_ctx("minio"))
        with pytest.raises(RuntimeError, match="mc unreachable"):
            compiler.verify(rbac, _ctx("minio"))


class TestMinioGovernanceBackend:
    def test_apply_creates_and_attaches_policy(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner()
        backend = MinioGovernanceBackend(runner=runner)
        document = {"Version": "2012-10-17", "Statement": []}
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_phlo_analyst__lake_orders",
                principal="phlo_analyst",
                table_pattern=json.dumps(document),
                action="policy_doc",
                effect="ALLOW",
            )
        )

        assert len(runner.scripts) == 4
        assert runner.scripts[0] == (
            "mc admin policy entities local --policy phlo_phlo_analyst__lake_orders --json"
        )
        assert runner.scripts[1] == (
            "mc admin policy remove local phlo_phlo_analyst__lake_orders >/dev/null 2>&1 || true"
        )
        assert "base64 -d" in runner.scripts[2]
        assert "mc admin policy create local phlo_phlo_analyst__lake_orders" in runner.scripts[2]
        assert runner.scripts[3] == (
            "mc admin policy attach local phlo_phlo_analyst__lake_orders --group phlo_analyst"
        )

    def test_apply_repairs_wrong_attachment(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner(
            outputs={
                "policy entities": json.dumps(
                    {"result": {"groupPolicyMappings": [{"group": "phlo_other", "policies": []}]}}
                )
            }
        )
        backend = MinioGovernanceBackend(runner=runner)
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_phlo_analyst__lake_orders",
                principal="phlo_analyst",
                table_pattern='{"Version": "2012-10-17", "Statement": []}',
                action="policy_doc",
                effect="ALLOW",
            )
        )

        assert runner.scripts[1] == (
            "mc admin policy detach local phlo_phlo_analyst__lake_orders --group phlo_other"
        )
        assert "policy remove" in runner.scripts[2]
        assert runner.scripts[-1] == (
            "mc admin policy attach local phlo_phlo_analyst__lake_orders --group phlo_analyst"
        )

    def test_apply_rejects_unmanaged_name(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        backend = MinioGovernanceBackend(runner=_FakeMcRunner())
        with pytest.raises(ValueError, match="phlo_"):
            backend.apply_policy(
                policy=AccessPolicy(
                    policy_id="unmanaged",
                    principal="phlo_analyst",
                    table_pattern="{}",
                    action="policy_doc",
                    effect="ALLOW",
                )
            )

    def test_revoke_detaches_then_removes(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner(
            outputs={
                "policy entities": json.dumps(
                    {"result": {"groupPolicyMappings": [{"group": "phlo_analyst", "policies": []}]}}
                )
            }
        )
        backend = MinioGovernanceBackend(runner=runner)
        backend.revoke_policy(policy_id="phlo_phlo_analyst__lake_orders")

        assert runner.scripts == [
            "mc admin policy entities local --policy phlo_phlo_analyst__lake_orders --json",
            "mc admin policy detach local phlo_phlo_analyst__lake_orders --group phlo_analyst",
            "mc admin policy remove local phlo_phlo_analyst__lake_orders",
        ]

    def test_revoke_detached_policy_just_removes(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner()
        MinioGovernanceBackend(runner=runner).revoke_policy(policy_id="phlo_orphan")
        assert runner.scripts == [
            "mc admin policy entities local --policy phlo_orphan --json",
            "mc admin policy remove local phlo_orphan",
        ]

    def test_list_policies_filters_managed(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        doc = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]}
        runner = _FakeMcRunner(
            outputs={
                "policy list": "\n".join(
                    [
                        json.dumps({"policy": "phlo_phlo_analyst__lake_orders"}),
                        json.dumps({"policy": "consoleAdmin"}),
                    ]
                ),
                "policy info": json.dumps({"policyDocument": doc}),
                "policy entities": json.dumps(
                    {"result": {"groupPolicyMappings": [{"group": "phlo_analyst", "policies": []}]}}
                ),
            }
        )
        rows = MinioGovernanceBackend(runner=runner).list_policies()

        assert len(rows) == 1
        assert rows[0]["policy_name"] == "phlo_phlo_analyst__lake_orders"
        assert rows[0]["role"] == "phlo_analyst"
        assert json.loads(rows[0]["document"]) == doc

    def test_list_policies_parses_current_mc_shapes(self) -> None:
        """Current mc nests the doc under policyInfo.Policy and emits
        attached group names under policyMappings[].groups — both were
        missed by the older shapes and produced false drift on a live run."""
        from phlo_minio.governance import MinioGovernanceBackend

        doc = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]}
        runner = _FakeMcRunner(
            outputs={
                "policy list": json.dumps({"policy": "phlo_phlo_analyst__lake_orders"}),
                "policy info": json.dumps(
                    {
                        "policy": "phlo_phlo_analyst__lake_orders",
                        "policyInfo": {
                            "PolicyName": "phlo_phlo_analyst__lake_orders",
                            "Policy": doc,
                        },
                    }
                ),
                "policy entities": json.dumps(
                    {
                        "result": {
                            "policyMappings": [
                                {
                                    "policy": "phlo_phlo_analyst__lake_orders",
                                    "users": None,
                                    "groups": ["phlo_analyst"],
                                }
                            ]
                        }
                    }
                ),
            }
        )
        rows = MinioGovernanceBackend(runner=runner).list_policies()

        assert rows[0]["role"] == "phlo_analyst"
        assert json.loads(rows[0]["document"]) == doc

    def test_list_policies_reports_detached_document(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner(
            outputs={
                "policy list": json.dumps({"policy": "phlo_phlo_analyst__lake_orders"}),
                "policy info": json.dumps({"policyDocument": {"Statement": []}}),
            }
        )
        rows = MinioGovernanceBackend(runner=runner).list_policies()

        assert rows[0]["role"] == ""

    def test_list_policies_propagates_mc_failure(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        runner = _FakeMcRunner()
        runner.fail = True
        with pytest.raises(RuntimeError, match="mc exploded"):
            MinioGovernanceBackend(runner=runner).list_policies()

    def test_check_access(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        doc = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": ["arn:aws:s3:::lake/lake/orders*"],
                }
            ],
        }
        runner = _FakeMcRunner(
            outputs={
                "policy entities": json.dumps(
                    {
                        "result": {
                            "groupPolicyMappings": [
                                {"policies": ["phlo_phlo_analyst__lake_orders"]}
                            ]
                        }
                    }
                ),
                "policy info": json.dumps({"policyDocument": doc}),
            }
        )
        backend = MinioGovernanceBackend(runner=runner)
        assert backend.check_access(
            principal="phlo_analyst",
            table_name="arn:aws:s3:::lake/lake/orders/part-1",
            action="s3:GetObject",
        )
        assert not backend.check_access(
            principal="phlo_analyst",
            table_name="arn:aws:s3:::lake/lake/orders/part-1",
            action="s3:PutObject",
        )

    def test_probe(self) -> None:
        from phlo_minio.governance import MinioGovernanceBackend

        assert MinioGovernanceBackend(runner=_FakeMcRunner()).probe()
        failing = _FakeMcRunner()
        failing.fail = True
        assert not MinioGovernanceBackend(runner=failing).probe()


class _FakeNessieGovernanceBackend:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.applied: list[AccessPolicy] = []
        self.revoked: list[str] = []
        self._rows = rows or []

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        return list(self._rows)

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        self.applied.append(policy)

    def revoke_policy(self, *, policy_id: str) -> None:
        self.revoked.append(policy_id)


class TestNessieCompiler:
    def test_registered(self) -> None:
        assert COMPILER_REGISTRY["nessie"] is NessieCompiler
        assert isinstance(get_compiler("nessie"), NessieCompiler)

    def test_catalog_rule_has_no_path(self) -> None:
        rbac = _rbac([_policy("p1", "catalog.read", "catalog", "*")])
        artifacts = NessieCompiler().compile(rbac, _ctx("nessie"))

        assert len(artifacts) == 1
        expression = artifacts[0].metadata["expression"]
        assert "role=='phlo_analyst'" in expression
        assert "'VIEW_REFERENCE'" in expression
        assert "path" not in expression
        assert artifacts[0].statement.startswith("nessie.server.authorization.rules.")

    def test_dataset_rule_scopes_path(self) -> None:
        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        artifacts = NessieCompiler().compile(rbac, _ctx("nessie"))
        expression = artifacts[0].metadata["expression"]

        assert r"path.matches('lake\.orders')" in expression

    def test_dataset_rule_escaped_path_does_not_overmatch(self) -> None:
        from phlo_nessie.governance import _evaluate_rule

        rbac = _rbac([_policy("p1", "dataset.read", "dataset", "lake.orders")])
        artifacts = NessieCompiler().compile(rbac, _ctx("nessie"))
        expression = artifacts[0].metadata["expression"]

        assert _evaluate_rule(
            expression, role="phlo_analyst", op="READ_ENTITY_VALUE", ref="main", path="lake.orders"
        )
        assert not _evaluate_rule(
            expression, role="phlo_analyst", op="READ_ENTITY_VALUE", ref="main", path="lakeXorders"
        )

    def test_unknown_pair_raises_surface_skipped(self) -> None:
        rbac = _rbac([_policy("p1", "settings.read", "settings", "x")])
        assert NessieCompiler().compile(rbac, _ctx("nessie")) == []
        rbac_unknown = _rbac([_policy("p2", "nope.nope", "nope", "x")])
        with pytest.raises(ValueError, match="nessie cannot compile policy"):
            NessieCompiler().compile(rbac_unknown, _ctx("nessie"))

    def test_verify_round_trip(self) -> None:
        rbac = _rbac([_policy("p1", "catalog.read", "catalog", "*")])
        desired = NessieCompiler().compile(rbac, _ctx("nessie"))
        backend = _FakeNessieGovernanceBackend(
            rows=[{"policy_id": desired[0].name, "rule": desired[0].metadata["expression"]}]
        )
        compiler = NessieCompiler(backend=cast(GovernanceBackend, backend))

        assert compiler.verify(rbac, _ctx("nessie")).in_sync


class TestNessieGovernanceBackend:
    def test_apply_writes_rules_file(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        backend = NessieGovernanceBackend(rules_path=rules_path)
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_phlo_analyst__catalog_read__all",
                principal="phlo_analyst",
                table_pattern="op in ['VIEW_REFERENCE'] && role=='phlo_analyst'",
                action="authz_rule",
                effect="ALLOW",
            )
        )

        content = rules_path.read_text()
        assert (
            "nessie.server.authorization.rules.phlo_phlo_analyst__catalog_read__all="
            "op in ['VIEW_REFERENCE'] && role=='phlo_analyst'" in content
        )

    def test_revoke_removes_rule(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        backend = NessieGovernanceBackend(rules_path=rules_path)
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_x__r",
                principal="phlo_x",
                table_pattern="op in ['VIEW_REFERENCE'] && role=='phlo_x'",
                action="authz_rule",
                effect="ALLOW",
            )
        )
        backend.revoke_policy(policy_id="phlo_x__r")

        assert "phlo_x__r" not in rules_path.read_text()

    def test_list_policies_parses_file(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        rules_path.write_text(
            "nessie.server.authorization.rules.phlo_a=op in ['VIEW_REFERENCE'] && role=='a'\n"
            "nessie.server.authorization.rules.unmanaged=op in ['X']\n"
        )
        rows = NessieGovernanceBackend(rules_path=rules_path).list_policies()

        assert rows == [{"policy_id": "phlo_a", "rule": "op in ['VIEW_REFERENCE'] && role=='a'"}]

    def test_check_access_evaluates_rules(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        backend = NessieGovernanceBackend(rules_path=rules_path)
        backend.apply_policy(
            policy=AccessPolicy(
                policy_id="phlo_analyst__read",
                principal="phlo_analyst",
                table_pattern=(
                    "op in ['VIEW_REFERENCE','READ_ENTITY_VALUE'] && "
                    "role=='phlo_analyst' && path.matches('lake.*')"
                ),
                action="authz_rule",
                effect="ALLOW",
            )
        )

        assert backend.check_access(
            principal="phlo_analyst",
            table_name="lake.orders",
            action="dataset.read",
        )
        assert not backend.check_access(
            principal="phlo_analyst",
            table_name="other.table",
            action="dataset.read",
        )
        assert not backend.check_access(
            principal="phlo_analyst",
            table_name="lake.orders",
            action="dataset.write",
        )
        assert not backend.check_access(
            principal="phlo_other",
            table_name="lake.orders",
            action="dataset.read",
        )

    def test_probe_requires_live_nessie(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        backend = NessieGovernanceBackend(
            rules_path=tmp_path / "missing.properties",
            live_check=lambda: False,
        )
        assert not backend.probe()
        assert backend.probe_reason() == "nessie_unreachable"

    def test_probe_missing_rules_file_ok_when_live(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        backend = NessieGovernanceBackend(
            rules_path=tmp_path / "missing.properties",
            live_check=lambda: True,
        )
        assert backend.probe()

    def test_probe_staged_rules_pending_restart(self, tmp_path) -> None:
        from datetime import UTC, datetime

        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        rules_path.write_text("nessie.server.authorization.rules.phlo_a=op in ['X']\n")
        backend = NessieGovernanceBackend(
            rules_path=rules_path,
            live_check=lambda: True,
            started_at=lambda: datetime(2000, 1, 1, tzinfo=UTC),
        )
        assert not backend.probe()
        assert backend.probe_reason() == "nessie_rules_pending_restart"

    def test_probe_loaded_rules(self, tmp_path) -> None:
        from datetime import UTC, datetime

        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        rules_path.write_text("nessie.server.authorization.rules.phlo_a=op in ['X']\n")
        backend = NessieGovernanceBackend(
            rules_path=rules_path,
            live_check=lambda: True,
            started_at=lambda: datetime.now(UTC),
        )
        assert backend.probe()

    def test_probe_load_state_unobservable(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        rules_path = tmp_path / "authz.properties"
        rules_path.write_text("nessie.server.authorization.rules.phlo_a=op in ['X']\n")
        backend = NessieGovernanceBackend(
            rules_path=rules_path,
            live_check=lambda: True,
            started_at=lambda: None,
        )
        assert not backend.probe()
        assert backend.probe_reason() == "nessie_load_state_unobservable"

    def test_list_policies_read_failure_propagates(self, tmp_path) -> None:
        from phlo_nessie.governance import NessieGovernanceBackend

        backend = NessieGovernanceBackend(
            rules_path=tmp_path,
            live_check=lambda: False,
        )
        with pytest.raises(OSError):
            backend.list_policies()
