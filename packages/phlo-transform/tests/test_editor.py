"""SQL drafts preserve source scope, immutable revisions and genuine engine boundaries."""

from pathlib import Path

import pytest

from phlo_transform import editor
from phlo_transform.binary import Invocation


def save(
    root: Path,
    sql: str = "SELECT batch_id FROM gold.batch_release_metrics",
    *,
    ref="main",
    **updates,
):
    source = editor.read_model_source(root, "review.inspection")
    return editor.save_model_draft(
        root,
        "review.inspection",
        actor="author",
        env="sandbox",
        ref=ref,
        sql=sql,
        source_digest=source["source_digest"],
        workspace_digest=source["workspace_digest"],
        **updates,
    )


def read(root: Path, draft, **scope):
    return editor.read_model_draft(
        root,
        "review.inspection",
        draft_id=draft["draft_id"],
        revision=draft["revision"],
        **{"actor": "author", "env": "sandbox", "ref": "main", **scope},
    )


def test_source_is_not_modified_and_each_draft_revision_remains_immutable(editable):
    root, workspace = editable
    original = (workspace.root / "transforms/inspection.sql").read_text()
    first = save(root)
    second = save(
        root,
        "SELECT count(*) FROM gold.batch_release_metrics",
        draft_id=first["draft_id"],
        expected_revision=1,
    )
    assert [first["revision"], second["revision"]] == [1, 2]
    assert read(root, first)["sql"] == "SELECT batch_id FROM gold.batch_release_metrics"
    assert read(root, second)["sql"] == "SELECT count(*) FROM gold.batch_release_metrics"
    assert (workspace.root / "transforms/inspection.sql").read_text() == original
    with pytest.raises(editor.TransformEditorError, match="draft changed"):
        save(root, draft_id=first["draft_id"], expected_revision=1)


def test_changed_source_or_workspace_is_rejected_before_save(editable):
    root, workspace = editable
    source = editor.read_model_source(root, "review.inspection")
    (workspace.root / "phlo.toml").write_text('[transform]\ndefault_catalog="other"\n')
    with pytest.raises(editor.TransformEditorError, match="workspace changed"):
        editor.save_model_draft(
            root,
            "review.inspection",
            actor="author",
            env="sandbox",
            ref="main",
            sql="SELECT1",
            source_digest=source["source_digest"],
            workspace_digest=source["workspace_digest"],
        )


@pytest.mark.parametrize("scope", [{"actor": "another"}, {"env": "prod"}, {"ref": "candidate"}])
def test_drafts_do_not_cross_actor_environment_or_ref(editable, scope):
    root, _ = editable
    draft = save(root)
    with pytest.raises(editor.TransformEditorError) as caught:
        read(root, draft, **scope)
    assert caught.value.status == 404


def test_revision_path_and_input_tampering_are_rejected(editable):
    root, _ = editable
    draft = save(root)
    path = editor._artifact(root, draft["draft_id"], draft["artifact_id"]) / draft["path"]
    path.write_text("SELECT secret FROM unrelated_catalog.private.data")
    with pytest.raises(editor.TransformEditorError, match="integrity"):
        read(root, draft)
    with pytest.raises(editor.TransformEditorError, match="identity"):
        editor.read_model_draft(
            root,
            "review.inspection",
            draft_id="../../outside",
            revision=1,
            actor="author",
            env="sandbox",
            ref="main",
        )


def test_compiler_source_paths_and_symlinks_cannot_escape_workspace(editable, monkeypatch):
    root, workspace = editable
    (root / "outside.sql").write_text("SELECT secret")
    model = workspace.models[0]
    from dataclasses import replace

    outside = replace(workspace, models=(replace(model, path="../outside.sql"),))
    monkeypatch.setattr(editor, "load_workspace", lambda root, settings: outside)
    with pytest.raises(editor.TransformEditorError, match="outside"):
        editor.read_model_source(root, "review.inspection")
    monkeypatch.setattr(editor, "load_workspace", lambda root, settings: workspace)
    (workspace.root / "transforms/inspection.sql").unlink()
    (workspace.root / "transforms/inspection.sql").symlink_to(root / "outside.sql")
    with pytest.raises(editor.TransformEditorError, match="outside"):
        editor.read_model_source(root, "review.inspection")


def invocation(code=0):
    return Invocation(command=("engine",), returncode=code, stdout="", stderr="")


@pytest.mark.parametrize("ref", ["main", "release_test"])
def test_compile_uses_engine_reports_and_never_applies_or_materializes(editable, monkeypatch, ref):
    root, _ = editable
    editor.get_settings(root).trino_user = "phlo_api_preview_sandbox"
    draft = save(root, ref=ref)
    calls = []

    def engine(binary, args, **kwargs):
        calls.append(args[0])
        if args[0] == "check":
            return {"diagnostics": []}, invocation()
        if args[0] == "list":
            return {"sources": [{"name": "gold.batch_release_metrics"}]}, invocation()
        assert "--trino-password" not in args
        assert "synthetic-test-password" not in args
        assert kwargs["environment"] == {
            "PHLO_TRINO_PASSWORD": "synthetic-test-password",
            "SSL_CERT_FILE": "/mounted/preview-ca.pem",
        }
        return {
            "id": "actual-engine-plan-id",
            "blocked": False,
            "diagnostics": [],
            "models": [
                {
                    "id": "review.inspection",
                    "compiled_sql": 'SELECT batch_id FROM "iceberg"."gold"."batch_release_metrics"',
                    "target": "iceberg.review.inspection",
                    "sql_hash": "sql-hash",
                    "desired_version": "version-hash",
                }
            ],
        }, invocation()

    monkeypatch.setattr(editor, "run_json", engine)
    result = editor.compile_model_draft(
        root,
        "review.inspection",
        actor="author",
        env="sandbox",
        ref=ref,
        draft_id=draft["draft_id"],
        revision=1,
        allowed_sources={"gold.batch_release_metrics"},
        required_ref=ref,
        required_catalog="iceberg",
        required_endpoint="http://trino:8080",
        required_user="phlo_api_preview_sandbox",
        trusted_ca_file="/mounted/preview-ca.pem",
    )
    assert calls == ["check", "list", "plan"]
    assert result["status"] == "ready" and result["plan_id"] == "actual-engine-plan-id"
    assert result["compiled_sql"].startswith("SELECT batch_id")
    assert result["materialization_available"] is False


def test_engine_diagnostics_are_retained_without_success_or_plan(editable, monkeypatch):
    root, _ = editable
    draft = save(root, "select broken syntax")
    diagnostics = [{"severity": "error", "code": "SQL001", "message": "Invalid SQL"}]
    monkeypatch.setattr(
        editor, "run_json", lambda *args, **kwargs: ({"diagnostics": diagnostics}, invocation(1))
    )
    result = editor.compile_model_draft(
        root,
        "review.inspection",
        actor="author",
        env="sandbox",
        ref="main",
        draft_id=draft["draft_id"],
        revision=1,
        allowed_sources=set(),
    )
    assert result["status"] == "failed" and result["diagnostics"] == diagnostics
    assert result["compiled_sql"] is None


def test_cross_catalog_or_unauthorized_source_is_rejected_before_planning(editable, monkeypatch):
    root, _ = editable
    draft = save(root, "SELECT * FROM system.runtime.queries")
    calls = []

    def engine(binary, args, **kwargs):
        calls.append(args[0])
        return (
            {"diagnostics": []}
            if args[0] == "check"
            else {"sources": [{"name": "system.runtime.queries"}]}
        ), invocation()

    monkeypatch.setattr(editor, "run_json", engine)
    with pytest.raises(editor.TransformEditorError) as caught:
        editor.compile_model_draft(
            root,
            "review.inspection",
            actor="author",
            env="sandbox",
            ref="main",
            draft_id=draft["draft_id"],
            revision=1,
            allowed_sources={"gold.batch_release_metrics"},
        )
    assert caught.value.status == 403 and calls == ["check", "list"]


@pytest.mark.parametrize(
    "constraints",
    [
        {"required_catalog": "preview_sandbox"},
        {
            "required_endpoint": "https://trino-preview:8443",
            "required_user": "phlo_api_preview_sandbox",
        },
        {"required_endpoint": "http://trino:8080", "required_user": "wrong_environment_identity"},
    ],
)
def test_engine_cannot_plan_through_an_unverified_catalog_or_connection(
    editable, monkeypatch, constraints
):
    root, _ = editable
    draft = save(root)
    calls = []

    def engine(binary, args, **kwargs):
        calls.append(args[0])
        return {"diagnostics": []}, invocation()

    monkeypatch.setattr(editor, "run_json", engine)
    with pytest.raises(editor.TransformEditorError) as caught:
        editor.compile_model_draft(
            root,
            "review.inspection",
            actor="author",
            env="sandbox",
            ref="main",
            draft_id=draft["draft_id"],
            revision=1,
            allowed_sources={"gold.batch_release_metrics"},
            **constraints,
        )
    assert caught.value.status == 503
    assert calls == []


@pytest.mark.parametrize(
    "binding",
    [
        {},
        {
            "required_ref": "main",
            "required_catalog": "iceberg",
            "required_endpoint": "http://trino:8080",
            "required_user": "preview",
        },
        {
            "required_ref": "release_test",
            "required_catalog": "other",
            "required_endpoint": "http://trino:8080",
            "required_user": "preview",
        },
    ],
)
def test_custom_reference_requires_its_exact_complete_preview_binding(editable, binding):
    root, _ = editable
    with pytest.raises(editor.TransformEditorError) as caught:
        editor.preview_settings(root, ref="release_test", **binding)
    assert caught.value.status == 503


def test_revision_history_is_principal_environment_ref_model_scoped_and_verified(editable):
    root, _ = editable
    first = save(root)
    second = save(
        root,
        "SELECT count(*) FROM gold.batch_release_metrics",
        draft_id=first["draft_id"],
        expected_revision=1,
    )
    scope = {"actor": "author", "env": "sandbox", "ref": "main"}
    history = editor.list_model_drafts(root, "review.inspection", **scope)
    assert [item["revision"] for item in history["items"]] == [2, 1]
    assert history["total"] == 2 and not history["truncated"]
    assert history["items"][0]["revision_digest"] == second["revision_digest"]
    assert all(
        "sql" not in item and "actor" not in item and "artifact_id" not in item
        for item in history["items"]
    )
    for changes in ({"actor": "another"}, {"env": "prod"}, {"ref": "other"}):
        assert (
            editor.list_model_drafts(root, "review.inspection", **{**scope, **changes})["items"]
            == []
        )
    assert editor.list_model_drafts(root, "other.model", **scope)["items"] == []
    (editor._artifact(root, second["draft_id"], second["artifact_id"]) / second["path"]).write_text(
        "SELECT private_data"
    )
    with pytest.raises(editor.TransformEditorError, match="integrity"):
        editor.list_model_drafts(root, "review.inspection", **scope)


def test_source_comparison_uses_actual_workspace_and_flags_changed_baseline(editable):
    root, workspace = editable
    draft = save(root)
    identity = {
        "actor": "author",
        "env": "sandbox",
        "ref": "main",
        "draft_id": draft["draft_id"],
        "revision": 1,
    }
    comparison = editor.compare_model_draft(root, "review.inspection", **identity)
    assert comparison["added_lines"] == comparison["removed_lines"] == 1
    assert comparison["baseline_matches_saved_source"] is True
    assert "-SELECT * FROM gold.batch_release_metrics" in comparison["unified_diff"]
    assert "+SELECT batch_id FROM gold.batch_release_metrics" in comparison["unified_diff"]
    (workspace.root / draft["path"]).write_text(draft["sql"])
    current = editor.compare_model_draft(root, "review.inspection", **identity)
    assert current["unified_diff"] == [] and current["added_lines"] == current["removed_lines"] == 0
    assert current["baseline_matches_saved_source"] is False
    with pytest.raises(editor.TransformEditorError) as forbidden:
        editor.compare_model_draft(root, "review.inspection", **{**identity, "actor": "another"})
    assert forbidden.value.status == 404


def test_source_comparison_does_not_hide_a_final_newline_revision(editable):
    root, _ = editable
    source = editor.read_model_source(root, "review.inspection")
    draft = save(root, source["sql"] + "\n")
    comparison = editor.compare_model_draft(
        root,
        "review.inspection",
        actor="author",
        env="sandbox",
        ref="main",
        draft_id=draft["draft_id"],
        revision=1,
    )
    assert comparison["added_lines"] == 1 and comparison["removed_lines"] == 0
    assert comparison["unified_diff"] and "+" in comparison["unified_diff"]
