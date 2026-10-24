"""Native executions pinned to immutable drafts and externally verified candidates.

The API's candidate provider owns physical branch/catalog provisioning. This
module runs only the supplied, verified candidate catalog; it never provisions,
merges, promotes or rewrites the saved SQL revision.
"""

from __future__ import annotations

import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from typing import Any

from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
from phlo_transform.assets import adapter_args
from phlo_transform.binary import require_binary, run_json
from phlo_transform.editor import (
    TransformEditorError,
    _artifact,
    _contained,
    _inputs,
    _namespace,
    _records,
    _uuid,
    _workspace_digest,
    read_model_draft,
)
from phlo_transform.settings import get_settings


def _storage(root: Path) -> str:
    return _namespace(root) + ".executions"


def _directory(root: Path, execution_id: str) -> Path:
    return _contained(
        root, root / ".phlo" / "transform-editor" / "executions" / _uuid(execution_id) / "workspace"
    )


def _store(
    root: Path, record: dict[str, Any], *, create: bool = False, expected_status: str | None = None
) -> None:
    def update(current):
        records = _records(current)
        existing = next(
            (item for item in records if item.get("execution_id") == record["execution_id"]), None
        )
        if expected_status and (existing is None or existing.get("status") != expected_status):
            raise TransformEditorError("Execution state changed; do not rerun it.", status=409)
        if create and len(records) >= 1000:
            raise TransformEditorError("Execution history limit reached.", status=409)
        if create and existing:
            raise TransformEditorError(
                "Execution identity is already recorded; read its result instead of rerunning.",
                status=409,
            )
        return {
            "schema_version": 1,
            "items": [
                *[item for item in records if item.get("execution_id") != record["execution_id"]],
                deepcopy(record),
            ],
        }

    get_settings_service().mutate(SettingsScope.GLOBAL, _storage(root), update)


def prepare_model_execution(
    root: Path, model: str, *, execution_id: str, **identity: Any
) -> dict[str, Any]:
    draft = read_model_draft(root, model, **identity)
    files = _inputs(_artifact(root, draft["draft_id"], draft["artifact_id"]))
    directory = _directory(root, execution_id)
    if directory.parent.exists():
        raise TransformEditorError(
            "Execution workspace already exists; read its result instead of rerunning.", status=409
        )
    directory.mkdir(parents=True)
    try:
        for name, content in files.items():
            path = _contained(directory, directory / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        record = {
            "execution_id": _uuid(execution_id),
            "model": model,
            **{
                key: draft[key]
                for key in ("actor", "env", "ref", "draft_id", "revision", "revision_digest")
            },
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "candidate_ref": None,
            "candidate_catalog": None,
            "report": None,
        }
        _store(root, record, create=True)
    except Exception:
        shutil.rmtree(directory.parent, ignore_errors=True)
        raise
    return record


def read_model_execution(
    root: Path, model: str, *, execution_id: str, **identity: Any
) -> dict[str, Any]:
    state = get_settings_service().get(SettingsScope.GLOBAL, _storage(root))
    found = next(
        (
            item
            for item in _records(state.settings if state else None)
            if item.get("execution_id") == _uuid(execution_id)
            and item.get("model") == model
            and all(item.get(key) == value for key, value in identity.items())
        ),
        None,
    )
    if found is None:
        raise TransformEditorError(
            "Execution was not found in this draft and principal scope.", status=404
        )
    if _workspace_digest(_inputs(_directory(root, execution_id))) != found["revision_digest"]:
        raise TransformEditorError("Execution source integrity verification failed.", status=409)
    return deepcopy(found)


def execute_model_draft(
    root: Path,
    model: str,
    *,
    execution_id: str,
    candidate_ref: str,
    candidate_catalog: str,
    connection: dict[str, str],
    candidate_binding: dict[str, Any],
    **identity: Any,
) -> dict[str, Any]:
    record = read_model_execution(root, model, execution_id=execution_id, **identity)
    if record["status"] != "prepared":
        raise TransformEditorError(
            "Execution already started; read its recorded outcome instead of rerunning.", status=409
        )
    operation_id = UUID(execution_id).hex
    if candidate_ref != f"{record['env']}-transform-{operation_id}" or candidate_ref in {
        "main",
        record["ref"],
    }:
        raise TransformEditorError(
            "A verified owned non-main candidate reference is required.", status=422
        )
    if (
        candidate_catalog != candidate_binding.get("catalog")
        or candidate_binding.get("execution_id") != operation_id
        or not candidate_catalog.endswith(f"_candidate_{operation_id}")
        or not all(character.isalnum() or character == "_" for character in candidate_catalog)
    ):
        raise TransformEditorError("A separate verified candidate catalog is required.", status=422)
    settings = get_settings(root).model_copy(
        update={
            "adapter": "trino",
            "trino_endpoint": connection["endpoint"],
            "trino_user": connection["user"],
            "trino_password": None,
            "trino_catalog": candidate_catalog,
            "timeout_seconds": min(get_settings(root).timeout_seconds, 90.0),
        }
    )
    binary = require_binary(root, settings)
    directory = _directory(root, execution_id)
    args = [
        "--jobs",
        "1",
        "--model-timeout",
        "60s",
        "--select",
        model,
        "--environment",
        candidate_ref,
        *adapter_args(None, settings, infer=False),
    ]
    environment = {
        "PHLO_TRINO_PASSWORD": connection.get("password"),
        # The verified API provider owns provisioning. Never let inherited
        # engine configuration silently invoke its independent provisioner.
        "PHLO_NESSIE_ENDPOINT": None,
        "PHLO_NESSIE_CATALOG_URI": None,
        "PHLO_NESSIE_TOKEN": None,
    }
    plan, planned = run_json(
        binary, ["plan", *args], settings=settings, root=directory, environment=environment
    )
    models = plan.get("models") or [] if plan else []
    if (
        not plan
        or not planned.ok
        or plan.get("blocked", True)
        or plan.get("seeds")
        or len(models) != 1
        or models[0].get("id") not in {model, "model://" + model.replace(".", "/")}
        or not str(models[0].get("target", "")).startswith(candidate_catalog + ".")
    ):
        raise TransformEditorError(
            "The native plan must contain only the selected model in the verified candidate catalog.",
            status=422,
        )
    if _workspace_digest(_inputs(directory)) != record["revision_digest"]:
        raise TransformEditorError("Execution source changed before native run.", status=409)
    record.update(
        status="running",
        candidate_ref=candidate_ref,
        candidate_catalog=candidate_catalog,
        candidate_binding=candidate_binding,
        plan=plan,
    )
    _store(root, record, expected_status="prepared")
    try:
        report, invoked = run_json(
            binary, ["run", *args], settings=settings, root=directory, environment=environment
        )
        if report is None:
            raise TransformEditorError(
                "The engine did not return a run report; the execution outcome is unknown.",
                status=502,
            )
        selected = [item for item in report.get("models") or [] if item.get("model") == model]
        if (
            len(selected) != 1
            or selected[0].get("target") != models[0]["target"]
            or report.get("environment") != candidate_ref
        ):
            raise TransformEditorError(
                "The engine returned execution evidence outside the pinned candidate plan.",
                status=502,
            )
        record.update(
            status="passed" if invoked.ok and report.get("status") == "passed" else "failed",
            report=report,
        )
        _store(root, record)
        return record
    except Exception:
        record.update(status="unknown")
        _store(root, record)
        raise


def list_model_executions(root: Path, model: str, **identity: Any) -> list[dict[str, Any]]:
    state = get_settings_service().get(SettingsScope.GLOBAL, _storage(root))
    return [
        read_model_execution(root, model, execution_id=item["execution_id"], **identity)
        for item in _records(state.settings if state else None)
        if item.get("model") == model
        and all(item.get(key) == value for key, value in identity.items())
    ]


def record_execution_preview(
    root: Path, model: str, *, execution_id: str, preview: dict[str, Any], **identity: Any
) -> dict[str, Any]:
    record = read_model_execution(root, model, execution_id=execution_id, **identity)
    if record["status"] != "passed":
        raise TransformEditorError(
            "Only successful native executions have a materialized result preview.", status=409
        )
    record["preview"] = preview
    record["candidate_result_hash"] = preview["ref_hash"]
    record["candidate_snapshot_id"] = preview["snapshot_id"]
    _store(root, record, expected_status="passed")
    return record


def record_execution_observation(
    root: Path, model: str, *, execution_id: str, candidate_result_hash: str, **identity: Any
) -> dict[str, Any]:
    record = read_model_execution(root, model, execution_id=execution_id, **identity)
    if record["status"] != "passed":
        raise TransformEditorError(
            "Only successful executions can record an output branch hash.", status=409
        )
    record["candidate_result_hash"] = candidate_result_hash
    _store(root, record, expected_status="passed")
    return record
