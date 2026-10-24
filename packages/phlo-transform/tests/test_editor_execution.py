"""Candidate execution pins full source revisions and refuses unsafe native targets."""

from uuid import uuid4

import pytest

from phlo_transform import editor, editor_execution as execution
from phlo_transform.binary import Invocation


def invocation(code=0):
    return Invocation(command=("engine",), returncode=code, stdout="", stderr="")


def save(root):
    source = editor.read_model_source(root, "review.inspection")
    return editor.save_model_draft(
        root,
        "review.inspection",
        actor="author",
        env="sandbox",
        ref="main",
        sql="SELECT batch_id FROM gold.batch_release_metrics",
        source_digest=source["source_digest"],
        workspace_digest=source["workspace_digest"],
    )


def prepared(editable, monkeypatch):
    root, _ = editable
    draft = save(root)
    identity = {
        "actor": "author",
        "env": "sandbox",
        "ref": "main",
        "draft_id": draft["draft_id"],
        "revision": 1,
    }
    execution_id = str(uuid4())
    record = execution.prepare_model_execution(
        root, "review.inspection", execution_id=execution_id, **identity
    )
    monkeypatch.setattr(execution, "get_settings", editor.get_settings)
    monkeypatch.setattr(execution, "require_binary", editor.require_binary)
    options = dict(
        execution_id=execution_id,
        candidate_ref=f"sandbox-transform-{execution_id.replace('-', '')}",
        candidate_catalog=f"iceberg_candidate_{execution_id.replace('-', '')}",
        candidate_binding={
            "execution_id": execution_id.replace("-", ""),
            "catalog": f"iceberg_candidate_{execution_id.replace('-', '')}",
        },
        connection={"endpoint": "http://trino:8080", "user": "phlo", "password": "private-test"},
        **identity,
    )
    return root, identity, options, record


def test_source_copy_and_principal_scope_remain_pinned(editable, monkeypatch):
    root, identity, options, record = prepared(editable, monkeypatch)
    with pytest.raises(editor.TransformEditorError, match="scope"):
        execution.read_model_execution(
            root,
            "review.inspection",
            execution_id=options["execution_id"],
            **{**identity, "actor": "another"},
        )
    with pytest.raises(editor.TransformEditorError, match="already exists"):
        execution.prepare_model_execution(
            root, "review.inspection", execution_id=options["execution_id"], **identity
        )
    (execution._directory(root, options["execution_id"]) / "phlo.toml").write_text("modified")
    with pytest.raises(editor.TransformEditorError, match="integrity"):
        execution.read_model_execution(
            root, "review.inspection", execution_id=options["execution_id"], **identity
        )


@pytest.mark.parametrize("unsafe", ["main-target", "extra-model", "seed"])
def test_native_plan_cannot_write_outside_the_selected_candidate_model(
    editable, monkeypatch, unsafe
):
    root, identity, options, record = prepared(editable, monkeypatch)
    models = [
        {"id": "review.inspection", "target": options["candidate_catalog"] + ".review.inspection"}
    ]
    if unsafe == "main-target":
        models[0]["target"] = "iceberg.review.inspection"
    if unsafe == "extra-model":
        models.append(
            {"id": "other.model", "target": options["candidate_catalog"] + ".other.model"}
        )
    calls = []

    def engine(binary, args, **kwargs):
        calls.append(args[0])
        return {
            "blocked": False,
            "models": models,
            "seeds": ["seed"] if unsafe == "seed" else [],
        }, invocation()

    monkeypatch.setattr(execution, "run_json", engine)
    with pytest.raises(editor.TransformEditorError, match="only the selected"):
        execution.execute_model_draft(root, "review.inspection", **options)
    assert calls == ["plan"]


@pytest.mark.parametrize("base_catalog", ["iceberg", "analytics"])
def test_native_success_keeps_real_identity_and_never_reexecutes(
    editable, monkeypatch, base_catalog
):
    root, identity, options, record = prepared(editable, monkeypatch)
    options["candidate_catalog"] = options["candidate_catalog"].replace(
        "iceberg_", base_catalog + "_", 1
    )
    options["candidate_binding"]["catalog"] = options["candidate_catalog"]
    calls = []
    target = options["candidate_catalog"] + ".review.inspection"

    def engine(binary, args, **kwargs):
        calls.append(args[0])
        assert "--nessie-endpoint" not in args and "private-test" not in args
        assert kwargs["environment"]["PHLO_NESSIE_ENDPOINT"] is None
        assert kwargs["environment"]["PHLO_TRINO_PASSWORD"] == "private-test"
        if args[0] == "plan":
            return {
                "id": "actual-plan",
                "blocked": False,
                "models": [{"id": "review.inspection", "target": target}],
            }, invocation()
        return {
            "run_id": "actual-run",
            "plan_id": "actual-run-plan",
            "status": "passed",
            "environment": options["candidate_ref"],
            "models": [
                {"model": "review.inspection", "target": target, "query_id": "actual-trino-query"}
            ],
        }, invocation()

    monkeypatch.setattr(execution, "run_json", engine)
    result = execution.execute_model_draft(root, "review.inspection", **options)
    assert result["status"] == "passed" and result["report"]["run_id"] == "actual-run"
    assert result["revision_digest"] == record["revision_digest"]
    with pytest.raises(editor.TransformEditorError, match="already started"):
        execution.execute_model_draft(root, "review.inspection", **options)
    assert calls == ["plan", "run"]
    observed = execution.record_execution_observation(
        root,
        "review.inspection",
        execution_id=options["execution_id"],
        candidate_result_hash="actual-output-hash",
        **identity,
    )
    assert observed["candidate_result_hash"] == "actual-output-hash"


@pytest.mark.parametrize("status", ["prepared", "running", "passed", "failed", "unknown"])
def test_execution_history_checks_each_visible_source_digest_before_returning_rows(
    editable, monkeypatch, status
):
    root, identity, options, record = prepared(editable, monkeypatch)
    record.update(
        status=status,
        report={"run_id": "recorded-run"},
        preview={"rows": [{"private": "retained"}]},
    )
    execution._store(root, record)
    assert (
        execution.list_model_executions(root, "review.inspection", **identity)[0]["report"][
            "run_id"
        ]
        == "recorded-run"
    )
    (execution._directory(root, options["execution_id"]) / "phlo.toml").write_text("tampered")
    assert (
        execution.list_model_executions(
            root, "review.inspection", **{**identity, "actor": "another"}
        )
        == []
    )
    assert execution.list_model_executions(root, "other.model", **identity) == []
    with pytest.raises(editor.TransformEditorError, match="integrity"):
        execution.list_model_executions(root, "review.inspection", **identity)
