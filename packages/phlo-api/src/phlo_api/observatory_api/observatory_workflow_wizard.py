"""Provider-neutral Observatory workflow wizard helpers.

Builds stage-by-stage workflow proposals from user selections and applies
them within the project workflow root. Proposals are generated and stored
server-side, HMAC-signed with a per-project integrity key so the browser can
only apply actions this process previously issued; state I/O opens paths
relative to O_NOFOLLOW directory descriptors and lands via temp-file plus
rename, never exposing partially written records.
"""

from __future__ import annotations

import contextlib
import importlib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException
from pydantic import BaseModel, Field

from phlo.capabilities import (
    WorkflowApplyAction,
    WorkflowFilePreview,
    WorkflowProposal,
    WorkflowProposalRequest,
    WorkflowStageSelection,
    detect_file_conflicts,
    validate_proposal_request,
)

_fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - POSIX is used in production
    pass

# DESIGN
# ------
#
# Proposals are generated and stored server-side and HMAC-signed with a
# per-project integrity key, so the browser can only apply actions for a
# proposal this process previously issued. All state I/O opens paths relative
# to directory descriptors opened with O_NOFOLLOW, and files land via
# temp-file + rename, so a hostile project tree cannot redirect writes or
# observe partially written records.


STAGES = ["source", "transform", "quality", "publish"]
_ALLOWED_PROJECT_ROOTS = ("workflows", "tests")
_WORKFLOW_ALLOWED_EXTENSIONS = {".json", ".py", ".sql", ".toml", ".yaml", ".yml"}
_WORKFLOW_FILE_MODE = 0o644
_PROPOSAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_STATE_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
WORKFLOW_WIZARD_PLUGIN_TYPES = (
    "ingestion_provider",
    "transformation_provider",
    "quality_provider",
    "orchestrator",
    "resource_provider",
    "service",
)
WORKFLOW_WIZARD_FALLBACK_MODULES = (
    "phlo_dlt.plugin",
    "phlo_sling.plugin",
    "phlo_dbt.plugin",
    "phlo_pandera.plugin",
    "phlo_openmetadata.plugin",
    "phlo_dagster.plugin",
)


class ObservatoryWorkflowWizardSelection(BaseModel):
    """User-selected contribution and values for one workflow stage."""

    contribution_id: str
    values: dict[str, Any] = Field(default_factory=dict)


class ObservatoryWorkflowGraphNode(BaseModel):
    """Canvas node selected by the workflow builder."""

    id: str
    contribution_id: str
    stage: str
    values: dict[str, Any] = Field(default_factory=dict)


class ObservatoryWorkflowGraphEdge(BaseModel):
    """Canvas edge connecting workflow builder nodes."""

    id: str
    source: str
    target: str


class ObservatoryWorkflowGraph(BaseModel):
    """Workflow graph authored by Observatory."""

    nodes: list[ObservatoryWorkflowGraphNode] = Field(default_factory=list)
    edges: list[ObservatoryWorkflowGraphEdge] = Field(default_factory=list)


class ObservatoryWorkflowProposalRequest(BaseModel):
    """Request body for workflow wizard proposal generation."""

    workflow_name: str
    domain: str
    graph: ObservatoryWorkflowGraph


class ObservatoryWorkflowActionRequest(BaseModel):
    """Request body for guarded workflow wizard apply actions."""

    action_id: str
    proposal_id: str


class _StoredWorkflowProposal(BaseModel):
    """Server-owned workflow proposal record."""

    proposal_id: str
    issuer_subject: str
    proposal: dict[str, Any]
    digest: str
    signature: str


class ObservatoryWorkflowActionResult(BaseModel):
    """Result for a guarded workflow wizard action."""

    action_id: str
    status: Literal["succeeded", "failed", "skipped"]
    message: str
    files: list[str] = Field(default_factory=list)


def list_workflow_wizard_contributions() -> list[dict[str, Any]]:
    """Return package-provided workflow wizard contributions."""

    from phlo.plugins.discovery import discover_plugins, get_global_registry

    contributions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    registry = get_global_registry()
    for plugin_type in WORKFLOW_WIZARD_PLUGIN_TYPES:
        try:
            discover_plugins(plugin_type=plugin_type, auto_register=True)
        except Exception:
            continue
        for plugin_name in registry.list(plugin_type):
            plugin = registry.get(plugin_type, plugin_name)
            if plugin is None:
                continue
            loader = getattr(plugin, "get_workflow_wizard_contributions", None)
            if not callable(loader):
                try:
                    module = importlib.import_module(plugin.__class__.__module__)
                except Exception:
                    module = None
                loader = getattr(module, "get_workflow_wizard_contributions", None)
            if callable(loader):
                try:
                    for item in loader():
                        contribution = item.to_browser_dict()
                        contribution_id = str(contribution.get("id") or "")
                        if contribution_id in seen_ids:
                            continue
                        seen_ids.add(contribution_id)
                        contributions.append(contribution)
                except Exception:
                    continue
    for module_name in WORKFLOW_WIZARD_FALLBACK_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        loader = getattr(module, "get_workflow_wizard_contributions", None)
        if not callable(loader):
            continue
        try:
            for item in loader():
                contribution = item.to_browser_dict()
                contribution_id = str(contribution.get("id") or "")
                if contribution_id in seen_ids:
                    continue
                seen_ids.add(contribution_id)
                contributions.append(contribution)
        except Exception:
            continue
    return contributions


def build_workflow_wizard_payload() -> dict[str, Any]:
    """Build the workflow wizard discovery payload."""

    return {"version": 1, "stages": STAGES, "contributions": list_workflow_wizard_contributions()}


def build_workflow_proposal(
    project_root: Path,
    request: ObservatoryWorkflowProposalRequest,
    issuer_subject: str,
) -> dict[str, Any]:
    """Build and persist a server-owned workflow proposal for the browser."""

    if not request.graph.nodes:
        raise HTTPException(status_code=422, detail={"graph": ["Add at least one workflow node."]})

    selections: dict[str, dict[str, Any] | list[dict[str, Any] | WorkflowStageSelection]] = {
        stage: (
            [dict(item) for item in payload]
            if isinstance(payload := _selection_payload(selection), list)
            else dict(payload)
        )
        for stage, selection in _selections_from_graph(request.graph).items()
    }
    contract_request = WorkflowProposalRequest(
        workflow_name=request.workflow_name,
        domain=request.domain,
        selections=cast(
            dict[
                str,
                dict[str, Any]
                | WorkflowStageSelection
                | list[dict[str, Any] | WorkflowStageSelection],
            ],
            selections,
        ),
    )
    errors = validate_proposal_request(contract_request)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    proposal = _proposal_from_request(contract_request)
    conflicts = detect_file_conflicts(project_root, proposal)
    if conflicts:
        proposal = _with_conflict_action_disabled(proposal, conflicts)
    return _issue_workflow_proposal(project_root, proposal, issuer_subject)


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, _STATE_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, _STATE_DIRECTORY_FLAGS, dir_fd=parent_fd)


def _open_workflow_state_fd(project_root: Path) -> int:
    root_fd = -1
    phlo_fd = -1
    try:
        root_fd = os.open(project_root.resolve(), _STATE_DIRECTORY_FLAGS)
        phlo_fd = _open_directory_at(root_fd, ".phlo", create=True)
        return _open_directory_at(phlo_fd, "workflow-wizard", create=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503, detail="Workflow state directory is not safe."
        ) from exc
    finally:
        if phlo_fd >= 0:
            os.close(phlo_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _open_state_storage_fd(project_root: Path, name: str, *, create: bool) -> int:
    state_fd = -1
    try:
        state_fd = _open_workflow_state_fd(project_root)
        return _open_directory_at(state_fd, name, create=create)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise HTTPException(
            status_code=503, detail="Workflow state directory is not safe."
        ) from exc
    finally:
        if state_fd >= 0:
            os.close(state_fd)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _proposal_digest(proposal: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(proposal)).hexdigest()


def _workflow_integrity_key(project_root: Path) -> bytes:
    configured = os.environ.get("PHLO_WORKFLOW_WIZARD_SECRET")
    if configured:
        return configured.encode("utf-8")

    state_fd = _open_workflow_state_fd(project_root)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                "integrity.key",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=state_fd,
            )
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    handle.write(secrets.token_bytes(32))
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink("integrity.key", dir_fd=state_fd)
                raise

        try:
            descriptor = os.open(
                "integrity.key",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=state_fd,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail="Workflow integrity key cannot be read safely."
            ) from exc
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HTTPException(
                status_code=503, detail="Workflow integrity key is not a regular file."
            )
        os.fchmod(descriptor, 0o600)
        key = os.read(descriptor, 4096)
        if not key:
            raise HTTPException(status_code=503, detail="Workflow integrity key is empty.")
        return key
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(state_fd)


def _proposal_signature(
    project_root: Path, proposal_id: str, issuer_subject: str, digest: str
) -> str:
    message = f"{proposal_id}:{issuer_subject}:{digest}".encode("utf-8")
    return hmac.new(_workflow_integrity_key(project_root), message, hashlib.sha256).hexdigest()


def _read_state_json(project_root: Path, storage_name: str, filename: str) -> dict[str, Any]:
    storage_fd = _open_state_storage_fd(project_root, storage_name, create=False)
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=storage_fd,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("state record is not a regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise TypeError("state record is not an object")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(storage_fd)


def _write_state_json(
    project_root: Path, storage_name: str, filename: str, payload: dict[str, Any]
) -> None:
    storage_fd = _open_state_storage_fd(project_root, storage_name, create=True)
    temporary_name = f".{filename}.{secrets.token_hex(8)}.tmp"
    temporary_fd = -1
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=storage_fd,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            temporary_fd = -1
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=storage_fd,
            dst_dir_fd=storage_fd,
        )
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=storage_fd)
        os.close(storage_fd)


def _issue_workflow_proposal(
    project_root: Path, proposal: WorkflowProposal, issuer_subject: str
) -> dict[str, Any]:
    proposal_id = secrets.token_urlsafe(24)
    proposal_payload = proposal.to_browser_dict()
    digest = _proposal_digest(proposal_payload)
    signature = _proposal_signature(project_root, proposal_id, issuer_subject, digest)
    record = _StoredWorkflowProposal(
        proposal_id=proposal_id,
        issuer_subject=issuer_subject,
        proposal=proposal_payload,
        digest=digest,
        signature=signature,
    )
    _write_state_json(
        project_root,
        "proposals",
        f"{proposal_id}.json",
        record.model_dump(mode="json"),
    )
    return {
        **proposal_payload,
        "proposal_id": proposal_id,
    }


def _selections_from_graph(
    graph: ObservatoryWorkflowGraph,
) -> dict[str, ObservatoryWorkflowWizardSelection | list[ObservatoryWorkflowWizardSelection]]:
    grouped: dict[str, list[ObservatoryWorkflowWizardSelection]] = {}
    for node in _topological_nodes(graph):
        grouped.setdefault(node.stage, []).append(
            ObservatoryWorkflowWizardSelection(
                contribution_id=node.contribution_id,
                values=node.values,
            )
        )

    selections: dict[
        str, ObservatoryWorkflowWizardSelection | list[ObservatoryWorkflowWizardSelection]
    ] = {}
    for stage, stage_nodes in grouped.items():
        selections[stage] = stage_nodes[0] if stage == "source" and stage_nodes else stage_nodes
    return selections


def _topological_nodes(graph: ObservatoryWorkflowGraph) -> list[ObservatoryWorkflowGraphNode]:
    by_id = {node.id: node for node in graph.nodes}
    indegree = {node.id: 0 for node in graph.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in by_id or edge.target not in by_id:
            continue
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1

    ready = [node.id for node in graph.nodes if indegree[node.id] == 0]
    ordered: list[ObservatoryWorkflowGraphNode] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)

    return ordered if len(ordered) == len(graph.nodes) else graph.nodes


def _selection_payload(
    selection: ObservatoryWorkflowWizardSelection | list[ObservatoryWorkflowWizardSelection],
) -> dict[str, Any] | list[dict[str, Any]]:
    if isinstance(selection, list):
        return [cast(dict[str, Any], _selection_payload(item)) for item in selection]
    return {
        "contribution_id": selection.contribution_id,
        "values": selection.values,
    }


def apply_workflow_action(
    project_root: Path, request: ObservatoryWorkflowActionRequest, issuer_subject: str
) -> ObservatoryWorkflowActionResult:
    """Apply a server-issued workflow action within the project workflow root."""

    proposal_id = request.proposal_id
    proposal, proposal_digest = _load_verified_workflow_proposal(
        project_root, proposal_id, issuer_subject
    )
    action = next((item for item in proposal.actions if item.id == request.action_id), None)
    if action is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow action not found: {request.action_id}"
        )
    if not action.enabled:
        raise HTTPException(status_code=409, detail=action.reason or "Workflow action is disabled.")

    with _workflow_apply_lock(project_root):
        targets = _validated_workflow_targets(project_root, proposal)
        applied = _load_applied_record(project_root, proposal_id)
        if applied is not None:
            if (
                applied.get("action_id") != action.id
                or applied.get("proposal_digest") != proposal_digest
            ):
                raise HTTPException(status_code=409, detail="Workflow proposal replay conflicts.")
            if applied.get("status") == "succeeded":
                for preview, _ in targets:
                    _apply_workflow_file(
                        project_root,
                        preview,
                        conflict_policy="fail-on-conflict",
                        verify_only=True,
                    )
                try:
                    return ObservatoryWorkflowActionResult.model_validate(applied["result"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="Workflow proposal application record is invalid.",
                    ) from exc
            if applied.get("status") != "applying":
                raise HTTPException(
                    status_code=409,
                    detail="Workflow proposal application record is invalid.",
                )
        else:
            conflicts = [
                preview.path
                for preview, _ in targets
                if preview.mode == "create"
                and _workflow_file_state(project_root, preview) != "missing"
            ]
            if conflicts and action.conflict_policy == "fail-on-conflict":
                raise HTTPException(
                    status_code=409, detail=f"File conflicts: {', '.join(conflicts)}"
                )
            applied = {
                "action_id": action.id,
                "proposal_digest": proposal_digest,
                "proposal_id": proposal_id,
                "status": "applying",
                "written_files": [],
            }
            _write_state_json(project_root, "applied", f"{proposal_id}.json", applied)

        # Every completed file is journaled to the applied record as it lands.
        # A crash mid-apply leaves status "applying"; a retry resumes from the
        # journal, re-applying only what is still missing.
        written_files = list(applied.get("written_files") or [])
        for preview, _ in targets:
            outcome = _apply_workflow_file(
                project_root,
                preview,
                conflict_policy=cast(
                    Literal["fail-on-conflict", "skip-if-exists"], action.conflict_policy
                ),
            )
            if outcome != "skipped" and preview.path not in written_files:
                written_files.append(preview.path)
            applied["written_files"] = written_files
            _write_state_json(project_root, "applied", f"{proposal_id}.json", applied)

        result = ObservatoryWorkflowActionResult(
            action_id=action.id,
            status="succeeded",
            message=f"Created {len(written_files)} workflow file{'' if len(written_files) == 1 else 's'}.",
            files=written_files,
        )
        _write_state_json(
            project_root,
            "applied",
            f"{proposal_id}.json",
            {
                **applied,
                "status": "succeeded",
                "result": result.model_dump(mode="json"),
            },
        )
        return result


def _load_verified_workflow_proposal(
    project_root: Path, proposal_id: str, issuer_subject: str
) -> tuple[WorkflowProposal, str]:
    if not _PROPOSAL_ID_PATTERN.fullmatch(proposal_id):
        raise HTTPException(status_code=400, detail="Invalid workflow proposal id.")
    try:
        record = _StoredWorkflowProposal.model_validate(
            _read_state_json(project_root, "proposals", f"{proposal_id}.json")
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Workflow proposal not found.") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Workflow proposal is invalid.") from exc

    if record.proposal_id != proposal_id:
        raise HTTPException(
            status_code=409, detail="Workflow proposal integrity verification failed."
        )
    expected_signature = _proposal_signature(
        project_root, record.proposal_id, record.issuer_subject, record.digest
    )
    if not hmac.compare_digest(record.signature, expected_signature):
        raise HTTPException(
            status_code=409, detail="Workflow proposal integrity verification failed."
        )
    if not hmac.compare_digest(record.issuer_subject, issuer_subject):
        raise HTTPException(status_code=404, detail="Workflow proposal not found.")
    if not hmac.compare_digest(record.digest, _proposal_digest(record.proposal)):
        raise HTTPException(
            status_code=409, detail="Workflow proposal integrity verification failed."
        )
    return _proposal_from_payload(record.proposal), record.digest


def _load_applied_record(project_root: Path, proposal_id: str) -> dict[str, Any] | None:
    try:
        record = _read_state_json(project_root, "applied", f"{proposal_id}.json")
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Workflow proposal application record is invalid.",
        ) from exc
    return record


@contextmanager
def _workflow_apply_lock(project_root: Path):
    state_fd = _open_workflow_state_fd(project_root)
    descriptor = -1
    try:
        descriptor = os.open(
            "apply.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=state_fd,
        )
    except OSError as exc:
        os.close(state_fd)
        raise HTTPException(status_code=503, detail="Workflow apply lock is not safe.") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HTTPException(status_code=503, detail="Workflow apply lock is not safe.")
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        descriptor = -1
        with handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(state_fd)


def _open_workflow_parent_fd(project_root: Path, relative_path: str) -> tuple[int, str]:
    parts = Path(relative_path).parts
    if (
        not parts
        or Path(relative_path).is_absolute()
        or Path(relative_path).anchor
        or ".." in parts
    ):
        raise HTTPException(status_code=400, detail="Invalid workflow file path.")
    # Walk the path one component at a time relative to directory descriptors:
    # a symlink swapped in at any depth fails the O_NOFOLLOW open instead of
    # being followed outside the project root.
    directory_fd = -1
    try:
        directory_fd = os.open(project_root.resolve(), _STATE_DIRECTORY_FLAGS)
        for component in parts[:-1]:
            try:
                next_fd = os.open(
                    component,
                    _STATE_DIRECTORY_FLAGS,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, _STATE_DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                raise HTTPException(
                    status_code=409, detail=f"Unsafe workflow file path: {relative_path}"
                ) from exc
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd, parts[-1]
    except HTTPException:
        if directory_fd >= 0:
            os.close(directory_fd)
        raise
    except OSError as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        raise HTTPException(
            status_code=409, detail=f"Unsafe workflow file path: {relative_path}"
        ) from exc


def _workflow_file_state(
    project_root: Path, preview: WorkflowFilePreview
) -> Literal["missing", "matching", "conflict"]:
    directory_fd, filename = _open_workflow_parent_fd(project_root, preview.path)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "conflict"
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return "conflict"
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return "matching" if handle.read() == preview.content else "conflict"
    except (OSError, UnicodeError):
        return "conflict"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _write_text_atomically_at(directory_fd: int, filename: str, content: str) -> None:
    temporary_name = f".{filename}.{secrets.token_hex(8)}.tmp"
    temporary_fd = -1
    try:
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            temporary_fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), _WORKFLOW_FILE_MODE)
            os.fsync(handle.fileno())
        os.link(
            temporary_name,
            filename,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_fd)
        raise
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)


def _apply_workflow_file(
    project_root: Path,
    preview: WorkflowFilePreview,
    *,
    conflict_policy: Literal["fail-on-conflict", "skip-if-exists"],
    verify_only: bool = False,
) -> Literal["written", "matching", "skipped"]:
    directory_fd, filename = _open_workflow_parent_fd(project_root, preview.path)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            if verify_only:
                raise HTTPException(
                    status_code=409, detail="Applied workflow files changed after completion."
                )
            try:
                _write_text_atomically_at(directory_fd, filename, preview.content)
            except FileExistsError as exc:
                if conflict_policy == "skip-if-exists":
                    return "skipped"
                raise HTTPException(
                    status_code=409, detail=f"File conflicts: {preview.path}"
                ) from exc
            return "written"
        except OSError as exc:
            if verify_only:
                raise HTTPException(
                    status_code=409, detail="Applied workflow files changed after completion."
                ) from exc
            if conflict_policy == "skip-if-exists":
                return "skipped"
            raise HTTPException(status_code=409, detail=f"File conflicts: {preview.path}") from exc

        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            state = "conflict"
        else:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                state = "matching" if handle.read() == preview.content else "conflict"
        if state == "matching":
            return "matching"
        if verify_only:
            raise HTTPException(
                status_code=409, detail="Applied workflow files changed after completion."
            )
        if conflict_policy == "skip-if-exists":
            return "skipped"
        raise HTTPException(status_code=409, detail=f"File conflicts: {preview.path}")
    except UnicodeError as exc:
        if verify_only:
            raise HTTPException(
                status_code=409, detail="Applied workflow files changed after completion."
            ) from exc
        if conflict_policy == "skip-if-exists":
            return "skipped"
        raise HTTPException(status_code=409, detail=f"File conflicts: {preview.path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _validated_workflow_targets(
    project_root: Path, proposal: WorkflowProposal
) -> list[tuple[WorkflowFilePreview, Path]]:
    project_root = project_root.resolve()
    allowed_roots = []
    for root_name in _ALLOWED_PROJECT_ROOTS:
        root = project_root / root_name
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise HTTPException(
                status_code=400, detail="Workflow root is not a safe project directory."
            )
        allowed_roots.append(root.resolve(strict=False))

    targets: list[tuple[WorkflowFilePreview, Path]] = []
    for preview in proposal.files:
        try:
            relative_path = Path(preview.path)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid workflow file path.") from exc
        if (
            relative_path.is_absolute()
            or relative_path.anchor
            or ".." in relative_path.parts
            or relative_path.suffix.lower() not in _WORKFLOW_ALLOWED_EXTENSIONS
        ):
            raise HTTPException(
                status_code=400, detail=f"Unsafe workflow file path: {preview.path}"
            )

        target = (project_root / relative_path).resolve(strict=False)
        if not any(_is_contained(target, root) for root in allowed_roots):
            raise HTTPException(
                status_code=400, detail=f"Unsafe workflow file path: {preview.path}"
            )

        current = project_root
        for component in relative_path.parts:
            current /= component
            if current.is_symlink():
                raise HTTPException(
                    status_code=400,
                    detail=f"Symlinked workflow path is not allowed: {preview.path}",
                )
        targets.append((preview, target))
    return targets


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _proposal_from_request(request: WorkflowProposalRequest) -> WorkflowProposal:
    source = request.selection_for("source")
    if source is None:
        raise RuntimeError("Missing source selection in workflow proposal request.")
    source_values = source.values
    domain = _slug(str(source_values.get("domain") or request.domain))
    table_name = _slug(
        str(
            source_values.get("table_name")
            or source_values.get("target_table")
            or request.workflow_name
        )
    )
    unique_key = _slug(
        str(source_values.get("unique_key") or source_values.get("primary_key") or "id")
    )
    fields = _coerce_fields(source_values.get("fields"))
    files: list[WorkflowFilePreview] = []
    selected = [source.contribution_id]
    planned_assets: list[str] = []
    planned_tables = [table_name]
    planned_models: list[str] = []
    warnings: list[str] = []

    if source.contribution_id == "sling.replication-source":
        source_name = str(source_values.get("source_name") or "POSTGRES")
        source_stream = str(source_values.get("source_stream") or table_name)
        replication_mode = str(source_values.get("replication_mode") or "incremental")
        update_key = str(source_values.get("update_key") or "")
        cron = str(source_values.get("schedule") or "0 2 * * *")
        planned_assets.append(f"sling_{table_name}")
        files.extend(
            [
                WorkflowFilePreview(
                    path=f"workflows/ingestion/{domain}/{table_name}_sling.py",
                    content=_render_sling_asset(
                        domain,
                        table_name,
                        unique_key,
                        source_name,
                        source_stream,
                        replication_mode,
                        update_key,
                        cron,
                    ),
                ),
                WorkflowFilePreview(
                    path=f"workflows/ingestion/{domain}/{table_name}_sling.yml",
                    content=_render_sling_replication_config(
                        domain,
                        table_name,
                        unique_key,
                        source_name,
                        source_stream,
                        replication_mode,
                        update_key,
                        cron,
                    ),
                ),
            ]
        )
    elif source.contribution_id == "dlt.rest-api-source":
        api_base_url = str(source_values.get("api_base_url") or "")
        cron = str(source_values.get("cron") or "0 */1 * * *")
        response_path = str(source_values.get("response_path") or "")
        pagination = str(source_values.get("pagination") or "none")
        auth = str(source_values.get("auth") or "none")
        planned_assets.append(f"dlt_{table_name}")
        files.extend(
            [
                WorkflowFilePreview(
                    path=f"workflows/schemas/{domain}.py",
                    content=_render_schema(domain, table_name, unique_key, fields),
                ),
                WorkflowFilePreview(
                    path=f"workflows/ingestion/{domain}/{table_name}.py",
                    content=_render_dlt_asset(
                        domain,
                        table_name,
                        unique_key,
                        api_base_url,
                        cron,
                        response_path,
                        pagination,
                        auth,
                    ),
                ),
                WorkflowFilePreview(
                    path=f"tests/test_{domain}_{table_name}.py",
                    content=_render_ingestion_test(domain, table_name, unique_key),
                ),
            ]
        )
    else:
        warnings.append(
            f"Source contribution {source.contribution_id!r} does not provide proposal rendering."
        )

    for transform in request.selections_for("transform"):
        if not transform.contribution_id:
            continue
        selected.append(transform.contribution_id)
        transform_values = transform.values
        if transform.contribution_id == "dbt.transform":
            planned_models.extend(
                _append_dbt_transform_files(
                    files,
                    request.workflow_name,
                    table_name,
                    unique_key,
                    fields,
                    transform_values,
                )
            )
            continue
        if transform.contribution_id == "dbt.initialize-project":
            project_name = _slug(str(transform_values.get("project_name") or request.workflow_name))
            files.append(
                WorkflowFilePreview(
                    path="workflows/transforms/dbt/dbt_project.yml",
                    content=_render_dbt_project(project_name),
                )
            )
        if transform.contribution_id == "dbt.basic-model":
            model_name = _slug(str(transform_values.get("model_name") or f"stg_{table_name}"))
            source_relation = str(transform_values.get("source_relation") or f"raw.{table_name}")
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_model(source_relation),
                )
            )
        if transform.contribution_id == "dbt.source-yml":
            source_name = _slug(str(transform_values.get("source_name") or "raw"))
            source_table = _slug(str(transform_values.get("table_name") or table_name))
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/sources/{source_name}.yml",
                    content=_render_dbt_source_yml(source_name, source_table, fields),
                )
            )
        if transform.contribution_id == "dbt.schema-tests":
            model_name = _slug(str(transform_values.get("model_name") or f"stg_{table_name}"))
            model_key = _slug(str(transform_values.get("unique_key") or unique_key))
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.yml",
                    content=_render_dbt_schema_tests(model_name, model_key, fields),
                )
            )
        if transform.contribution_id == "dbt.rename-columns":
            model_name = _slug(str(transform_values.get("model_name") or f"renamed_{table_name}"))
            source_relation = str(
                transform_values.get("source_relation") or f"ref('stg_{table_name}')"
            )
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_rename_columns(
                        source_relation,
                        _coerce_fields(transform_values.get("renames")),
                    ),
                )
            )
        if transform.contribution_id == "dbt.cast-columns":
            model_name = _slug(str(transform_values.get("model_name") or f"typed_{table_name}"))
            source_relation = str(
                transform_values.get("source_relation") or f"ref('stg_{table_name}')"
            )
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_cast_columns(
                        source_relation,
                        _coerce_fields(transform_values.get("casts")),
                    ),
                )
            )
        if transform.contribution_id == "dbt.filter-rows":
            model_name = _slug(str(transform_values.get("model_name") or f"filtered_{table_name}"))
            source_relation = str(
                transform_values.get("source_relation") or f"ref('stg_{table_name}')"
            )
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_filter_rows(
                        source_relation,
                        str(transform_values.get("where") or "1 = 1"),
                    ),
                )
            )
        if transform.contribution_id == "dbt.deduplicate":
            model_name = _slug(str(transform_values.get("model_name") or f"deduped_{table_name}"))
            source_relation = str(
                transform_values.get("source_relation") or f"ref('stg_{table_name}')"
            )
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_deduplicate(
                        source_relation,
                        str(transform_values.get("partition_by") or unique_key),
                        str(transform_values.get("order_by") or unique_key),
                    ),
                )
            )
        if transform.contribution_id == "dbt.aggregate":
            model_name = _slug(str(transform_values.get("model_name") or f"{table_name}_summary"))
            source_relation = str(
                transform_values.get("source_relation") or f"ref('stg_{table_name}')"
            )
            planned_models.append(model_name)
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/transforms/dbt/models/{model_name}.sql",
                    content=_render_dbt_aggregate(
                        source_relation,
                        str(transform_values.get("group_by") or unique_key),
                        _coerce_fields(transform_values.get("metrics")),
                    ),
                )
            )

    for quality in request.selections_for("quality"):
        if quality.contribution_id != "pandera.quality-checks":
            continue
        selected.append(quality.contribution_id)
        files.append(
            WorkflowFilePreview(
                path=f"workflows/quality/{domain}/{table_name}_quality.py",
                content=_render_pandera_quality(
                    domain,
                    table_name,
                    unique_key,
                    quality.values,
                ),
            )
        )

    for publish in request.selections_for("publish"):
        if not publish.contribution_id:
            continue
        selected.append(publish.contribution_id)
        if publish.contribution_id == "dagster.orchestration":
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/orchestration/{_slug(request.workflow_name)}.py",
                    content=_render_dagster_orchestration(
                        request.workflow_name,
                        domain,
                        table_name,
                        planned_assets,
                        planned_models,
                        publish.values,
                    ),
                )
            )
        if publish.contribution_id == "openmetadata.catalog":
            files.append(
                WorkflowFilePreview(
                    path=f"workflows/catalog/{domain}/{_slug(request.workflow_name)}.yml",
                    content=_render_openmetadata_catalog(
                        request.workflow_name,
                        domain,
                        table_name,
                        planned_models,
                        publish.values,
                    ),
                )
            )

    disabled_stages: dict[str, str] = {}
    if not request.selections_for("quality"):
        disabled_stages["quality"] = "No quality wizard contribution selected."
    if not request.selections_for("publish"):
        disabled_stages["publish"] = "No publish wizard contribution selected."

    action = WorkflowApplyAction(
        id=f"workflow.apply.{_slug(request.workflow_name)}",
        label="Create workflow files",
        target_files=[file.path for file in files],
        conflict_policy="fail-on-conflict",
        expected_evidence=[f"Created {file.path}" for file in files],
    )
    return WorkflowProposal(
        workflow_name=request.workflow_name,
        domain=domain,
        selected_contributions=selected,
        planned_assets=planned_assets,
        planned_tables=planned_tables,
        planned_models=planned_models,
        files=files,
        warnings=warnings,
        disabled_stages=disabled_stages,
        actions=[action],
    )


def _append_dbt_transform_files(
    files: list[WorkflowFilePreview],
    workflow_name: str,
    table_name: str,
    unique_key: str,
    fields: list[str],
    values: dict[str, Any],
) -> list[str]:
    planned_models: list[str] = []
    project_name = _slug(str(values.get("project_name") or workflow_name))
    source_name = _slug(str(values.get("source_name") or "raw"))
    source_table = _slug(str(values.get("source_table") or table_name))
    staging_model = _slug(str(values.get("staging_model_name") or f"stg_{table_name}"))
    staging_relation = str(values.get("staging_source_relation") or f"{source_name}.{source_table}")

    files.extend(
        [
            WorkflowFilePreview(
                path="workflows/transforms/dbt/dbt_project.yml",
                content=_render_dbt_project(project_name),
            ),
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/sources/{source_name}.yml",
                content=_render_dbt_source_yml(source_name, source_table, fields),
            ),
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/{staging_model}.sql",
                content=_render_dbt_model(staging_relation),
            ),
        ]
    )
    planned_models.append(staging_model)
    upstream_relation = f"ref('{staging_model}')"

    if str(values.get("enable_rename") or "no") == "yes" and values.get("renames"):
        model_name = _slug(str(values.get("rename_model_name") or f"renamed_{table_name}"))
        files.append(
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/{model_name}.sql",
                content=_render_dbt_rename_columns(
                    upstream_relation,
                    _coerce_fields(values.get("renames")),
                ),
            )
        )
        planned_models.append(model_name)
        upstream_relation = f"ref('{model_name}')"

    if str(values.get("enable_cast") or "no") == "yes" and values.get("casts"):
        model_name = _slug(str(values.get("cast_model_name") or f"typed_{table_name}"))
        files.append(
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/{model_name}.sql",
                content=_render_dbt_cast_columns(
                    upstream_relation,
                    _coerce_fields(values.get("casts")),
                ),
            )
        )
        planned_models.append(model_name)
        upstream_relation = f"ref('{model_name}')"

    if values.get("where"):
        model_name = _slug(str(values.get("filter_model_name") or f"filtered_{table_name}"))
        files.append(
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/{model_name}.sql",
                content=_render_dbt_filter_rows(
                    upstream_relation,
                    str(values.get("where") or "1 = 1"),
                ),
            )
        )
        planned_models.append(model_name)
        upstream_relation = f"ref('{model_name}')"

    dedupe_model = _slug(str(values.get("dedupe_model_name") or f"clean_{table_name}"))
    files.append(
        WorkflowFilePreview(
            path=f"workflows/transforms/dbt/models/{dedupe_model}.sql",
            content=_render_dbt_deduplicate(
                upstream_relation,
                str(values.get("partition_by") or unique_key),
                str(values.get("order_by") or unique_key),
            ),
        )
    )
    planned_models.append(dedupe_model)
    upstream_relation = f"ref('{dedupe_model}')"

    if str(values.get("enable_aggregate") or "no") == "yes":
        model_name = _slug(str(values.get("aggregate_model_name") or f"{table_name}_summary"))
        files.append(
            WorkflowFilePreview(
                path=f"workflows/transforms/dbt/models/{model_name}.sql",
                content=_render_dbt_aggregate(
                    upstream_relation,
                    str(values.get("group_by") or unique_key),
                    _coerce_fields(values.get("metrics")),
                ),
            )
        )
        planned_models.append(model_name)

    test_model = _slug(str(values.get("test_model_name") or dedupe_model))
    files.append(
        WorkflowFilePreview(
            path=f"workflows/transforms/dbt/models/{test_model}.yml",
            content=_render_dbt_schema_tests(
                test_model,
                _slug(str(values.get("unique_key") or unique_key)),
                fields,
            ),
        )
    )
    return planned_models


def _with_conflict_action_disabled(
    proposal: WorkflowProposal,
    conflicts: list[str],
) -> WorkflowProposal:
    action = WorkflowApplyAction(
        id=proposal.actions[0].id,
        label=proposal.actions[0].label,
        target_files=proposal.actions[0].target_files,
        conflict_policy=proposal.actions[0].conflict_policy,
        enabled=False,
        reason=f"File conflicts: {', '.join(conflicts)}",
        expected_evidence=proposal.actions[0].expected_evidence,
    )
    return WorkflowProposal(
        workflow_name=proposal.workflow_name,
        domain=proposal.domain,
        selected_contributions=proposal.selected_contributions,
        planned_assets=proposal.planned_assets,
        planned_tables=proposal.planned_tables,
        planned_models=proposal.planned_models,
        files=proposal.files,
        warnings=[*proposal.warnings, f"File conflicts: {', '.join(conflicts)}"],
        missing_capabilities=proposal.missing_capabilities,
        disabled_stages=proposal.disabled_stages,
        actions=[action],
    )


def _proposal_from_payload(payload: dict[str, Any]) -> WorkflowProposal:
    return WorkflowProposal(
        workflow_name=str(payload.get("workflow_name") or ""),
        domain=str(payload.get("domain") or ""),
        selected_contributions=list(payload.get("selected_contributions") or []),
        planned_assets=list(payload.get("planned_assets") or []),
        planned_tables=list(payload.get("planned_tables") or []),
        planned_models=list(payload.get("planned_models") or []),
        files=[
            WorkflowFilePreview(
                path=str(item["path"]),
                content=str(item.get("content") or ""),
                mode=item.get("mode", "create"),
            )
            for item in payload.get("files", [])
        ],
        warnings=list(payload.get("warnings") or []),
        missing_capabilities=list(payload.get("missing_capabilities") or []),
        disabled_stages=dict(payload.get("disabled_stages") or {}),
        actions=[
            WorkflowApplyAction(
                id=str(item["id"]),
                label=str(item["label"]),
                target_files=list(item.get("target_files") or []),
                conflict_policy=item.get("conflict_policy", "fail-on-conflict"),
                enabled=bool(item.get("enabled", True)),
                reason=item.get("reason"),
                expected_evidence=list(item.get("expected_evidence") or []),
            )
            for item in payload.get("actions", [])
        ],
    )


def _slug(value: str) -> str:
    lowered = value.strip().lower().replace("-", "_")
    slugged = re.sub(r"[^a-z0-9_]+", "_", lowered).strip("_")
    return slugged or "workflow"


def _python_string(value: Any) -> str:
    """Render caller-provided text as a Python string literal."""

    return repr(str(value))


def _integer_literal(value: Any, default: int) -> str:
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return str(default)


def _number_literal(value: Any, default: float) -> str:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        number = default
    return repr(number)


def _safe_doc(value: Any) -> str:
    return str(value).replace('"""', "'''")


def _class_name(table_name: str) -> str:
    return "Raw" + "".join(part.capitalize() for part in table_name.split("_"))


def _coerce_fields(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _render_schema(domain: str, table_name: str, unique_key: str, fields: list[str]) -> str:
    class_name = _class_name(table_name)
    field_lines = [f"    {unique_key}: Series[str] = pa.Field(nullable=False)"]
    for item in fields:
        name, _, raw_type = item.partition(":")
        if not name:
            continue
        type_name = raw_type.rstrip("?!") or "str"
        py_type = {"float": "float", "int": "int", "datetime": "str"}.get(type_name, "str")
        nullable = "True" if raw_type.endswith("?") else "False"
        field_lines.append(f"    {_slug(name)}: Series[{py_type}] = pa.Field(nullable={nullable})")
    return (
        f'"""Generated schema for {domain}.{table_name}."""\n\n'
        "import pandera.pandas as pa\n"
        "from pandera.typing import Series\n\n\n"
        f"class {class_name}(pa.DataFrameModel):\n"
        + "\n".join(field_lines)
        + "\n\n    class Config:\n        strict = True\n        coerce = True\n"
    )


def _render_dlt_asset(
    domain: str,
    table_name: str,
    unique_key: str,
    api_base_url: str,
    cron: str,
    response_path: str,
    pagination: str,
    auth: str,
) -> str:
    class_name = _class_name(table_name)
    response_path_line = (
        f', "data_selector": {_python_string(response_path)}' if response_path else ""
    )
    pagination_line = ""
    if pagination == "offset-limit":
        pagination_line = ', "paginator": {"type": "offset", "limit": 100}'
    elif pagination == "page-number":
        pagination_line = ', "paginator": {"type": "page_number", "base_page": 1}'
    auth_lines = ""
    if auth == "bearer-token":
        auth_lines = '\n    headers = {"Authorization": "Bearer ${TOKEN}"}'
    elif auth == "api-key-header":
        auth_lines = '\n    headers = {"X-API-Key": "${API_KEY}"}'
    else:
        auth_lines = "\n    headers = {}"
    phlo_dlt_import = "from phlo_dlt import phlo_ingestion"
    return f'''"""Generated DLT ingestion asset for {_safe_doc(domain)}.{_safe_doc(table_name)}."""

from dlt.sources.rest_api import rest_api
{phlo_dlt_import}

from workflows.schemas.{domain} import {class_name}


@phlo_ingestion(
    table_name="{table_name}",
    unique_key="{unique_key}",
    validation_schema={class_name},
    group="{domain}",
    cron={_python_string(cron)},
    freshness_hours=(1, 24),
)
def {table_name}(partition_date: str):
    base_url = {_python_string(api_base_url)}
    if not base_url:
        raise RuntimeError("Missing API base URL. Configure base_url before materializing.")
{auth_lines}

    return rest_api(
        client={{"base_url": base_url, "headers": headers}},
        resources=[{{"name": "{table_name}", "endpoint": {{"path": ""{response_path_line}{pagination_line}}}}}],
    )
'''


def _render_ingestion_test(domain: str, table_name: str, unique_key: str) -> str:
    class_name = _class_name(table_name)
    return f'''"""Generated tests for {domain}.{table_name}."""

from workflows.schemas.{domain} import {class_name}


def test_schema_contains_unique_key() -> None:
    assert "{unique_key}" in {class_name}.to_schema().columns
'''


def _render_sling_asset(
    domain: str,
    table_name: str,
    primary_key: str,
    source_name: str,
    source_stream: str,
    replication_mode: str,
    update_key: str,
    cron: str,
) -> str:
    resolved_update_key = update_key or primary_key
    phlo_sling_import = "from phlo_sling import phlo_sling_replication"
    return f'''"""Generated Sling replication asset for {_safe_doc(domain)}.{_safe_doc(table_name)}."""

{phlo_sling_import}


@phlo_sling_replication(
    stream_name={_python_string(source_stream)},
    table_name={_python_string(table_name)},
    source_conn={_python_string(source_name)},
    group="{domain}",
    mode={_python_string(replication_mode)},
    primary_key={_python_string(primary_key)},
    update_key={_python_string(resolved_update_key)},
    cron={_python_string(cron)},
    freshness_hours=(4, 24),
)
def replicate_{table_name}(context):
    return None
'''


def _render_sling_replication_config(
    domain: str,
    table_name: str,
    primary_key: str,
    source_name: str,
    source_stream: str,
    replication_mode: str,
    update_key: str,
    cron: str,
) -> str:
    update_key_line = f"\nupdate_key: {update_key or primary_key}"
    return f"""name: {domain}_{table_name}_replication
provider: phlo-sling
schedule: "{cron}"
source:
  connection: {source_name}
  stream: {source_stream}
target:
  table: {table_name}
replication:
  mode: {replication_mode}
  primary_key: {primary_key}{update_key_line}
"""


def _render_pandera_quality(
    domain: str,
    table_name: str,
    unique_key: str,
    values: dict[str, Any],
) -> str:
    target_table = str(values.get("target_table") or f"{domain}.{table_name}")
    check_name = _slug(str(values.get("check_name") or f"{table_name}_quality"))
    null_columns = _coerce_fields(values.get("not_null_columns")) or [unique_key]
    range_checks = _coerce_fields(values.get("range_checks"))
    freshness_column = str(values.get("freshness_column") or "").strip()
    freshness_hours = str(values.get("freshness_hours") or "24").strip() or "24"
    min_rows = str(values.get("min_rows") or "1").strip() or "1"

    check_lines = [
        f"        UniqueCheck(columns=[{_python_string(unique_key)}]),",
        f"        NullCheck(columns={null_columns!r}),",
        f"        CountCheck(min_rows={_integer_literal(min_rows, 1)}),",
    ]
    for item in range_checks:
        column, _, bounds = item.partition(":")
        minimum, _, maximum = bounds.partition(":")
        if column.strip() and minimum.strip() and maximum.strip():
            check_lines.append(
                "        "
                f"RangeCheck(column={_python_string(_slug(column))}, "
                f"min_value={_number_literal(minimum, 0)}, "
                f"max_value={_number_literal(maximum, 0)}),"
            )
    if freshness_column:
        check_lines.append(
            "        "
            f"FreshnessCheck(timestamp_column={_python_string(_slug(freshness_column))}, "
            f"max_age_hours={_number_literal(freshness_hours, 24)}),"
        )
    checks = "\n".join(check_lines)
    phlo_pandera_import = """from phlo_pandera import (
    CountCheck,
    FreshnessCheck,
    NullCheck,
    RangeCheck,
    UniqueCheck,
    phlo_pandera,
)"""
    return f'''"""Generated Pandera quality checks for {_safe_doc(target_table)}."""

{phlo_pandera_import}


@phlo_pandera(
    table={_python_string(target_table)},
    group="{domain}",
    checks=[
{checks}
    ],
)
def {check_name}():
    pass
'''


def _render_dagster_orchestration(
    workflow_name: str,
    domain: str,
    table_name: str,
    planned_assets: list[str],
    planned_models: list[str],
    values: dict[str, Any],
) -> str:
    job_name = _slug(str(values.get("job_name") or f"{workflow_name}_job"))
    asset_group = _slug(str(values.get("asset_group") or domain))
    schedule = str(values.get("schedule") or "0 2 * * *")
    include_sensor = str(values.get("include_sensor") or "no") == "yes"
    asset_list = ", ".join(_python_string(item) for item in planned_assets)
    model_list = ", ".join(_python_string(item) for item in planned_models)
    sensor = ""
    sensor_names = ""
    if include_sensor:
        sensor = f"""

@dg.sensor(job={job_name})
def {_slug(workflow_name)}_external_sensor(context):
    return dg.SkipReason("Connect this sensor to an external event source.")
"""
        sensor_names = f", sensors=[{_slug(workflow_name)}_external_sensor]"
    return f'''"""Generated Dagster orchestration scaffold for {_safe_doc(workflow_name)}."""

import dagster as dg

WORKFLOW_ASSETS = [{asset_list}]
WORKFLOW_MODELS = [{model_list}]
ASSET_GROUP = {_python_string(asset_group)}
TARGET_TABLE = {_python_string(table_name)}

{job_name} = dg.define_asset_job(
    name="{job_name}",
    selection=dg.AssetSelection.groups(ASSET_GROUP),
)

{_slug(workflow_name)}_schedule = dg.ScheduleDefinition(
    job={job_name},
    cron_schedule={_python_string(schedule)},
)
{sensor}

defs = dg.Definitions(
    jobs=[{job_name}],
    schedules=[{_slug(workflow_name)}_schedule]{sensor_names},
)
'''


def _render_openmetadata_catalog(
    workflow_name: str,
    domain: str,
    table_name: str,
    planned_models: list[str],
    values: dict[str, Any],
) -> str:
    tags = _coerce_fields(values.get("tags"))
    tag_lines = "\n".join(f"  - {tag}" for tag in tags) or "  - generated"
    model_lines = "\n".join(f"  - {model}" for model in planned_models) or "  - none"
    description = str(
        values.get("description") or f"Generated catalog metadata for the {workflow_name} workflow."
    )
    return f"""workflow: {workflow_name}
provider: phlo-openmetadata
service: {values.get("service_name") or "phlo"}
database: {values.get("database") or "warehouse"}
schema: {values.get("schema") or domain}
owner: {values.get("owner") or "data-platform"}
description: "{description}"
tables:
  - name: {table_name}
    domain: {domain}
    generated_models:
{model_lines}
tags:
{tag_lines}
"""


def _render_dbt_project(project_name: str) -> str:
    return f"""name: {project_name}
version: 1.0.0
config-version: 2
profile: phlo
model-paths: ["models"]
models:
  {project_name}:
    +materialized: table
"""


def _render_dbt_model(source_relation: str) -> str:
    return f"""select *
from {source_relation}
"""


def _render_dbt_rename_columns(source_relation: str, renames: list[str]) -> str:
    expressions = []
    for item in renames:
        source, _, target = item.partition(":")
        if source.strip() and target.strip():
            expressions.append(f"    {source.strip()} as {_slug(target)}")
    if not expressions:
        expressions.append("    *")
    select_lines = ",\n".join(expressions)
    return f"""select
{select_lines}
from {source_relation}
"""


def _render_dbt_cast_columns(source_relation: str, casts: list[str]) -> str:
    expressions = []
    for item in casts:
        column, _, target_type = item.partition(":")
        if column.strip() and target_type.strip():
            expressions.append(
                f"    cast({column.strip()} as {target_type.strip()}) as {_slug(column)}"
            )
    if not expressions:
        expressions.append("    *")
    select_lines = ",\n".join(expressions)
    return f"""select
{select_lines}
from {source_relation}
"""


def _render_dbt_filter_rows(source_relation: str, where_clause: str) -> str:
    return f"""select *
from {source_relation}
where {where_clause.strip() or "1 = 1"}
"""


def _render_dbt_deduplicate(source_relation: str, partition_by: str, order_by: str) -> str:
    return f"""with ranked as (
    select
        *,
        row_number() over (
            partition by {partition_by}
            order by {order_by} desc
        ) as phlo_row_number
    from {source_relation}
)

select * exclude (phlo_row_number)
from ranked
where phlo_row_number = 1
"""


def _render_dbt_aggregate(source_relation: str, group_by: str, metrics: list[str]) -> str:
    group_columns = [column.strip() for column in group_by.split(",") if column.strip()]
    metric_expressions = []
    for item in metrics:
        name, _, expression = item.partition(":")
        if name.strip() and expression.strip():
            metric_expressions.append(f"    {expression.strip()} as {_slug(name)}")
    select_lines = [f"    {column}" for column in group_columns] + metric_expressions
    if not select_lines:
        select_lines = ["    count(*) as row_count"]
    group_line = f"\ngroup by {', '.join(group_columns)}" if group_columns else ""
    select_sql = ",\n".join(select_lines)
    return f"""select
{select_sql}
from {source_relation}{group_line}
"""


def _render_dbt_source_yml(source_name: str, table_name: str, fields: list[str]) -> str:
    columns = "\n".join(
        f"      - name: {_slug(item.partition(':')[0])}"
        for item in fields
        if item.partition(":")[0]
    )
    if not columns:
        columns = "      - name: id"
    return f"""version: 2

sources:
  - name: {source_name}
    schema: raw
    tables:
      - name: {table_name}
        columns:
{columns}
"""


def _render_dbt_schema_tests(model_name: str, unique_key: str, fields: list[str]) -> str:
    extra_columns = "\n".join(
        f"      - name: {_slug(item.partition(':')[0])}"
        for item in fields
        if item.partition(":")[0]
    )
    return f"""version: 2

models:
  - name: {model_name}
    description: Staging model generated by the Phlo workflow wizard.
    columns:
      - name: {unique_key}
        tests:
          - not_null
          - unique
{extra_columns}
"""
