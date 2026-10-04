"""Saved governance policies exercised at the mutation-owning boundaries."""

from __future__ import annotations

import asyncio
import subprocess
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import dagster as dg
import pytest
from click.testing import CliRunner
from fastapi import HTTPException, Request

from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.capabilities.interfaces import IndependentReviewRequired
from phlo.identity.authority import IdentityAuthority
from phlo.plugins.observatory_settings import (
    ADMIN_SETTINGS_NAMESPACE,
    InMemorySettingsService,
    SettingsScope,
    StorageUnavailableError,
    get_settings_service,
)
from phlo_api.api import (
    operation_controls,
    v1_admin_identity,
    v1_assets,
    v1_branch_workflows,
    v1_staging,
)
from phlo_iceberg import tables
from phlo_iceberg.resource import IcebergResource
from phlo_dagster import wap_sensors
from phlo_nessie.resource import NessieResource
from phlo_polaris.promotion import PolarisSnapshotPromotionCatalog
from phlo_trino import cli as trino_cli
from phlo_trino import resource as trino_resource
from phlo_trino.sql_policy import require_governed_sql
from phlo_api.errors import BackendUnavailableError
from test_v1_branch_workflows import _enable_branch_mutations, _passing_checks
from test_v1_branch_workflows import branch_api as branch_api
from test_v1_staging import _async, _candidate, _git, _signature
from test_v1_staging import staging_api as staging_api


def _settings(**values):
    return get_settings_service().put(
        SettingsScope.GLOBAL,
        ADMIN_SETTINGS_NAMESPACE,
        {
            "version": 1,
            "values": {f"observatory.settings.audit.{key}": value for key, value in values.items()},
        },
    )


@pytest.fixture(autouse=True)
def restore_governance_defaults():
    yield
    _settings()


@pytest.mark.parametrize("enabled", [False, True])
def test_protection_reaches_all_direct_table_mutations(monkeypatch, enabled):
    _settings(protect_main=enabled)
    catalog = type("Catalog", (), {"drop_table": lambda self, name: None})()
    calls = []
    monkeypatch.setattr(tables, "get_catalog", lambda **kwargs: calls.append(kwargs) or catalog)
    if enabled:
        mutations = [
            lambda: tables.ensure_table("bronze.events", schema=None),
            lambda: tables.append_to_table("bronze.events", "/missing.parquet"),
            lambda: tables.merge_to_table("bronze.events", "/missing.parquet", unique_key="id"),
            lambda: tables.overwrite_table("bronze.events", "/missing.parquet"),
            lambda: tables.delete_rows_from_table("bronze.events", "id = 1"),
            lambda: tables.delete_table("bronze.events"),
            lambda: IcebergResource().compact(table_name="bronze.events"),
            lambda: IcebergResource().expire_snapshots(table_name="bronze.events", dry_run=False),
            lambda: IcebergResource().cleanup_orphan_files(
                table_name="bronze.events", dry_run=False
            ),
        ]
        for mutate in mutations:
            with pytest.raises(PermissionError, match="signed merge"):
                mutate()
        assert not calls
    else:
        tables.delete_table("bronze.events")
        assert calls == [{"ref": "main"}]
    tables.delete_table("bronze.events", ref="prod-feature")
    assert calls[-1] == {"ref": "prod-feature"}


@pytest.mark.parametrize("enabled", [False, True])
def test_merge_reason_is_enforced_before_provider_work(branch_api, monkeypatch, enabled):
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    _settings(require_merge_reason=enabled)
    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "blank-reason"},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": "unused",
            "message": " \t ",
        },
    )
    assert response.status_code == (422 if enabled else 200)
    if not enabled:
        assert response.json()["status"] == "conflict"  # mandatory checks still apply
    assert not any(path.endswith("/history/merge") for path in calls["paths"])


def test_changed_settings_revision_rejects_merge_before_signature(branch_api, monkeypatch):
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    _settings(require_merge_reason=False)

    async def change_policy(**_kwargs):
        store = get_settings_service()
        store.put(SettingsScope.GLOBAL, ADMIN_SETTINGS_NAMESPACE, {"version": 2, "values": {}})
        return _passing_checks()

    monkeypatch.setattr(v1_branch_workflows, "_premerge_checks", change_policy)
    monkeypatch.setattr(
        v1_admin_identity, "_consume_signature", lambda *_args: pytest.fail("signature consumed")
    )
    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "changed-policy"},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": "unused",
            "message": "Reviewed",
        },
    )
    assert response.status_code == 409
    assert "Governance settings changed" in response.text
    assert not any(
        body["isDryRun"] is False
        for method, path, _, body in calls["requests"]
        if path.endswith("/history/merge")
    )


@pytest.mark.parametrize("failure", ["corrupt", "unavailable"])
def test_merge_storage_failures_are_actionable_and_precede_provider_work(
    branch_api, monkeypatch, failure
):
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    if failure == "corrupt":
        _settings(retention_years=0)
    else:

        def unavailable():
            raise StorageUnavailableError("Unavailable test store")

        monkeypatch.setattr(operation_controls, "get_operational_settings", unavailable)
    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": failure},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": "unused",
            "message": "Reviewed",
        },
    )
    assert response.status_code == 503
    assert "restore durable settings storage" in response.text
    assert not calls["requests"]


@pytest.mark.parametrize(
    "enabled,reviewer,version,assurance,active,expected_status",
    [
        (False, None, "p2:p3", "mfa", True, 200),
        (True, None, "p2:p3", "mfa", True, 409),
        (True, "branch-reviewer", "p2:p3", "mfa", True, 409),
        (True, "independent", "stale", "mfa", True, 409),
        (True, "independent", "p2:p3", "session", True, 409),
        (True, "independent", "p2:p3", "mfa", False, 409),
        (True, "independent", "p2:p3", "mfa", True, 200),
    ],
)
def test_gold_review_toggle_validates_and_consumes_both_signatures(
    branch_api, monkeypatch, enabled, reviewer, version, assurance, active, expected_status
):
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    _settings(second_gold_reviewer=enabled)
    authority = IdentityAuthority(InMemorySettingsService())
    expected = SignatureRequest(
        signer_subject="branch-reviewer",
        meaning=SignatureMeaning.APPROVED,
        action="branch.merge",
        record_type="branch",
        record_id="prod:prod-feature->main",
        record_version="p2:p3",
        justification="Reviewed gold change",
    )
    approval = SignatureRecord.from_request(expected, authentication_assurance="mfa")
    authority.save_signature(approval)
    review = None
    if reviewer:
        authority.assign_roles(
            subject=reviewer,
            email=None,
            principal_type="user",
            roles=["operator"],
            expected_version=0,
            active=active,
        )
        review = SignatureRecord.from_request(
            replace(
                expected,
                signer_subject=reviewer,
                meaning=SignatureMeaning.REVIEWED,
                record_version=version,
            ),
            authentication_assurance=assurance,
        )
        authority.save_signature(review)
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_admin_identity, "_action_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        v1_branch_workflows, "_premerge_checks", lambda **_kwargs: _async(_passing_checks())
    )
    original = v1_branch_workflows._nessie

    async def nessie(method, path, **kwargs):
        if "/diff/" in path:
            return {
                "diffs": [
                    {
                        "key": {"elements": ["gold", "release_metrics"]},
                        "from": None,
                        "to": {"id": "new"},
                    }
                ]
            }
        return await original(method, path, **kwargs)

    monkeypatch.setattr(v1_branch_workflows, "_nessie", nessie)
    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": "gold-merge"},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": approval.signature_id,
            "reviewer_subject": reviewer,
            "review_signature_id": review.signature_id if review else None,
            "message": expected.justification,
        },
    )
    assert response.status_code == expected_status, response.text
    stored = authority.signatures("branch-reviewer")
    approval_state = next(
        item for item in stored if item.record.signature_id == approval.signature_id
    )
    assert (approval_state.consumed_at is not None) == (expected_status == 200)
    if review:
        item = next(
            item
            for item in authority.signatures(reviewer)
            if item.record.signature_id == review.signature_id
        )
        assert (item.consumed_at is not None) == (enabled and expected_status == 200)
    applied = [
        body
        for _, path, _, body in calls["requests"]
        if path.endswith("/history/merge") and not body["isDryRun"]
    ]
    assert bool(applied) == (expected_status == 200)


@pytest.mark.parametrize(
    "layer,group,key,want_review",
    [
        ("gold", "dbt", "models/fct_events", True),
        (None, "gold", "models/events", True),
        ("silver", "gold", "models/events", False),
        ("silver", "dbt", "models/fct_gold_sales", False),
        (None, "silver", "models/events", False),
        (None, "dbt", "models/fct_gold_sales", True),
        (None, "dbt", "models/events", True),
        ("invalid", "silver", "models/events", True),
    ],
)
def test_gold_classification_uses_explicit_metadata_not_model_names(
    branch_api, monkeypatch, layer, group, key, want_review
):
    _, _calls = branch_api
    entries = [{"label": "phlo/relation", "text": "orders.daily"}]
    if layer:
        entries.append({"label": "phlo/layer", "text": layer})
    node = {
        "id": "one",
        "isMaterializable": True,
        "assetKey": {"path": key.split("/")},
        "groupName": group,
        "repository": {"location": {"name": "prod_loc"}},
        "metadataEntries": entries,
        "assetMaterializations": [],
        "dependencyKeys": [],
    }
    response = {
        "data": {
            "repositoriesOrError": {
                "__typename": "RepositoryConnection",
                "nodes": [{"location": {"name": "prod_loc"}, "assetNodes": [node]}],
            }
        }
    }
    monkeypatch.setattr(v1_assets, "_graphql", lambda *_args: _async(response))
    source = v1_branch_workflows.BranchReference(
        env="prod", name="prod-feature", type="BRANCH", hash="p2", protected=False
    )
    target = source.model_copy(update={"name": "main", "hash": "p3"})
    request = Request({"type": "http", "query_string": b"env=prod"})
    assert (
        asyncio.run(v1_branch_workflows._requires_independent_review(request, source, target))
        == want_review
    )


@pytest.fixture
def selected_layers(monkeypatch):
    def asset(key, layers, location="prod_loc"):
        return {
            "assetKey": {"path": key.split("/")},
            "repository": {"location": {"name": location}},
            "groupName": "silver",
            "metadataEntries": [
                {"label": "phlo/relation", "text": "orders.daily"},
                *[{"label": "phlo/layer", "text": layer} for layer in layers],
            ],
        }

    def inventory(nodes, foreign=()):
        response = {
            "data": {
                "repositoriesOrError": {
                    "__typename": "RepositoryConnection",
                    "nodes": [
                        {"location": {"name": "prod_loc"}, "assetNodes": nodes},
                        {"location": {"name": "stage_loc"}, "assetNodes": list(foreign)},
                    ],
                }
            }
        }
        monkeypatch.setattr(v1_assets, "_graphql", lambda *_args: _async(response))

    return asset, inventory


@pytest.mark.parametrize(
    "case,status",
    [
        ("duplicate-key", 503),
        ("same-layer-twice", 503),
        ("gold-then-silver", 503),
        ("silver-then-gold", 503),
        ("conflicting-relations", 503),
        ("conflicting-relations-reversed", 503),
        ("wrong-repository", 502),
    ],
)
def test_ambiguous_selected_layers_stop_merge_before_signature(
    branch_api, monkeypatch, selected_layers, case, status
):
    client, calls = branch_api
    _enable_branch_mutations(monkeypatch)
    _settings(second_gold_reviewer=True)
    asset, inventory = selected_layers
    nodes = [asset("models/events", ["silver"])]
    if case == "duplicate-key":
        nodes.append(asset("models/events", ["silver"]))
    elif case == "same-layer-twice":
        nodes = [asset("models/events", ["silver", "silver"])]
    elif case in {"gold-then-silver", "silver-then-gold"}:
        nodes = [asset("models/events", case.split("-then-"))]
    elif case.startswith("conflicting-relations"):
        nodes.append(asset("models/another", ["bronze"]))
        if case.endswith("reversed"):
            nodes.reverse()
    else:
        nodes[0]["repository"]["location"]["name"] = "stage_loc"
    inventory(nodes)
    monkeypatch.setattr(
        v1_branch_workflows, "_premerge_checks", lambda **_kwargs: _async(_passing_checks())
    )
    monkeypatch.setattr(
        v1_branch_workflows,
        "_consume_independent_review",
        lambda *_args: pytest.fail("review consumed"),
    )
    monkeypatch.setattr(
        v1_admin_identity, "_consume_signature", lambda *_args: pytest.fail("approval consumed")
    )
    response = client.post(
        "/api/v1/branches/prod-feature/merge?env=prod",
        headers={"Idempotency-Key": case},
        json={
            "target": "main",
            "expected_source_hash": "p2",
            "expected_target_hash": "p3",
            "signature_id": "unused",
            "message": "Reviewed",
        },
    )
    assert response.status_code == status, response.text
    assert not any(
        not body["isDryRun"]
        for _, path, _, body in calls["requests"]
        if path.endswith("/history/merge")
    )


@pytest.mark.parametrize(
    "selected,foreign,want_review", [("silver", "gold", False), ("gold", "silver", True)]
)
def test_review_uses_selected_definition_not_foreign_duplicate(
    branch_api, selected_layers, selected, foreign, want_review
):
    asset, inventory = selected_layers
    inventory(
        [asset("models/events", [selected])], [asset("models/events", [foreign], "stage_loc")]
    )
    source = v1_branch_workflows.BranchReference(
        env="prod", name="prod-feature", type="BRANCH", hash="p2", protected=False
    )
    target = source.model_copy(update={"name": "main", "hash": "p3"})
    request = Request({"type": "http", "query_string": b"env=prod"})
    assert (
        asyncio.run(v1_branch_workflows._requires_independent_review(request, source, target))
        == want_review
    )


def test_review_rejects_truncated_diff_before_classification(branch_api, monkeypatch):
    original = v1_branch_workflows._diff

    async def truncated(*args):
        return (await original(*args)).model_copy(update={"truncated": True})

    monkeypatch.setattr(v1_branch_workflows, "_diff", truncated)
    monkeypatch.setattr(v1_assets, "_graphql", lambda *_args: pytest.fail("classification read"))
    source = v1_branch_workflows.BranchReference(
        env="prod", name="prod-feature", type="BRANCH", hash="p2", protected=False
    )
    target = source.model_copy(update={"name": "main", "hash": "p3"})
    request = Request({"type": "http", "query_string": b"env=prod"})
    with pytest.raises(BackendUnavailableError, match="complete branch diff"):
        asyncio.run(v1_branch_workflows._requires_independent_review(request, source, target))


def test_retention_prevents_capacity_deletion_and_keeps_every_segment(tmp_path, monkeypatch):
    path = tmp_path / "operations.jsonl"
    path.write_text("current\n", encoding="utf-8")
    oldest = path.with_name("operations.jsonl.1")
    oldest.write_text("preserved\n", encoding="utf-8")
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", "1")
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_FILES", "1")
    _settings(retention_years=7)
    with pytest.raises(StorageUnavailableError, match="at least 7 years"):
        operation_controls._rotate_audit_log(path)
    assert path.read_text(encoding="utf-8") == "current\n"
    assert oldest.read_text(encoding="utf-8") == "preserved\n"
    _settings(retention_years=None)
    operation_controls._rotate_audit_log(path)
    assert oldest.read_text(encoding="utf-8") == "current\n"


def test_signed_promotion_missing_key_has_no_side_effects(staging_api, monkeypatch):
    client, context = staging_api
    state = _candidate(client)
    _settings(sign_release_tags=True)
    authority = IdentityAuthority(InMemorySettingsService())
    signature = _signature(authority, state, "Reviewed release")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_staging, "_checks", lambda _state: _async([]))
    response = client.post(
        "/api/v1/staging/promotions?env=staging",
        headers={"Idempotency-Key": "missing-key"},
        json={
            "candidate_id": state["candidate_id"],
            "signature_id": signature.signature_id,
            "justification": "Reviewed release",
            "confirm": True,
        },
    )
    assert response.status_code == 503
    assert "user.signingkey" in response.text
    assert _git(context["prod"], "rev-parse", "HEAD") == context["base"]
    assert authority.signatures("release-manager")[0].consumed_at is None
    assert not _git(context["prod"], "tag", "--list")


@pytest.mark.parametrize("failure", ["missing-key", "changed-policy"])
def test_promotion_fails_closed_before_code_advance(staging_api, tmp_path, monkeypatch, failure):
    client, context = staging_api
    state = _candidate(client)
    _settings(sign_release_tags=True)
    prod = context["prod"]
    _git(prod, "config", "user.signingkey", str(tmp_path / "absent-signing-key"))
    _git(prod, "config", "gpg.format", "ssh")
    authority = IdentityAuthority(InMemorySettingsService())
    signature = _signature(authority, state, "Reviewed release")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)

    async def checks(_state):
        if failure == "changed-policy":
            get_settings_service().put(
                SettingsScope.GLOBAL, ADMIN_SETTINGS_NAMESPACE, {"version": 2, "values": {}}
            )
        return []

    monkeypatch.setattr(v1_staging, "_checks", checks)
    response = client.post(
        "/api/v1/staging/promotions?env=staging",
        headers={"Idempotency-Key": failure},
        json={
            "candidate_id": state["candidate_id"],
            "signature_id": signature.signature_id,
            "justification": "Reviewed release",
            "confirm": True,
        },
    )
    assert response.status_code == (409 if failure == "changed-policy" else 503)
    assert (
        "Governance settings changed" if failure == "changed-policy" else "no code was advanced"
    ) in response.text
    assert _git(prod, "rev-parse", "HEAD") == context["base"]
    assert not _git(prod, "tag", "--list")
    assert all(method == "GET" for method, *_rest in context["remote_calls"])
    if failure == "changed-policy":
        assert authority.signatures("release-manager")[0].consumed_at is None


@pytest.mark.parametrize("enabled,trust_retry", [(False, False), (True, False), (True, True)])
def test_promotion_signing_toggle_uses_real_verified_tag_evidence(
    staging_api, tmp_path, monkeypatch, enabled, trust_retry
):
    client, context = staging_api
    state = _candidate(client)
    prod = context["prod"]
    _settings(sign_release_tags=enabled)
    allowed = tmp_path / "allowed-signers"
    if enabled:
        key = tmp_path / "signing-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed.write_text(
            "release@example.invalid " + key.with_suffix(".pub").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        for name, value in {
            "user.signingkey": str(key),
            "user.name": "Release signer",
            "user.email": "release@example.invalid",
            "gpg.format": "ssh",
            "gpg.ssh.allowedSignersFile": str(allowed),
        }.items():
            _git(prod, "config", name, value)
    authority = IdentityAuthority(InMemorySettingsService())
    signature = _signature(authority, state, "Reviewed release")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_staging, "_checks", lambda _state: _async([]))
    reload_result = {
        "data": {
            "reloadRepositoryLocation": {
                "__typename": "WorkspaceLocationEntry",
                "loadStatus": "LOADED",
                "locationOrLoadError": {"name": "prod_loc"},
            }
        }
    }
    monkeypatch.setattr(v1_staging, "graphql_request", lambda *_args: _async(reload_result))
    body = {
        "candidate_id": state["candidate_id"],
        "signature_id": signature.signature_id,
        "justification": "Reviewed release",
        "confirm": True,
    }
    headers = {"Idempotency-Key": "signed-candidate"}
    retained_tag = None
    if trust_retry:
        trust = allowed.read_text(encoding="utf-8")
        allowed.write_text("", encoding="utf-8")
        rejected = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
        assert rejected.status_code == 503
        assert "new MFA approval" in rejected.text
        assert _git(prod, "rev-parse", "HEAD") == context["base"]
        retained_tag = _git(prod, "tag", "--list")
        assert signature.signature_id in _git(prod, "cat-file", "tag", retained_tag)
        allowed.write_text(trust, encoding="utf-8")
        signature = _signature(authority, state, "Reviewed release")
        body["signature_id"] = signature.signature_id
        headers = {"Idempotency-Key": "trusted-retry"}
    response = client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert (
        client.post("/api/v1/staging/promotions?env=staging", json=body, headers=headers).json()
        == response.json()
    )
    assert _git(prod, "rev-parse", "HEAD") == context["staging_head"]
    tag = response.json()["release_tag"]
    if enabled:
        _git(prod, "verify-tag", tag)
        assert _git(prod, "rev-parse", f"{tag}^{{commit}}") == context["staging_head"]
        assert signature.signature_id in _git(prod, "cat-file", "tag", tag)
        assert set(_git(prod, "tag", "--list").splitlines()) == (
            {tag, retained_tag} if retained_tag else {tag}
        )
        allowed.write_text("", encoding="utf-8")
        with pytest.raises(HTTPException):
            asyncio.run(v1_staging._git(prod, "verify-tag", tag))
    else:
        assert tag is None
        assert not _git(prod, "tag", "--list")


@pytest.mark.parametrize(
    "sql,ref,catalog,allowed",
    [
        ("SELECT * FROM iceberg.gold.events", "main", "iceberg", True),
        ("WITH x AS (SELECT 'INSERT INTO t' AS text) SELECT * FROM x", "main", "iceberg", True),
        ("INSERT INTO bronze.events VALUES (1)", "main", "iceberg", False),
        (
            'INSERT INTO "iceberg_prod-feature".bronze.events VALUES (1)',
            "prod-feature",
            "iceberg_prod-feature",
            True,
        ),
        (
            "INSERT INTO iceberg.bronze.events VALUES (1)",
            "prod-feature",
            "iceberg_prod-feature",
            False,
        ),
        ("EXPLAIN ANALYZE INSERT INTO bronze.events VALUES (1)", "main", "iceberg", False),
        (
            "SELECT * FROM TABLE(system.query(query => 'DELETE FROM gold.events'))",
            "main",
            "iceberg",
            False,
        ),
        ("SELECT 1; DELETE FROM bronze.events", "main", "iceberg", False),
    ],
)
def test_sql_protection_preserves_reads_and_proves_write_catalog(sql, ref, catalog, allowed):
    _settings(protect_main=True)
    if allowed:
        require_governed_sql(sql, ref=ref, catalog=catalog)
    else:
        with pytest.raises(PermissionError):
            require_governed_sql(sql, ref=ref, catalog=catalog)
    _settings(protect_main=False)
    require_governed_sql(sql, ref=ref, catalog=catalog)


def test_dbapi_cursor_and_batches_cannot_bypass_policy(monkeypatch):
    _settings(protect_main=True)
    native = MagicMock()
    native.cursor.return_value.description = None
    monkeypatch.setattr(trino_resource, "connect", lambda **_kwargs: native)
    resource = trino_resource.TrinoResource(ref="main", catalog="iceberg")
    connection = resource.get_connection()
    cursor = connection.cursor()
    assert cursor.connection is connection
    with pytest.raises(PermissionError):
        cursor.execute("DELETE FROM gold.events")
    with pytest.raises(PermissionError):
        cursor.executemany("INSERT INTO gold.events VALUES (?)", [(1,), (2,)])
    native.cursor.return_value.execute.assert_not_called()
    native.cursor.return_value.executemany.assert_not_called()
    cursor.execute("SELECT * FROM gold.events")
    native.cursor.return_value.execute.assert_called_once_with("SELECT * FROM gold.events", None)
    _settings(protect_main=False)
    cursor.execute("DELETE FROM gold.events")
    assert native.cursor.return_value.execute.call_count == 2


def test_cli_refuses_unrestricted_shell_and_writes_but_keeps_reads(monkeypatch):
    _settings(protect_main=True)
    monkeypatch.setattr(trino_cli, "enforce_surface_mutation_authorization", lambda *_args: None)
    monkeypatch.setattr(trino_cli, "_require_container_backend", lambda: None)
    monkeypatch.setattr(trino_cli, "_trino_exec_base", lambda **_kwargs: ["trino"])
    submitted = []
    monkeypatch.setattr(
        trino_cli,
        "run_command",
        lambda cmd, **_kwargs: (
            submitted.append(cmd)
            or subprocess.CompletedProcess(cmd, 0, stdout="read result\n", stderr="")
        ),
    )
    runner = CliRunner()
    read = runner.invoke(trino_cli.trino_group, ["query", "SELECT 1"])
    assert read.exit_code == 0, read.output
    assert len(submitted) == 1
    mutation = runner.invoke(trino_cli.trino_group, ["query", "DELETE FROM gold.events"])
    shell = runner.invoke(trino_cli.trino_group, [])
    assert mutation.exit_code == shell.exit_code == 1
    assert "protected" in mutation.output
    assert "unrestricted" in shell.output
    assert len(submitted) == 1


def test_catalog_providers_refuse_unprovable_automatic_review(monkeypatch):
    _settings(second_gold_reviewer=True)
    nessie = NessieResource("http://nessie.invalid")
    calls = []
    monkeypatch.setattr(nessie, "get_branch_hash", lambda name: calls.append(name))
    with pytest.raises(IndependentReviewRequired, match="governed review workflow"):
        nessie.merge_branch("pipeline-run-one")
    assert not calls
    store = SimpleNamespace(publication_lock=nullcontext)
    polaris = PolarisSnapshotPromotionCatalog(
        store=store, table_opener=lambda _name: pytest.fail("table mutation")
    )
    promoted = []
    monkeypatch.setattr(
        polaris, "_promote_candidates", lambda **kwargs: promoted.append(kwargs) or []
    )
    with pytest.raises(IndependentReviewRequired, match="retain the candidates"):
        polaris.promote_candidates(namespace="pipeline-run-one", release_id="one")
    assert not promoted
    _settings(second_gold_reviewer=False)
    assert not nessie.merge_branch("pipeline-run-one")
    assert calls == ["pipeline-run-one", "main"]
    assert polaris.promote_candidates(namespace="pipeline-run-one", release_id="one") == []
    assert len(promoted) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_catalog_merge_reason_reaches_provider_boundary(monkeypatch, enabled):
    _settings(require_merge_reason=enabled)
    catalog = NessieResource("http://nessie.invalid")
    monkeypatch.setattr(catalog, "get_branch_hash", lambda name: f"{name}-head")
    submitted = []
    monkeypatch.setattr(
        catalog,
        "_request",
        lambda *args, **kwargs: (
            submitted.append(kwargs["json"]) or SimpleNamespace(status_code=200, text="")
        ),
    )
    if enabled:
        for message in (None, " \t "):
            with pytest.raises(PermissionError, match="non-blank"):
                catalog.merge_branch("candidate", message=message)
        assert not submitted
    else:
        assert catalog.merge_branch("candidate")
        assert submitted[-1]["message"] == "Merge candidate into main"
    assert catalog.merge_branch("candidate", message="Approved validated batch")
    assert submitted[-1]["message"] == "Approved validated batch"
    assert catalog.merge_branch("candidate", target="staging")


@pytest.mark.parametrize(
    "strategy,race", [("branch", False), ("snapshot", False), ("branch", True)]
)
def test_wap_review_required_is_actionable_and_retained_past_cleanup(
    monkeypatch, tmp_path, strategy, race
):
    _settings(second_gold_reviewer=not race)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    import phlo.infrastructure

    monkeypatch.setattr(
        phlo.infrastructure, "load_wap_config", lambda: SimpleNamespace(strategy=strategy)
    )
    branch = "pipeline-run-one"
    run = SimpleNamespace(
        run_id="physical",
        status=dg.DagsterRunStatus.SUCCESS,
        tags={
            "phlo/wap_branch": branch,
            "phlo/run_id": "one",
            "phlo/ref": branch,
            "phlo/project_id": "project",
            "phlo/attempt": "1",
        },
    )
    instance = MagicMock()
    instance.get_runs.return_value = [run]
    context = MagicMock(instance=instance, cursor=None)
    catalog = MagicMock()
    catalog.get_branch_hash.return_value = "head"
    if race:
        catalog.merge_branch.side_effect = IndependentReviewRequired(
            "Independent review is required; retain this branch."
        )
    monkeypatch.setattr(wap_sensors, "_load_wap_catalog", lambda: catalog)
    monkeypatch.setattr(
        wap_sensors, "_verify_wap_launch_manifest", lambda *_args: ("one", {"strategy": strategy})
    )
    monkeypatch.setattr(wap_sensors, "_quality_evidence", lambda *_args, **_kwargs: ("quality", {}))
    monkeypatch.setattr(wap_sensors, "_all_checks_passed", lambda *_args: True)
    monkeypatch.setattr(wap_sensors, "_load_ref_query_catalog_manager", lambda: None)
    wap_sensors.write_wap_report("one", strategy=strategy, branch=branch)
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    report = wap_sensors._read_wap_report("one")
    assert report["status"] == "promotion_blocked"
    assert report["failure_reason"] == "independent_gold_review_required"
    assert report["review_required"] is True
    assert "retain" in report["review_message"].lower()
    assert report.get("merge_state") != "merge_started"
    assert catalog.merge_branch.call_count == int(race)
    catalog.promote_candidates.assert_not_called()
    catalog.delete_branch.assert_not_called()
    assert not instance.add_run_tags.called
    catalog.list_branches.return_value = [
        SimpleNamespace(name=branch, created_at=datetime.now(UTC) - timedelta(days=3))
    ]
    monkeypatch.setattr(wap_sensors, "_load_versioned_catalog", lambda: catalog)
    wap_sensors.wap_branch_cleanup_sensor._raw_fn(context)
    catalog.delete_branch.assert_not_called()
    monkeypatch.setattr(wap_sensors, "_load_snapshot_promotion_catalog", lambda: catalog)
    wap_sensors.wap_candidate_cleanup_sensor._raw_fn(context)
    catalog.abort_candidates.assert_not_called()
    if strategy == "branch":
        _settings(second_gold_reviewer=False, require_merge_reason=True)
        catalog.merge_branch.side_effect = None
        catalog.merge_branch.return_value = True
        monkeypatch.setattr(wap_sensors, "_reconcile_promoted_wap_run", lambda *_args: True)
        monkeypatch.setattr(
            wap_sensors,
            "_finalize_wap_promotion",
            lambda *_args, **_kwargs: wap_sensors.write_wap_report("one", status="promoted"),
        )
        wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
        catalog.merge_branch.assert_called_with(
            source=branch,
            target="main",
            message="Publish validated WAP run one: all required quality checks passed.",
        )
        report = wap_sensors._read_wap_report("one")
        assert report["status"] == "promoted"
        assert "review_required" not in report
        assert "review_message" not in report
        assert "failure_reason" not in report
