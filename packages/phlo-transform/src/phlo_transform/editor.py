"""Contained SQL draft revisions and genuine read-only engine compilation.

Drafts are immutable workspace snapshots, not Git branches or published source
files. The Rust engine owns compilation and planning; this adapter only keeps
source identity, storage and invocation boundaries intact.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
from phlo_transform.assets import adapter_args, provider_is_active
from phlo_transform.binary import require_binary, run_json
from phlo_transform.settings import TransformSettings, get_settings
from phlo_transform.workspace import TransformWorkspace, load_workspace

_MAX_SOURCE_BYTES = 256 * 1024
_MAX_WORKSPACE_BYTES = 16 * 1024 * 1024
_MAX_RETAINED_REVISIONS = 1000
_INPUT_SUFFIXES = {".sql", ".toml", ".yaml", ".yml", ".json", ".csv"}
_SKIPPED = {".git", ".phlo", ".venv", "node_modules", "target", "dbt_packages", "__pycache__"}


class TransformEditorError(RuntimeError):
    """A client-safe failure to read, save or compile a specific SQL revision."""

    def __init__(self, message: str, *, reason: str = "invalid", status: int = 422) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _workspace(root: Path) -> TransformWorkspace:
    if not provider_is_active(root):
        raise TransformEditorError("Phlo Transform is not the active provider.", status=503)
    workspace = load_workspace(root, get_settings(root))
    if workspace is None:
        raise TransformEditorError("No native Phlo Transform workspace is configured.", status=503)
    _contained(root, workspace.root)
    return workspace


def _contained(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise TransformEditorError("The model source is outside the project workspace.")
    return resolved


def _inputs(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _SKIPPED.intersection(relative.parts) or path.suffix.lower() not in _INPUT_SUFFIXES:
            continue
        if path.is_symlink():
            raise TransformEditorError(
                "Symbolic links are not supported in editable transform inputs."
            )
        if not path.is_file():
            continue
        content = _contained(root, path).read_bytes()
        total += len(content)
        if total > _MAX_WORKSPACE_BYTES or len(files) >= 1000:
            raise TransformEditorError("The transform workspace exceeds the editor snapshot limit.")
        files[relative.as_posix()] = content
    return files


def _workspace_digest(files: dict[str, bytes]) -> str:
    return _digest(
        json.dumps(
            [(name, _digest(content)) for name, content in sorted(files.items())],
            separators=(",", ":"),
        ).encode()
    )


def _source(root: Path, model: str) -> tuple[TransformWorkspace, str, dict[str, bytes]]:
    workspace = _workspace(root)
    selected = workspace.model(model)
    if selected is None or not selected.path:
        raise TransformEditorError("The selected model has no editable SQL source.", status=404)
    path = _contained(workspace.root, workspace.root / selected.path)
    if path.suffix.lower() != ".sql" or path.is_symlink():
        raise TransformEditorError("Only contained SQL model files can be edited.")
    relative = path.relative_to(workspace.root.resolve()).as_posix()
    files = _inputs(workspace.root)
    if relative not in files or len(files[relative]) > _MAX_SOURCE_BYTES:
        raise TransformEditorError("The selected SQL source is unavailable or too large.")
    return workspace, relative, files


def read_model_source(root: Path, model: str) -> dict[str, Any]:
    workspace, relative, files = _source(root, model)
    selected = workspace.model(model)
    assert selected is not None
    return {
        "model": model,
        "path": relative,
        "project_path": (workspace.root / relative).relative_to(root.resolve()).as_posix(),
        "sql": files[relative].decode("utf-8"),
        "source_digest": _digest(files[relative]),
        "workspace_digest": _workspace_digest(files),
        "model_version": selected.version,
    }


def _namespace(root: Path) -> str:
    return "transform.editor." + _digest(str(root.resolve()).encode())


def _records(
    state: dict[str, Any] | None, *, require_known_state: bool = False
) -> list[dict[str, Any]]:
    if state is None:
        return []
    if require_known_state and set(state) - {"schema_version", "items"}:
        raise TransformEditorError(
            "Unknown mutation or reference metadata prevents reclamation.", status=409
        )
    if state.get("schema_version") != 1 or not isinstance(state.get("items"), list):
        raise TransformEditorError("Stored transform draft metadata is unavailable.", status=503)
    return state["items"]


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise TransformEditorError("Invalid transform draft identity.") from exc


def _artifact(root: Path, draft_id: str, artifact_id: str) -> Path:
    return _contained(
        root,
        root / ".phlo" / "transform-editor" / _uuid(draft_id) / _uuid(artifact_id) / "workspace",
    )


def save_model_draft(
    root: Path,
    model: str,
    *,
    actor: str,
    env: str,
    ref: str,
    sql: str,
    source_digest: str,
    workspace_digest: str,
    draft_id: str | None = None,
    expected_revision: int = 0,
) -> dict[str, Any]:
    if len(sql.encode("utf-8")) > _MAX_SOURCE_BYTES or not sql.strip():
        raise TransformEditorError("Provide non-empty SQL within the 256 KiB editor limit.")
    workspace, relative, files = _source(root, model)
    if _digest(files[relative]) != source_digest or _workspace_digest(files) != workspace_digest:
        raise TransformEditorError(
            "The source workspace changed. Reload before saving a draft.",
            reason="conflict",
            status=409,
        )
    if (draft_id is None) != (expected_revision == 0):
        raise TransformEditorError(
            "New drafts require revision zero; updates require a draft identity."
        )
    identity = _uuid(draft_id) if draft_id else str(uuid4())
    artifact_id = str(uuid4())
    destination = _artifact(root, identity, artifact_id)
    files[relative] = sql.encode("utf-8")
    record = {
        "draft_id": identity,
        "artifact_id": artifact_id,
        "revision": expected_revision + 1,
        "model": model,
        "actor": actor,
        "env": env,
        "ref": ref,
        "path": relative,
        "project_path": (workspace.root / relative).relative_to(root.resolve()).as_posix(),
        "source_digest": source_digest,
        "workspace_digest": workspace_digest,
        "revision_digest": _workspace_digest(files),
        "sql_digest": _digest(files[relative]),
        "created_at": datetime.now(UTC).isoformat(),
        "archived_at": None,
        "archived_by": None,
        "archive_reason": None,
    }
    try:
        for name, content in files.items():
            path = _contained(destination, destination / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        def change(current: dict[str, Any] | None) -> dict[str, Any]:
            records = _records(current)
            history = [item for item in records if item.get("draft_id") == identity]
            if history and any(
                item.get(key) != record[key]
                for item in history
                for key in ("actor", "env", "ref", "model")
            ):
                raise TransformEditorError(
                    "Draft revision was not found in this scope.", status=404
                )
            if max((item.get("revision", 0) for item in history), default=0) != expected_revision:
                raise TransformEditorError(
                    "The draft changed. Reload its latest revision before saving.",
                    reason="conflict",
                    status=409,
                )
            if history and any(
                item.get("archived_at") for item in history if item["revision"] == expected_revision
            ):
                raise TransformEditorError(
                    "Restore the latest revision before editing this draft.", status=409
                )
            if sum(not item.get("reclaimed_at") for item in records) >= _MAX_RETAINED_REVISIONS:
                raise TransformEditorError("The transform draft revision limit was reached.")
            return {"schema_version": 1, "items": [*records, record]}

        get_settings_service().mutate(SettingsScope.GLOBAL, _namespace(root), change)
    except Exception:
        shutil.rmtree(destination.parent, ignore_errors=True)
        raise
    return {**record, "sql": sql}


def read_model_draft(
    root: Path, model: str, *, draft_id: str, revision: int, actor: str, env: str, ref: str
) -> dict[str, Any]:
    state = get_settings_service().get(SettingsScope.GLOBAL, _namespace(root))
    found = next(
        (
            item
            for item in _records(state.settings if state else None)
            if item.get("draft_id") == _uuid(draft_id)
            and item.get("revision") == revision
            and item.get("actor") == actor
            and item.get("env") == env
            and item.get("ref") == ref
            and item.get("model") == model
        ),
        None,
    )
    if found is None:
        raise TransformEditorError("Draft revision was not found in this scope.", status=404)
    if found.get("reclaim_started_at"):
        sql = found["retained_sql"]
        if _digest(sql.encode()) != found["sql_digest"]:
            raise TransformEditorError("Retained source checksum changed.", status=409)
        return {**found, "sql": sql}
    directory = _artifact(root, found["draft_id"], found["artifact_id"])
    files = _inputs(directory)
    if (
        _workspace_digest(files) != found["revision_digest"]
        or found["path"] not in files
        or _digest(files[found["path"]]) != found["sql_digest"]
    ):
        raise TransformEditorError(
            "Draft revision integrity verification failed.", reason="tampered", status=409
        )
    return {**found, "sql": files[found["path"]].decode("utf-8")}


def archive_model_draft(
    root: Path, model: str, *, archived: bool, reason: str, **identity: Any
) -> dict[str, Any]:
    """Preserve immutable source and its identity while changing retention visibility."""
    saved = read_model_draft(root, model, **identity)
    if saved.get("reclaim_started_at"):
        raise TransformEditorError(
            "Reclamation is irreversible; revision cannot be restored.", status=409
        )
    changes = {
        "archived_at": datetime.now(UTC).isoformat() if archived else None,
        "archived_by": identity["actor"] if archived else None,
        "archive_reason": reason if archived else None,
    }

    def update(current):
        records = _records(current)
        record = next(
            item
            for item in records
            if item["draft_id"] == saved["draft_id"] and item["revision"] == saved["revision"]
        )
        if any(saved[key] != value for key, value in record.items()):
            raise TransformEditorError("Revision changed during archival.", status=409)
        return {
            "schema_version": 1,
            "items": [{**item, **changes} if item is record else item for item in records],
        }

    get_settings_service().mutate(SettingsScope.GLOBAL, _namespace(root), update)
    return {**saved, **changes}


def _reclaim_workspace_bytes(root: Path, directory: Path) -> int:
    """Measure only the literal retained workspace, never a symlink destination."""
    if directory.absolute() != directory.resolve() or not directory.is_relative_to(
        root.resolve() / ".phlo" / "transform-editor"
    ):
        raise TransformEditorError(
            "Reclamation requires a contained non-symlink workspace.", status=409
        )
    return sum(
        path.stat().st_size
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _reclaim_workspace(root: Path, directory: Path) -> int:
    """Remove only the literal retained workspace, never a symlink destination."""
    size = _reclaim_workspace_bytes(root, directory)
    if directory.exists():
        shutil.rmtree(directory)
    return size


def reclaim_model_draft(
    root: Path,
    model: str,
    *,
    expected_revision_digest: str,
    reason: str,
    expected_record: dict[str, Any] | None = None,
    **identity: Any,
) -> dict[str, Any]:
    """Reclaim an archived snapshot but retain SQL, file digests and all revision identities."""
    from phlo_transform.editor_execution import _EXECUTION_FIELDS, _storage

    saved = read_model_draft(root, model, **identity)
    if expected_record is not None and saved != expected_record:
        raise TransformEditorError("Revision changed after retention validation.", status=409)
    known = {
        "draft_id",
        "artifact_id",
        "revision",
        "model",
        "actor",
        "env",
        "ref",
        "path",
        "project_path",
        "source_digest",
        "workspace_digest",
        "revision_digest",
        "sql_digest",
        "created_at",
        "sql",
        "archived_at",
        "archived_by",
        "archive_reason",
        "reclaim_started_at",
        "reclaimed_at",
        "reclaimed_by",
        "reclaim_reason",
        "reclaimed_bytes",
        "reclaim_payload_bytes",
        "retained_sql",
        "retained_file_digests",
    }
    if set(saved) - known:
        raise TransformEditorError("Unknown revision evidence prevents reclamation.", status=409)
    if not saved.get("archived_at") or saved["revision_digest"] != expected_revision_digest:
        raise TransformEditorError(
            "An archived revision and exact digest are required.", status=409
        )
    if saved.get("reclaimed_at"):
        return saved
    state = get_settings_service().get(SettingsScope.GLOBAL, _storage(root))
    if any(
        set(item) - _EXECUTION_FIELDS
        or (
            item.get("draft_id") == saved["draft_id"]
            and item.get("revision") == saved["revision"]
            and item.get("status") not in {"passed", "failed"}
        )
        for item in _records(state.settings if state else None, require_known_state=True)
    ):
        raise TransformEditorError(
            "Unresolved executions must retain their source snapshot.", status=409
        )
    directory = (
        root.resolve()
        / ".phlo/transform-editor"
        / saved["draft_id"]
        / saved["artifact_id"]
        / "workspace"
    )
    started = {key: value for key, value in saved.items() if key != "sql"}
    if not saved.get("reclaim_started_at"):
        started.update(
            reclaim_started_at=datetime.now(UTC).isoformat(),
            reclaimed_by=identity["actor"],
            reclaim_reason=reason,
            reclaim_payload_bytes=_reclaim_workspace_bytes(root, directory),
            retained_sql=saved["sql"],
            retained_file_digests={
                name: _digest(data) for name, data in _inputs(directory).items()
            },
        )
    size = started.get("reclaim_payload_bytes")
    if type(size) is not int or size < 0:
        raise TransformEditorError("Original reclamation byte evidence is unavailable.", status=409)

    def store(record):
        def update(current):
            records = _records(current, require_known_state=True)
            if any(set(item) - known for item in records):
                raise TransformEditorError(
                    "Unknown revision references prevent reclamation.", status=409
                )
            existing = next(item for item in records if item["artifact_id"] == saved["artifact_id"])
            expected = {key: value for key, value in saved.items() if key != "sql"}
            if existing != expected and existing != started:
                raise TransformEditorError("Revision changed during reclamation.", status=409)
            return {
                "schema_version": 1,
                "items": [record if item is existing else item for item in records],
            }

        get_settings_service().mutate(SettingsScope.GLOBAL, _namespace(root), update)

    store(started)
    _reclaim_workspace(root, directory)
    finished = {**started, "reclaimed_at": datetime.now(UTC).isoformat(), "reclaimed_bytes": size}
    store(finished)
    return {**finished, "sql": saved["sql"]}


def preview_settings(
    root: Path,
    *,
    ref: str,
    required_ref: str | None = None,
    required_catalog: str | None = None,
    required_endpoint: str | None = None,
    required_user: str | None = None,
) -> TransformSettings:
    """Validate local native prerequisites against the caller's attested preview binding."""
    if not provider_is_active(root):
        raise TransformEditorError("Phlo Transform is not the active provider.", status=503)
    if required_ref is not None and required_ref != ref:
        raise TransformEditorError(
            "The preview binding does not match the draft reference.", status=503
        )
    if ref != "main" and not all(
        (required_ref, required_catalog, required_endpoint, required_user)
    ):
        raise TransformEditorError(
            "A complete preview binding is required for this reference.", status=503
        )
    settings = get_settings(root)
    require_binary(root, settings)
    if settings.adapter != "trino" or not settings.trino_endpoint or not settings.trino_catalog:
        raise TransformEditorError(
            "Read-only planning requires an explicitly configured Trino endpoint and catalog.",
            status=503,
        )
    if required_catalog is not None and settings.trino_catalog != required_catalog:
        raise TransformEditorError(
            "The engine catalog does not match the selected environment.", status=503
        )
    if required_endpoint is not None and (
        settings.trino_endpoint.rstrip("/") != required_endpoint.rstrip("/")
        or settings.trino_user != required_user
    ):
        raise TransformEditorError(
            "The engine connection does not match the environment's read-only preview identity.",
            status=503,
        )
    return settings


def compile_model_draft(
    root: Path,
    model: str,
    *,
    allowed_sources: set[str],
    required_ref: str | None = None,
    required_catalog: str | None = None,
    required_endpoint: str | None = None,
    required_user: str | None = None,
    trusted_ca_file: str | None = None,
    **identity: Any,
) -> dict[str, Any]:
    record = read_model_draft(root, model, **identity)
    if record.get("archived_at"):
        raise TransformEditorError("Restore the archived revision before compiling it.", status=409)
    directory = _artifact(root, record["draft_id"], record["artifact_id"])
    settings = preview_settings(
        root,
        ref=record["ref"],
        required_ref=required_ref,
        required_catalog=required_catalog,
        required_endpoint=required_endpoint,
        required_user=required_user,
    )
    binary = require_binary(root, settings)
    check, checked = run_json(binary, ["check"], settings=settings, root=directory)
    if check is None:
        raise TransformEditorError(
            "The engine did not return a valid compilation report.", status=502
        )
    result = {
        "draft_id": record["draft_id"],
        "revision": record["revision"],
        "revision_digest": record["revision_digest"],
        "ref": record["ref"],
        "status": "failed",
        "diagnostics": check.get("diagnostics") or [],
        "plan_id": None,
        "compiled_sql": None,
        "target": None,
        "sql_hash": None,
        "desired_version": None,
        "materialization_available": False,
    }
    if not checked.ok:
        return result
    listed, listed_invocation = run_json(binary, ["list"], settings=settings, root=directory)
    if listed is None or not listed_invocation.ok:
        raise TransformEditorError("The engine did not report the draft source graph.", status=502)
    for source in listed.get("sources") or []:
        name = str(source.get("name") or "")
        parts = name.split(".")
        if len(parts) == 3 and parts[0] == settings.trino_catalog:
            name = ".".join(parts[1:])
        if name not in allowed_sources:
            raise TransformEditorError(
                "Every draft source must be an authorized asset in the configured environment catalog.",
                reason="forbidden",
                status=403,
            )
    plan, planned = run_json(
        binary,
        [
            "plan",
            "--catalogue",
            "--select",
            model,
            *adapter_args(None, settings.model_copy(update={"trino_password": None}), infer=False),
        ],
        settings=settings,
        root=directory,
        environment={
            "PHLO_TRINO_PASSWORD": settings.trino_password or "",
            **({"SSL_CERT_FILE": trusted_ca_file} if trusted_ca_file else {}),
        },
    )
    if plan is None:
        raise TransformEditorError("The engine did not return a valid read-only plan.", status=502)
    selected = next(
        (
            item
            for item in plan.get("models") or []
            if item.get("id") in {model, "model://" + model.replace(".", "/")}
        ),
        None,
    )
    result.update(
        {
            "status": "ready"
            if planned.ok and not plan.get("blocked", True) and selected
            else "failed",
            "diagnostics": [*result["diagnostics"], *(plan.get("diagnostics") or [])],
            "plan_id": plan.get("id"),
            "compiled_sql": selected.get("compiled_sql") if selected else None,
            "target": selected.get("target") if selected else None,
            "sql_hash": selected.get("sql_hash") if selected else None,
            "desired_version": selected.get("desired_version") if selected else None,
        }
    )
    return result


def list_model_drafts(
    root: Path, model: str, *, actor: str, env: str, ref: str, include_archived: bool = False
) -> dict[str, Any]:
    """Return bounded, verified revision metadata from this exact principal scope."""
    state = get_settings_service().get(SettingsScope.GLOBAL, _namespace(root))
    records = [
        item
        for item in _records(state.settings if state else None)
        if item.get("model") == model
        and item.get("actor") == actor
        and item.get("env") == env
        and item.get("ref") == ref
        and (include_archived or not item.get("archived_at"))
    ]
    records.sort(key=lambda item: (item["created_at"], item["revision"]), reverse=True)
    summaries = []
    keys = (
        "draft_id",
        "revision",
        "model",
        "env",
        "ref",
        "path",
        "created_at",
        "source_digest",
        "revision_digest",
        "sql_digest",
        "archived_at",
        "archived_by",
        "archive_reason",
        "reclaim_started_at",
        "reclaimed_at",
        "reclaimed_by",
        "reclaim_reason",
        "reclaimed_bytes",
    )
    for record in records[:100]:
        verified = read_model_draft(
            root,
            model,
            actor=actor,
            env=env,
            ref=ref,
            draft_id=record["draft_id"],
            revision=record["revision"],
        )
        summaries.append(
            {key: verified.get(key, 0 if key == "reclaimed_bytes" else None) for key in keys}
        )
    return {"items": summaries, "total": len(records), "truncated": len(records) > 100}


def compare_model_draft(root: Path, model: str, **identity: Any) -> dict[str, Any]:
    """Compare an immutable SQL revision with actual current workspace source."""
    from difflib import SequenceMatcher, unified_diff

    saved = read_model_draft(root, model, **identity)
    source = read_model_source(root, model)
    before = source["sql"].split("\n")
    after = saved["sql"].split("\n")
    added = removed = 0
    for operation, start_before, end_before, start_after, end_after in SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if operation in {"replace", "delete"}:
            removed += end_before - start_before
        if operation in {"replace", "insert"}:
            added += end_after - start_after
    return {
        "draft_id": saved["draft_id"],
        "revision": saved["revision"],
        "revision_digest": saved["revision_digest"],
        "source_digest": source["source_digest"],
        "source_path": source["path"],
        "baseline_matches_saved_source": source["source_digest"] == saved["source_digest"],
        "added_lines": added,
        "removed_lines": removed,
        "unified_diff": list(
            unified_diff(
                before,
                after,
                fromfile="Current workspace source",
                tofile=f"Saved SQL revision {saved['revision']}",
                lineterm="",
            )
        ),
    }
