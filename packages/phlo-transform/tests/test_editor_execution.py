"""Candidate execution pins full source revisions and refuses unsafe native targets."""

from uuid import uuid4
from copy import deepcopy

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


@pytest.mark.parametrize("status", ["prepared", "running", "unknown", "passed", "failed"])
def test_reclamation_preserves_tombstones_source_handoff_and_unresolved_fences(
    editable, monkeypatch, status
):
    monkeypatch.setattr(editor, "_MAX_RETAINED_REVISIONS", 1)
    monkeypatch.setattr(execution, "_MAX_RETAINED_EXECUTIONS", 1)
    root, identity, options, record = prepared(editable, monkeypatch)
    with pytest.raises(editor.TransformEditorError, match="history limit"):
        execution.prepare_model_execution(
            root, "review.inspection", execution_id=str(uuid4()), **identity
        )
    record.update(
        status=status,
        archived_at="2026-10-01T00:00:00Z",
        report={"run_id": "durable-report"},
        candidate_result_hash="durable-head",
        candidate_snapshot_id="123",
        operation_claim={"key_hash": "durable-fence"},
    )
    execution._store(root, record)
    editor.archive_model_draft(
        root, "review.inspection", archived=True, reason="Expired", **identity
    )
    with pytest.raises(editor.TransformEditorError, match="revision limit"):
        save(root)
    reclaim = dict(expected_revision_digest=record["revision_digest"], reason="Expired", **identity)
    directory = execution._directory(root, options["execution_id"])
    if status in {"prepared", "running", "unknown"}:
        with pytest.raises(editor.TransformEditorError, match="Unresolved"):
            execution.reclaim_model_execution(
                root, "review.inspection", execution_id=options["execution_id"], **reclaim
            )
        with pytest.raises(editor.TransformEditorError, match="Unresolved"):
            editor.reclaim_model_draft(root, "review.inspection", **reclaim)
        assert directory.exists()
        assert (
            execution.read_model_execution(
                root, "review.inspection", execution_id=options["execution_id"], **identity
            )["operation_claim"]
            == record["operation_claim"]
        )
        return
    reclaimed = execution.reclaim_model_execution(
        root, "review.inspection", execution_id=options["execution_id"], **reclaim
    )
    assert not directory.exists() and reclaimed["reclaimed_bytes"] > 0
    assert (
        reclaimed["report"] == record["report"]
        and reclaimed["operation_claim"] == record["operation_claim"]
    )
    assert (
        reclaimed["candidate_snapshot_id"] == "123"
        and reclaimed["candidate_result_hash"] == "durable-head"
    )
    assert (
        execution.read_model_execution(
            root, "review.inspection", execution_id=options["execution_id"], **identity
        )
        == reclaimed
    )
    draft = editor.reclaim_model_draft(root, "review.inspection", **reclaim)
    assert draft["retained_file_digests"][draft["path"]] == draft["sql_digest"]
    assert (
        editor.read_model_draft(root, "review.inspection", **identity)["sql"]
        == "SELECT batch_id FROM gold.batch_release_metrics"
    )
    assert not editor._artifact(root, draft["draft_id"], draft["artifact_id"]).exists()
    assert editor.list_model_drafts(
        root,
        "review.inspection",
        actor=identity["actor"],
        env=identity["env"],
        ref=identity["ref"],
        include_archived=True,
    )["items"][0]["reclaimed_at"]
    assert editor.reclaim_model_draft(root, "review.inspection", **reclaim) == draft
    with pytest.raises(editor.TransformEditorError, match="irreversible"):
        editor.archive_model_draft(
            root, "review.inspection", archived=False, reason="Restore", **identity
        )
    with pytest.raises(editor.TransformEditorError, match="irreversible"):
        execution.archive_model_execution(
            root,
            "review.inspection",
            execution_id=options["execution_id"],
            archived=False,
            reason="Restore",
            **identity,
        )

    fresh = save(root)
    fresh_identity = {**identity, "draft_id": fresh["draft_id"]}
    execution.prepare_model_execution(
        root, "review.inspection", execution_id=str(uuid4()), **fresh_identity
    )
    assert (
        len(
            execution.list_model_executions(
                root,
                "review.inspection",
                include_archived=True,
                actor="author",
                env="sandbox",
                ref="main",
            )
        )
        == 2
    )


@pytest.mark.parametrize("kind", ["draft", "execution"])
@pytest.mark.parametrize("level", ["wrapper", "record"])
@pytest.mark.parametrize("racing", [False, True])
def test_reclamation_refuses_future_reference_metadata_even_if_inserted_after_read(
    editable, monkeypatch, kind, level, racing
):
    from phlo.plugins.observatory_settings import SettingsScope

    root, identity, options, record = prepared(editable, monkeypatch)
    record.update(status="passed", archived_at="2026-01-01T00:00:00Z")
    execution._store(root, record)
    draft = editor.archive_model_draft(
        root, "review.inspection", archived=True, reason="Expired", **identity
    )
    namespace = editor._namespace(root) if kind == "draft" else execution._storage(root)
    service = editor.get_settings_service()
    mutate = service.mutate

    def inject(current):
        state = deepcopy(current)
        target = state if level == "wrapper" else state["items"][0]
        target["future_reference_fence"] = {"retain": True}
        return state

    if racing:

        def race(scope, name, change):
            if name == namespace:
                mutate(scope, name, inject)
            return mutate(scope, name, change)

        monkeypatch.setattr(service, "mutate", race)
    else:
        mutate(SettingsScope.GLOBAL, namespace, inject)
    with pytest.raises(editor.TransformEditorError):
        if kind == "draft":
            editor.reclaim_model_draft(
                root,
                "review.inspection",
                expected_revision_digest=record["revision_digest"],
                reason="Expired",
                **identity,
            )
        else:
            execution.reclaim_model_execution(
                root,
                "review.inspection",
                execution_id=options["execution_id"],
                expected_revision_digest=record["revision_digest"],
                reason="Expired",
                **identity,
            )
    state = service.get(SettingsScope.GLOBAL, namespace).settings
    target = state if level == "wrapper" else state["items"][0]
    assert target["future_reference_fence"] == {"retain": True}
    assert not state["items"][0].get("reclaim_started_at")
    assert execution._directory(root, options["execution_id"]).exists()
    assert editor._artifact(root, draft["draft_id"], draft["artifact_id"]).exists()


@pytest.mark.parametrize("kind", ["draft", "execution"])
def test_interrupted_reclamation_retry_preserves_original_bytes_without_duplicate_disposal(
    editable, monkeypatch, kind
):
    from phlo.plugins.observatory_settings import SettingsScope

    root, identity, options, record = prepared(editable, monkeypatch)
    record.update(status="passed", archived_at="2020-01-01T00:00:00Z")
    execution._store(root, record)
    draft = editor.archive_model_draft(
        root, "review.inspection", archived=True, reason="Expired", **identity
    )
    if kind == "draft":
        directory = editor._artifact(root, draft["draft_id"], draft["artifact_id"])
        namespace = editor._namespace(root)
    else:
        directory = execution._directory(root, options["execution_id"])
        namespace = execution._storage(root)
    original_bytes = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
    assert original_bytes > 0
    service = editor.get_settings_service()
    mutate = service.mutate
    rmtree = editor.shutil.rmtree
    disposals = []

    def remove(path):
        disposals.append(path)
        rmtree(path)

    def fail_completion(scope, name, change):
        def crash(current):
            updated = change(current)
            if any(item.get("reclaimed_at") for item in updated["items"]):
                raise OSError("Completion persistence failed after deletion")
            return updated

        return mutate(scope, name, crash if name == namespace else change)

    monkeypatch.setattr(editor.shutil, "rmtree", remove)
    monkeypatch.setattr(service, "mutate", fail_completion)

    def reclaim():
        kwargs = dict(
            expected_revision_digest=record["revision_digest"], reason="Expired", **identity
        )
        if kind == "draft":
            return editor.reclaim_model_draft(root, "review.inspection", **kwargs)
        return execution.reclaim_model_execution(
            root, "review.inspection", execution_id=options["execution_id"], **kwargs
        )

    with pytest.raises(OSError, match="after deletion"):
        reclaim()
    assert not directory.exists() and disposals == [directory]
    intent = service.get(SettingsScope.GLOBAL, namespace).settings["items"][0]
    assert intent["reclaim_payload_bytes"] == original_bytes
    assert not intent.get("reclaimed_at") and not intent.get("reclaimed_bytes")
    monkeypatch.setattr(service, "mutate", mutate)
    finished = reclaim()
    assert finished["reclaimed_at"] and finished["reclaimed_bytes"] == original_bytes
    assert reclaim() == finished
    assert disposals == [directory]


@pytest.mark.parametrize("kind", ["draft", "execution"])
@pytest.mark.parametrize("stage", ["before_provider_read", "before_atomic_intent"])
def test_rearchive_cannot_bypass_retention_validated_record(editable, monkeypatch, kind, stage):
    from phlo.plugins.observatory_settings import SettingsScope

    root, identity, options, record = prepared(editable, monkeypatch)
    record.update(status="passed", archived_at="2020-01-01T00:00:00Z")
    execution._store(root, record)
    draft = editor.archive_model_draft(
        root, "review.inspection", archived=True, reason="Expired", **identity
    )
    expected = draft if kind == "draft" else record
    namespace = editor._namespace(root) if kind == "draft" else execution._storage(root)
    service = editor.get_settings_service()
    mutate = service.mutate

    def rearchive(current):
        state = deepcopy(current)
        state["items"][0]["archived_at"] = "2099-10-23T00:00:00Z"
        return state

    if stage == "before_provider_read":
        mutate(SettingsScope.GLOBAL, namespace, rearchive)
    else:

        def race(scope, name, change):
            if name == namespace:
                mutate(scope, name, rearchive)
            return mutate(scope, name, change)

        monkeypatch.setattr(service, "mutate", race)
    with pytest.raises(editor.TransformEditorError, match="changed") as rejected:
        if kind == "draft":
            editor.reclaim_model_draft(
                root,
                "review.inspection",
                expected_record=expected,
                expected_revision_digest=expected["revision_digest"],
                reason="Expired",
                **identity,
            )
        else:
            execution.reclaim_model_execution(
                root,
                "review.inspection",
                execution_id=options["execution_id"],
                expected_record=expected,
                expected_revision_digest=expected["revision_digest"],
                reason="Expired",
                **identity,
            )
    assert rejected.value.status == 409
    stored = service.get(SettingsScope.GLOBAL, namespace).settings["items"][0]
    assert stored["archived_at"] == "2099-10-23T00:00:00Z"
    assert not stored.get("reclaim_started_at")
    assert execution._directory(root, options["execution_id"]).exists()
    assert editor._artifact(root, draft["draft_id"], draft["artifact_id"]).exists()


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
    preview = {
        "ref_hash": "actual-output-hash",
        "snapshot_id": "9007199254740993",
        "rows": [{"batch_id": "B71"}],
    }
    observed = execution.record_execution_preview(
        root, "review.inspection", execution_id=options["execution_id"], preview=preview, **identity
    )
    # Simulate an uncertain operation response after success evidence was durably retained.
    observed["status"] = "unknown"
    execution._store(root, observed)
    for changed in ({"ref_hash": "unrelated-head"}, {"snapshot_id": "9007199254740992"}):
        with pytest.raises(editor.TransformEditorError, match="stays fenced"):
            execution.reconcile_model_execution(
                root,
                "review.inspection",
                execution_id=options["execution_id"],
                preview={**preview, **changed},
                **identity,
            )
    resolved = execution.reconcile_model_execution(
        root, "review.inspection", execution_id=options["execution_id"], preview=preview, **identity
    )
    assert resolved["status"] == "passed" and resolved["report"]["run_id"] == "actual-run"
    archived = execution.archive_model_execution(
        root,
        "review.inspection",
        execution_id=options["execution_id"],
        archived=True,
        reason="Retain signed review evidence",
        **identity,
    )
    assert archived["archived_at"] and archived["candidate_result_hash"] == "actual-output-hash"
    assert execution.list_model_executions(root, "review.inspection", **identity) == []
    retained = execution.list_model_executions(
        root, "review.inspection", include_archived=True, **identity
    )
    assert retained[0]["preview"]["snapshot_id"] == "9007199254740993"
    with pytest.raises(editor.TransformEditorError, match="Archived executions"):
        execution.execute_model_draft(root, "review.inspection", **options)
    restored = execution.archive_model_execution(
        root,
        "review.inspection",
        execution_id=options["execution_id"],
        archived=False,
        reason="Reopen review",
        **identity,
    )
    assert restored["archived_at"] is None and calls == ["plan", "run"]


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


@pytest.mark.parametrize("status", ["prepared", "running", "unknown", "failed"])
def test_reconciliation_never_infers_success_from_table_existence_without_a_report(
    editable, monkeypatch, status
):
    root, identity, options, record = prepared(editable, monkeypatch)
    record.update(status=status)
    execution._store(root, record)
    monkeypatch.setattr(
        execution,
        "run_json",
        lambda *args, **kwargs: pytest.fail("Reconciliation must not run the engine"),
    )
    with pytest.raises(editor.TransformEditorError, match="stays fenced"):
        execution.reconcile_model_execution(
            root,
            "review.inspection",
            execution_id=options["execution_id"],
            preview={"ref_hash": "some-table-head", "snapshot_id": "1"},
            **identity,
        )
    assert (
        execution.read_model_execution(
            root, "review.inspection", execution_id=options["execution_id"], **identity
        )["status"]
        == status
    )
