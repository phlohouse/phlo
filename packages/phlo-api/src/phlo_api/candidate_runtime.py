"""Durable ownership and pinned readback for isolated, never-promoted candidates."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Literal
from urllib.parse import quote
import re

from pydantic import TypeAdapter, ValidationError
from phlo.config_schema import EnvironmentId

from phlo_api.api.operation_controls import project_root
from phlo_api.errors import BackendUnavailableError, ConflictError
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.v1_contract import Environment, WireModel

_COLLECTION = "v1_owned_candidates"
_ID = re.compile(r"^[a-f0-9]{32}$")
_ENVIRONMENT = TypeAdapter(EnvironmentId)
_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CandidateEvidence(WireModel):
    stale: bool
    eligible_for_release: bool
    reason: str | None
    base_hash: str | None
    current_base_hash: str | None
    candidate_hash: str | None
    observed_candidate_hash: str | None


class CandidateReconciliation(WireModel):
    resolution: Literal["verified_success", "unresolved"]
    reason: str | None


class CandidateHandoffFile(WireModel):
    path: str
    content: str
    sha256: str


class CandidateReleaseHandoff(WireModel):
    source_ref: str | None
    target_ref: str
    checks_url: str | None
    trial_merge_url: str | None
    merge_url: str | None
    requires_signature: Literal[True]
    blocked_reason: str | None


class CandidateHandoff(WireModel):
    env: Environment
    kind: Literal["csv_candidate", "transform_revision"]
    source_review_required: Literal[True]
    automatic_promotion: Literal[False]
    files: list[CandidateHandoffFile]
    candidate_evidence: CandidateEvidence | None
    release: CandidateReleaseHandoff


def _provider():
    try:
        return import_module("phlo_trino.candidates")
    except ImportError as exc:
        raise BackendUnavailableError("The candidate query provider is not installed.") from exc


def preflight_candidate() -> None:
    """Check local provider/configuration prerequisites without reserving or writing a candidate."""
    try:
        import_module("phlo_iceberg.catalog")
        _provider().catalog_properties(project_root())
    except (ImportError, OSError, ValueError) as exc:
        raise BackendUnavailableError("Candidate provider configuration is unavailable.") from exc


async def _nessie(method: str, path: str, **kwargs: Any) -> Any:
    from phlo_api.api.v1_branch_workflows import _nessie as request

    return await request(method, path, **kwargs)


async def _reference(ref: str) -> dict[str, Any]:
    response = await _nessie("GET", f"/api/v1/trees/tree/{quote(ref, safe='')}")
    if (
        not isinstance(response, dict)
        or response.get("name") != ref
        or response.get("type") != "BRANCH"
    ):
        raise ConflictError("The candidate reference is not the expected branch.")
    return response


def _records() -> list[dict[str, Any]]:
    root = project_root()
    return load_collection(root, _COLLECTION, root / ".phlo" / "candidate-bindings.json")


def _mutate(change):
    root = project_root()
    return mutate_collection(root, _COLLECTION, root / ".phlo" / "candidate-bindings.json", change)


def _stored(binding: dict[str, Any]) -> dict[str, Any]:
    record = next(
        (item for item in _records() if item.get("execution_id") == binding.get("execution_id")),
        None,
    )
    if (
        record is None
        or record.get("state") != "ready"
        or any(
            record.get(key) != binding.get(key)
            for key in (
                "actor",
                "env",
                "candidate_ref",
                "base_ref",
                "base_hash",
                "catalog",
                "catalog_properties_digest",
            )
        )
    ):
        raise ConflictError("Candidate ownership or binding is unavailable.")
    return record


def _state(identity: str, **changes: Any) -> dict[str, Any]:
    records = _mutate(
        lambda items: [
            {**item, **changes} if item["execution_id"] == identity else item for item in items
        ]
    )
    return next(item for item in records if item["execution_id"] == identity)


async def provision_candidate(
    *, env: str, actor: str, kind: str, operation_id: str, base_ref: str, base_hash: str
) -> dict[str, Any]:
    """Reserve ownership before IO; replay ready records, never adopt unknown outcomes."""
    try:
        _ENVIRONMENT.validate_python(env)
    except ValidationError as exc:
        raise ConflictError("Invalid candidate operation identity.") from exc
    if kind not in {"upload", "transform"} or not _ID.fullmatch(operation_id):
        raise ConflictError("Invalid candidate operation identity.")
    existing = next((item for item in _records() if item.get("execution_id") == operation_id), None)
    expected = {
        "actor": actor,
        "env": env,
        "kind": kind,
        "base_ref": base_ref,
        "base_hash": base_hash,
    }
    if existing is not None:
        if (
            any(existing.get(key) != value for key, value in expected.items())
            or existing.get("state") != "ready"
        ):
            raise ConflictError(
                "Candidate preparation needs reconciliation; it cannot be replayed."
            )
        await verify_candidate(existing, expected_hash=base_hash)
        return existing
    source = await _reference(base_ref)
    if source.get("hash") != base_hash:
        raise ConflictError("The base branch changed before candidate preparation.")
    ref = f"{env}-{kind}-{operation_id}"
    record = {
        **expected,
        "execution_id": operation_id,
        "candidate_ref": ref,
        "ref": ref,
        "candidate_hash": base_hash,
        "state": "preparing",
        "created_at": datetime.now(UTC).isoformat(),
    }

    def reserve(items):
        if len(items) >= 1000 or any(item.get("execution_id") == operation_id for item in items):
            raise ConflictError("Candidate ownership cannot be reserved.")
        return [*items, record]

    _mutate(reserve)
    try:
        created = await _nessie(
            "POST",
            "/api/v2/trees",
            params={"name": ref, "type": "branch"},
            body={"type": "BRANCH", "name": base_ref, "hash": base_hash},
        )
        created = created.get("reference", created) if isinstance(created, dict) else {}
        if created.get("name") != ref or created.get("hash") != base_hash:
            raise ConflictError("Nessie did not attest the requested candidate fork.")
        catalog = await asyncio.to_thread(
            _provider().create_catalog, project_root(), operation_id, ref
        )
        binding = _state(
            operation_id, **catalog, candidate_catalog=catalog["catalog"], state="ready"
        )
        await verify_candidate(binding, expected_hash=base_hash)
        return binding
    except Exception:
        _state(operation_id, state="unknown")
        raise


def candidate_connection(binding: dict[str, Any]) -> dict[str, str]:
    _stored(binding)
    return _provider().connection(project_root())


async def verify_candidate(
    binding: dict[str, Any],
    *,
    expected_hash: str | None = None,
    require_current_base: bool = True,
) -> str:
    record = _stored(binding)
    try:
        await asyncio.to_thread(
            _provider().verify_catalog,
            project_root(),
            record["execution_id"],
            record["candidate_ref"],
            record["catalog"],
            record["catalog_properties_digest"],
        )
    except Exception as exc:
        raise ConflictError("The candidate catalog binding could not be verified.") from exc
    reference, base = await asyncio.gather(
        _reference(record["candidate_ref"]), _reference(record["base_ref"])
    )
    head = reference.get("hash")
    if (
        not isinstance(head, str)
        or not head
        or (expected_hash is not None and head != expected_hash)
    ):
        raise ConflictError("The candidate branch changed from the expected revision.")
    if require_current_base and base.get("hash") != record["base_hash"]:
        raise ConflictError("The original base branch changed; candidate evidence needs review.")
    return head


async def candidate_evidence(
    binding: dict[str, Any], *, expected_hash: str | None
) -> dict[str, Any]:
    """Describe live eligibility without hiding retained evidence when providers drift."""
    evidence = {
        "stale": True,
        "eligible_for_release": False,
        "reason": "Candidate binding or provider evidence is unavailable.",
        "base_hash": binding.get("base_hash"),
        "current_base_hash": None,
        "candidate_hash": expected_hash,
        "observed_candidate_hash": None,
    }
    try:
        _stored(binding)
        base = await _reference(binding["base_ref"])
        evidence["current_base_hash"] = base.get("hash")
        evidence["observed_candidate_hash"] = await verify_candidate(
            binding, expected_hash=expected_hash, require_current_base=False
        )
    except Exception:
        return evidence
    stale = base.get("hash") != binding["base_hash"]
    evidence.update(
        stale=stale,
        eligible_for_release=not stale and expected_hash is not None,
        reason="The original base changed; review this historical result only." if stale else None,
    )
    return evidence


async def reconcile_candidate(*, operation_id: str, actor: str, env: str) -> dict[str, Any]:
    """Recover only a fully recorded catalog binding, without provisioning or adopting one."""
    record = next(
        (
            item
            for item in _records()
            if item.get("execution_id") == operation_id
            and item.get("actor") == actor
            and item.get("env") == env
        ),
        None,
    )
    if record is None or not record.get("catalog_properties_digest") or not record.get("catalog"):
        raise ConflictError(
            "No complete owned catalog evidence exists; the operation stays fenced."
        )
    await asyncio.to_thread(
        _provider().verify_catalog,
        project_root(),
        operation_id,
        record["candidate_ref"],
        record["catalog"],
        record["catalog_properties_digest"],
    )
    reference = await _reference(record["candidate_ref"])
    if record["state"] != "ready":
        if reference.get("hash") != record["base_hash"]:
            raise ConflictError("An uncertain preparation has subsequent writes; it stays fenced.")

        def resolve(items):
            current = next(item for item in items if item.get("execution_id") == operation_id)
            if current != record:
                raise ConflictError("Candidate evidence changed during reconciliation.")
            return [{**item, "state": "ready"} if item is current else item for item in items]

        _mutate(resolve)
    return {**record, "state": "ready"}


def release_handoff(
    *, env: str, source_ref: str | None, target_ref: str, evidence: dict[str, Any] | None
) -> dict[str, Any]:
    """Point to existing checked and signed governance, never imply source publication."""
    prefix = f"/api/v1/branches/{quote(source_ref, safe='')}" if source_ref else None
    return {
        "source_ref": source_ref,
        "target_ref": target_ref,
        "checks_url": f"{prefix}/checks?env={env}" if prefix else None,
        "trial_merge_url": f"{prefix}/trial-merge?env={env}" if prefix else None,
        "merge_url": f"{prefix}/merge?env={env}" if prefix else None,
        "requires_signature": True,
        "blocked_reason": (evidence or {}).get("reason")
        or "Review and commit the exported source before separate checked, signed release.",
    }


def _table_observation(ref: str, relation: str, content: dict[str, Any]) -> dict[str, Any]:
    from phlo_iceberg.catalog import get_catalog

    table = get_catalog(ref=ref).load_table(tuple(relation.split(".")))
    snapshot = table.current_snapshot()
    if (
        table.metadata_location != content.get("metadataLocation")
        or snapshot is None
        or snapshot.snapshot_id != content.get("snapshotId")
    ):
        raise ConflictError("Candidate table metadata no longer matches the pinned reference.")
    summary = snapshot.summary.additional_properties if snapshot.summary else {}
    count = summary.get("total-records")
    return {
        "snapshot_id": str(snapshot.snapshot_id),
        "row_count": int(count) if count is not None else None,
    }


async def preview_candidate(
    binding: dict[str, Any],
    relation: str,
    *,
    expected_hash: str | None,
    expected_snapshot_id: int | str | None = None,
    limit: int = 25,
    require_current_base: bool = True,
) -> dict[str, Any]:
    record = _stored(binding)
    parts = relation.split(".")
    if len(parts) == 3 and parts[0] == record["catalog"]:
        parts = parts[1:]
    if len(parts) != 2 or not all(_PART.fullmatch(part) for part in parts) or not 1 <= limit <= 100:
        raise ConflictError("Candidate preview requires its owned bounded table relation.")
    relation = ".".join(parts)
    head = await verify_candidate(
        binding, expected_hash=expected_hash, require_current_base=require_current_base
    )
    from phlo_api.branch_schema_resolution import _content

    content = await _content(_nessie, record["candidate_ref"], head, relation)
    if content.get("type") != "ICEBERG_TABLE":
        raise ConflictError("The candidate result is not an Iceberg table.")
    observation = await asyncio.to_thread(
        _table_observation, record["candidate_ref"], relation, content
    )
    if expected_snapshot_id is not None and str(observation["snapshot_id"]) != str(
        expected_snapshot_id
    ):
        raise ConflictError("The candidate result snapshot changed.")
    result = await asyncio.to_thread(
        _provider().preview, project_root(), record["catalog"], relation, limit
    )
    await verify_candidate(binding, expected_hash=head, require_current_base=require_current_base)
    return {
        **result,
        **observation,
        "ref_hash": head,
        "candidate_ref": record["candidate_ref"],
        "relation": relation,
    }
